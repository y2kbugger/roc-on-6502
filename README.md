# roc-on-6502

[Roc](https://www.roc-lang.org), a functional language from the 2020s, compiled through LLVM for
the 6502, a CPU from 1975, and run by a 6502 written in SQL. Every instruction is one SQLite
`UPDATE`. It computes pi.

```text
$ sqlite3 < pi.sql
Calculated first 69 digits of pi: 314159265358979323846264338327950288419716939937510582097494459230781
Ran 14440597 6502 instructions, exit code 0.
```

That takes about 80 seconds, which is roughly how long it takes to explain why.


## Use it

`pi.sql` needs nothing but `sqlite3`: the CPU, the program and the clock ticks are all in the file.

```sh
git clone https://github.com/y2kbugger/roc-on-6502
cd roc-on-6502
sqlite3 < pi.sql
```

It is the Rabinowitz–Wagon spigot ([`apps/pi.roc`](apps/pi.roc)), the same 69 digits as
[`PISPIGOT69`](https://github.com/y2kbugger/y2k-lc3-tools/blob/main/src/PISPIGOT69.asm), which did
it in assembly on an LC-3 written in SQL. This is the SQL sequel.


## How it works

```text
apps/pi.roc ──roc──▶ LLVM bitcode (wasm32) ──retarget──▶ 6502 IR
            ──llvm-mos clang + platform/host.c──▶ 5 KB 6502 image
            ──mksql.py──▶ pi.sql ──sqlite3──▶ π
```

- **Roc to LLVM.** Roc has no 6502 target, so `roc build --target=wasm32` makes the bitcode.
    - wasm32 is Roc's smallest target, and its IR is plain: pointers, `i32`s, byte buffers.
- **LLVM to 6502.** Drop the bitcode's wasm32 triple and data layout, and hand it to
  [llvm-mos](https://llvm-mos.org), an LLVM with a 6502 backend.
    - It compiles and LTOs the app with the C host into one image for its `sim` machine.
- **The ABI is 32-bit on a 16-bit machine.** Roc's words stay 32 bits; a pointer fills the low 16.
    - Both CPUs are little-endian, so a 16-bit load of a 32-bit word reads the pointer.
    - It holds as long as nothing moves a pointer through a 32-bit integer; these apps don't.
    - [`platform/host.c`](platform/host.c) speaks the same layout: `Str` and `List` are 12 bytes.
- **The host is C.** [`platform/`](platform) is a Roc platform: `Host.puts!`, `Host.putc!`.
    - It also has the few Roc builtins the apps call, in C, after Roc's own Zig: string literals,
      `List.repeat`, `List.set`, freeing a list.
    - Any other builtin is a link error, by name.
- **The CPU is SQL.** [`sql65/`](sql65) is a 6502 as tables and one trigger per clock tick.
    - Memory is a 65,536-row table.
    - The console is two more triggers: a byte written to `$FFF9` is a character, one written to
      `$FFF8` halts the CPU.
    - [`mksql.py`](mksql.py) appends the program and 222 batches of 65,536 ticks.

The 6502 has no divide instruction, so most of the 14.4 million instructions are the spigot's two
16-bit divisions per term, each a call into llvm-mos's runtime.


## The road ahead

- Roc's real builtins on the 6502: Roc's Zig through Zig's C backend, then llvm-mos.
- A 16-bit word in Roc itself, instead of 32-bit words on a 16-bit CPU.
- `roc build` emitting bitcode directly, instead of fishing it out of `--keep-temp`.
- A test that builds every app and checks its output.


## Working on it

| tool | verified with |
|---|---|
| [Roc](https://www.roc-lang.org), the new compiler | nightly-2026-10-24-f6c6cfb |
| [llvm-mos SDK](https://github.com/llvm-mos/llvm-mos-sdk/releases) (`mos-sim-clang`) | clang 24.0.0git (llvm-mos a40207a3) |
| `llvm-dis`, any recent LLVM (the SDK has none) | LLVM 23.1.3 |
| Python | 3.14; 3.12 is the oldest whose `sqlite3` has `autocommit` |
| sqlite3 | 3.54.0 |

Put them on `PATH`, or name them: `ROC`, `LLVM_DIS`, and `LLVM_MOS` for the SDK's directory.

```sh
./build.sh apps/hello.roc --run   # Hello, World! on the SQL 6502
./build.sh apps/count.roc --run   # triangle numbers: loops and arithmetic
./build.sh apps/pi.roc --sql      # rebuilds pi.sql (runs it once to count ticks, ~80 s)
```

`out/` holds each step: `name.wasm32.bc`, `name.mos.ll`, `name.sim` and its `.elf`. `pi.sql` is
generated and committed on purpose, so it runs from a fresh clone.
