"""Compile an llvm-mos `sim` image into one SQL file: the SQL 6502, the
program in its memory, and the clock ticks to run it. Anyone with sqlite3
runs it:

    python3 mksql.py out/pi.sim -o pi.sql
    sqlite3 < pi.sql

The machine is run.py's, in SQL alone: two triggers on memory are its ports,
a byte written to $FFF9 is a character out (into a `console` table), a byte
written to $FFF8 halts the CPU with that exit code. The program is run once
through run.py first, to count its instructions and so the ticks; for pi
that takes as long as the file itself does, about 80 seconds.
"""

from __future__ import annotations

import argparse
import io
import math
from pathlib import Path

from run import EXIT, PUTCHAR, RESET, blocks, load_image, run
from sql65.sqlvm import CORE_SQL, SqlVM

TICKS = 0x10000  # rows in core.sql's ticks table
STEP = 4 * TICKS  # clock ticks per step of the run: about 1.4 s, a digit or so of pi
REPOSITORY = 'https://github.com/y2kbugger/roc-on-6502'


def compile_sql(image: bytes, name: str, step: int = STEP) -> tuple[str, str, int]:
    """(the SQL, what the program prints, its instruction count)."""
    assert step % TICKS == 0 or step < TICKS, f'a step is under {TICKS} ticks or a multiple of it'
    vm = SqlVM()
    load_image(vm, image)
    printed = io.StringIO()
    code, steps = run(vm, printed)
    assert code == 0, f'{name} exits {code}'
    # One spare step: a step that starts after the halt does nothing.
    inserts = math.ceil(steps / step) + 1
    copies, rows = (step // TICKS, TICKS) if step >= TICKS else (1, step)

    out = [f'-- {name}: a Roc program compiled for the 6502, on a 6502 written in SQL.',
           '--',
           '--     sqlite3 < ' + name + '.sql',
           '--',
           f'-- It prints: {printed.getvalue().strip()}',
           f'-- in {steps:,} 6502 instructions, one SQL UPDATE each.',
           '--',
           f'-- Made by {REPOSITORY}: Roc -> LLVM -> llvm-mos -> 6502 -> SQL.',
           '',
           CORE_SQL.rstrip(),
           '',
           '-- The console: llvm-mos sim\'s two ports, as triggers on memory.',
           'CREATE TABLE console (n INTEGER PRIMARY KEY, ch INTEGER NOT NULL);',
           f'CREATE TRIGGER putchar AFTER UPDATE OF v ON memory WHEN NEW.addr = {PUTCHAR}',
           'BEGIN',
           '  INSERT INTO console (ch) VALUES (NEW.v);',
           'END;',
           f'CREATE TRIGGER exit AFTER UPDATE OF v ON memory WHEN NEW.addr = {EXIT}',
           'BEGIN',
           '  UPDATE cpu SET halted = 1;',
           'END;',
           '',
           '-- The program.']
    for addr, block in blocks(image):
        values = ','.join(f'({a},{v})' for a, v in enumerate(block, addr))
        out.append(f'INSERT OR REPLACE INTO memory (addr, v) VALUES {values};')
    out += ['',
            '-- Reset: start where the reset vector points.',
            f'UPDATE cpu SET pc = (SELECT v FROM memory WHERE addr = {RESET})'
            f' | ((SELECT v FROM memory WHERE addr = {RESET + 1}) << 8);',
            '',
            f'-- Run: {inserts} steps of {step:,} clock ticks, each printing what the program',
            '-- printed during it, so the output streams. Once halted, a step does nothing.',
            '.headers off',
            '.mode list',
            ".separator '' ''",
            f'CREATE VIEW chunk AS SELECT 0 FROM ticks, (SELECT 0 FROM ticks WHERE n < {copies})',
            f'    WHERE ticks.n < {rows} AND (SELECT halted FROM cpu) = 0;',
            'BEGIN;']
    out += ['INSERT INTO clock SELECT * FROM chunk; '
            'SELECT char(ch) FROM console ORDER BY n; DELETE FROM console;'] * inserts
    out += ['COMMIT;',
            '',
            "SELECT CASE WHEN halted THEN 'Ran ' || steps || ' 6502 instructions, exit code '"
            f" || (SELECT v FROM memory WHERE addr = {EXIT}) || '.'"
            " ELSE 'Still running after ' || steps || ' instructions.' END || char(10) FROM cpu;",
            '']
    return '\n'.join(out), printed.getvalue(), steps


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('image', type=Path)
    parser.add_argument('-o', '--output', type=Path, required=True)
    parser.add_argument('--step', type=int, default=STEP, help=f'clock ticks per step (default {STEP:,})')
    args = parser.parse_args()
    sql, printed, steps = compile_sql(args.image.read_bytes(), args.output.stem, args.step)
    args.output.write_text(sql)
    print(f'wrote {args.output} ({len(sql):,} bytes, {steps:,} instructions): {printed.strip()}')


if __name__ == '__main__':
    main()
