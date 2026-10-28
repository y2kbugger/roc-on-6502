"""Run a 6502 program on the SQL 6502.

    python3 run.py out/pi.sim

The program is an llvm-mos `sim` image: blocks of (address, length, bytes),
with little-endian 16-bit header words, and the reset vector says where to
start. The machine is llvm-mos's simulator as its libc sees it: flat 64 KiB of
RAM, a byte written to $FFF9 is a character out, a byte written to $FFF8 ends
the program with that exit code.

The CPU is sql65/: every instruction is one SQL UPDATE in an in-memory SQLite
database. Python only loads the image and watches the two ports.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from sql65.sqlvm import SqlVM

EXIT, PUTCHAR = 0xFFF8, 0xFFF9
RESET = 0xFFFC
MAX_STEPS = 50_000_000


def blocks(image: bytes):
    """The image's (address, bytes) blocks."""
    at = 0
    while at < len(image):
        addr = int.from_bytes(image[at:at + 2], 'little')
        length = int.from_bytes(image[at + 2:at + 4], 'little')
        block = image[at + 4:at + 4 + length]
        assert len(block) == length, f'block at ${addr:04X} is truncated'
        yield addr, block
        at += 4 + length


def load_image(vm: SqlVM, image: bytes) -> None:
    for addr, block in blocks(image):
        vm.load(block, addr)


def run(vm: SqlVM, out) -> tuple[int, int]:
    """Run to the exit port; return (exit code, instructions)."""
    target = vm.read(RESET) | (vm.read(RESET + 1) << 8)
    vm.state = vm.state.replace(pc=target)
    steps = 0
    while steps < MAX_STEPS:
        r = vm.run(MAX_STEPS - steps, wr=(EXIT, PUTCHAR))
        steps += r.steps
        if r.touched == PUTCHAR:
            out.write(chr(vm.read(PUTCHAR)))
            out.flush()
        elif r.touched == EXIT:
            return vm.read(EXIT), steps
        elif r.stuck:
            raise SystemExit(f'stuck at ${vm.pc:04X} after {steps} instructions')
    raise SystemExit(f'no exit after {steps} instructions')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('image', type=Path)
    args = parser.parse_args()
    vm = SqlVM()
    load_image(vm, args.image.read_bytes())
    start = time.perf_counter()
    code, steps = run(vm, sys.stdout)
    elapsed = time.perf_counter() - start
    print(f'[exit {code}, {steps:,} instructions, {elapsed:.2f} s]', file=sys.stderr)
    sys.exit(code)


if __name__ == '__main__':
    main()
