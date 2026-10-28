// The host: a C program for the 6502, built with llvm-mos for its `sim`
// machine, which run.py and mksql.py also are (putchar at $FFF9, exit at
// $FFF8).
//
// Roc has no 16-bit target. The app is compiled for wasm32 and its LLVM IR
// retargeted to the 6502, so the app speaks Roc's 32-bit ABI: every usize
// is 32 bits, and a pointer stored in one of Roc's words fills its low 16
// bits (little-endian, so a 16-bit load at the word's address reads it).
// The host speaks that ABI too: `roc_usize` is 32 bits, and pointers cross
// it through `to_word`/`from_word`.

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint32_t roc_usize;

static roc_usize to_word(const void *ptr) { return (roc_usize)(uintptr_t)ptr; }
static uint8_t *from_word(roc_usize word) { return (uint8_t *)(uintptr_t)word; }

// Roc's Str: `bytes, capacity_or_alloc_ptr, length`, 12 bytes at 32 bits.
// A small string (under 12 bytes) keeps its bytes inline and its length in
// the last byte, top bit set.
typedef struct {
    roc_usize bytes;
    roc_usize capacity_or_alloc_ptr;
    roc_usize length;
} RocStr;

_Static_assert(sizeof(RocStr) == 12, "RocStr must match wasm32's layout");

enum { small_str_flag = 0x80 };

static int str_is_small(const RocStr *str) {
    return (((const uint8_t *)str)[sizeof(RocStr) - 1] & small_str_flag) != 0;
}

static void crash(const char *message) {
    fputs(message, stdout);
    putchar('\n');
    exit(1);
}

// A heap allocation as Roc lays it out: a refcount word, then the data.
static uint8_t *alloc_with_refcount(roc_usize data_bytes) {
    uint8_t *start = malloc(sizeof(roc_usize) + data_bytes);
    if (start == NULL) crash("roc: out of memory");
    roc_usize refcount_one = 1;
    memcpy(start, &refcount_one, sizeof(roc_usize));
    return start + sizeof(roc_usize);
}

static roc_usize refcount_of(const uint8_t *data) {
    roc_usize refcount;
    memcpy(&refcount, data - sizeof(roc_usize), sizeof(roc_usize));
    return refcount;
}

// Release one reference to a heap allocation. Refcount 0 is static data,
// never freed.
static void data_decref(uint8_t *data) {
    if (data == NULL) return;
    roc_usize refcount = refcount_of(data);
    if (refcount == 0) return;
    if (refcount == 1) {
        free(data - sizeof(roc_usize));
        return;
    }
    refcount -= 1;
    memcpy(data - sizeof(roc_usize), &refcount, sizeof(roc_usize));
}

static void str_decref(RocStr *str) {
    if (str_is_small(str)) return;
    data_decref(from_word(str->bytes));
}

// Roc's builtin, as src/builtins/dev_wrappers.zig writes it: a small
// string inline, a longer one copied to the heap with refcount 1.
void roc_builtins_str_from_literal(RocStr *out, const uint8_t *bytes, roc_usize len) {
    if (len < sizeof(RocStr)) {
        uint8_t small[sizeof(RocStr)] = {0};
        memcpy(small, bytes, len);
        small[sizeof(RocStr) - 1] = (uint8_t)(len | small_str_flag);
        memcpy(out, small, sizeof(RocStr));
        return;
    }
    uint8_t *data = alloc_with_refcount(len);
    memcpy(data, bytes, len);
    out->bytes = to_word(data);
    out->capacity_or_alloc_ptr = len << 1;
    out->length = len;
}

// Roc's List: `bytes, length, capacity_or_alloc_ptr`, 12 bytes at 32 bits.
// The capacity is stored shifted left one; a set low bit marks a seamless
// slice, whose word is then its allocation's pointer instead.
typedef struct {
    roc_usize bytes;
    roc_usize length;
    roc_usize capacity_or_alloc_ptr;
} RocList;

_Static_assert(sizeof(RocList) == 12, "RocList must match wasm32's layout");

enum { seamless_slice_tag = 1, update_mode_in_place = 1 };

static int list_is_slice(const RocList *list) {
    return (list->capacity_or_alloc_ptr & seamless_slice_tag) != 0;
}

static roc_usize list_capacity(const RocList *list) {
    return list_is_slice(list) ? list->length : list->capacity_or_alloc_ptr >> 1;
}

static uint8_t *list_allocation(const RocList *list) {
    if (list_is_slice(list)) return from_word(list->capacity_or_alloc_ptr & ~(roc_usize)seamless_slice_tag);
    return from_word(list->bytes);
}

// As list.zig's isUnique: a list with no capacity owns nothing, so it is
// unique; otherwise the refcount says.
static int list_is_unique(const RocList *list) {
    if (list_capacity(list) == 0 && !list_is_slice(list)) return 1;
    return refcount_of(list_allocation(list)) == 1;
}

// The host implements Roc's list builtins only for elements that hold no
// references (numbers, records of numbers): all this program needs.
static void list_require_plain(int elements_refcounted) {
    if (elements_refcounted) crash("roc: list of refcounted elements");
}

void roc_builtins_list_with_capacity(RocList *out, uint64_t capacity, uint32_t alignment,
                                     roc_usize element_width, _Bool elements_refcounted) {
    list_require_plain(elements_refcounted);
    if (alignment > sizeof(roc_usize)) crash("roc: list alignment over 4");
    out->bytes = 0;
    out->length = 0;
    out->capacity_or_alloc_ptr = 0;
    if (capacity == 0) return;
    if (capacity * element_width > 0x8000) crash("roc: list too big");
    out->bytes = to_word(alloc_with_refcount((roc_usize)capacity * element_width));
    out->capacity_or_alloc_ptr = (roc_usize)capacity << 1;
}

// Elements appendable in place: spare capacity of a unique, non-slice list.
uint64_t roc_builtins_list_slack_unique(uint8_t *bytes, roc_usize length, roc_usize capacity_or_alloc_ptr) {
    RocList list = {to_word(bytes), length, capacity_or_alloc_ptr};
    if (list_is_slice(&list) || !list_is_unique(&list)) return 0;
    return list_capacity(&list) - list.length;
}

// Append `count` elements copied from the list itself starting at `start`.
// The ranges may overlap (List.repeat doubles its run): copy forward, byte
// by byte, so every byte read was already written.
void roc_builtins_list_append_range_within_unsafe(RocList *out, uint8_t *bytes, roc_usize length,
                                                  roc_usize capacity_or_alloc_ptr, uint64_t start,
                                                  uint64_t count, roc_usize element_width,
                                                  _Bool elements_refcounted, void *element_incref) {
    (void)element_incref;
    list_require_plain(elements_refcounted);
    uint8_t *src = bytes + (roc_usize)start * element_width;
    uint8_t *dst = bytes + length * element_width;
    for (roc_usize i = 0; i < (roc_usize)count * element_width; i++) dst[i] = src[i];
    out->bytes = to_word(bytes);
    out->length = length + (roc_usize)count;
    out->capacity_or_alloc_ptr = capacity_or_alloc_ptr;
}

// List.set: in place when Roc proved the list unique or it is, else on a copy.
void roc_builtins_list_set(RocList *out, uint8_t *bytes, roc_usize length, roc_usize capacity_or_alloc_ptr,
                           uint32_t alignment, uint64_t index, const uint8_t *element,
                           roc_usize element_width, _Bool elements_refcounted, void *element_incref,
                           void *element_decref, uint8_t update_mode) {
    (void)alignment, (void)element_incref, (void)element_decref;
    list_require_plain(elements_refcounted);
    RocList list = {to_word(bytes), length, capacity_or_alloc_ptr};
    if (update_mode != update_mode_in_place && !list_is_unique(&list)) {
        uint8_t *copy = alloc_with_refcount(length * element_width);
        memcpy(copy, bytes, length * element_width);
        data_decref(list_allocation(&list));
        list.bytes = to_word(copy);
        list.capacity_or_alloc_ptr = length << 1;
    }
    memcpy(from_word(list.bytes) + (roc_usize)index * element_width, element, element_width);
    *out = list;
}

void roc_builtins_list_decref_with_single_thread(uint8_t *bytes, roc_usize length, roc_usize capacity_or_alloc_ptr,
                                                 uint32_t alignment, roc_usize element_width,
                                                 void *element_decref) {
    (void)alignment, (void)element_width;
    if (element_decref != NULL) crash("roc: list of refcounted elements");
    RocList list = {to_word(bytes), length, capacity_or_alloc_ptr};
    if (list_capacity(&list) == 0 && !list_is_slice(&list)) return;
    data_decref(list_allocation(&list));
}

void roc_crashed(const uint8_t *message, roc_usize len) {
    fputs("roc crashed: ", stdout);
    for (roc_usize i = 0; i < len; i++) putchar(message[i]);
    putchar('\n');
    exit(1);
}

// Host.puts!: the string is ours to release.
void roc_host_puts(RocStr *str) {
    if (str_is_small(str)) {
        const uint8_t *bytes = (const uint8_t *)str;
        uint8_t len = bytes[sizeof(RocStr) - 1] & (uint8_t)~small_str_flag;
        for (uint8_t i = 0; i < len; i++) putchar(bytes[i]);
    } else {
        const uint8_t *bytes = from_word(str->bytes);
        for (roc_usize i = 0; i < str->length; i++) putchar(bytes[i]);
    }
    str_decref(str);
}

// Host.putc!: wasm32's C ABI passes the U8 widened to 32 bits.
void roc_host_putc(uint32_t byte) { putchar((uint8_t)byte); }

void roc_main(void);

int main(void) {
    roc_main();
    return 0;
}
