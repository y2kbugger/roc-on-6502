"""The SQL core: a 6502 that lives in a SQLite database.

The machine is core.sql (generated from isa.py by gencore.py): a dense
memory table, a decode table, a one-row cpu latch, and a clock whose
trigger executes one instruction per tick.  Python's role is strictly I/O:
compiling bytes into an INSERT, reading state out, pulsing the clock.

Every instance is a backup-API clone of one template database (0.2 ms)
rather than a re-run of the schema (20 ms).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sql65.vm import MEMORY_SIZE, VM, Crash, Run, State

CORE_SQL = (Path(__file__).parent / 'core.sql').read_text()
MAX_CHUNK = 0x10000  # rows in the ticks table

_template: sqlite3.Connection | None = None


def _template_db() -> sqlite3.Connection:
    global _template
    if _template is None:
        _template = sqlite3.connect(':memory:', autocommit=True)
        _template.executescript(CORE_SQL)
    return _template


def compile_rom(data: bytes, origin: int = 0) -> str:
    """Bytes in, one statement out: the ROM as INSERTs into the memory table."""
    assert origin + len(data) <= MEMORY_SIZE
    values = ','.join(f'({addr},{v})' for addr, v in enumerate(data, origin))
    return f'INSERT OR REPLACE INTO memory (addr, v) VALUES {values}'


class SqlVM(VM):
    def __init__(self) -> None:
        self.conn = sqlite3.connect(':memory:', autocommit=True)
        _template_db().backup(self.conn)
        self._chunk = 64  # ticks per clock insert while a platform is watching addresses
        self._watch = (-1, -2, -1, -2)

    @property
    def pc(self) -> int:
        return self.conn.execute('SELECT pc FROM cpu').fetchone()[0]

    @property
    def state(self) -> State:
        pc, a, x, y, s, p = self.conn.execute('SELECT pc, a, x, y, s, p FROM cpu').fetchone()
        return State(pc=pc, a=a, x=x, y=y, s=s, p=p)

    @state.setter
    def state(self, state: State) -> None:
        self.conn.execute(
            'UPDATE cpu SET pc = ?, a = ?, x = ?, y = ?, s = ?, p = ?, halted = 0',
            (state.pc, state.a, state.x, state.y, state.s, state.p),
        )

    def read(self, addr: int) -> int:
        return self.conn.execute('SELECT v FROM memory WHERE addr = ?', (addr,)).fetchone()[0]

    def write(self, addr: int, value: int) -> None:
        self.conn.execute('UPDATE memory SET v = ? WHERE addr = ?', (value & 0xFF, addr))
        self.conn.execute('UPDATE cpu SET halted = 0')

    def load(self, data: bytes, origin: int = 0) -> None:
        self.conn.execute(compile_rom(data, origin))
        self.conn.execute('UPDATE cpu SET halted = 0')

    def dump(self, start: int = 0, length: int = MEMORY_SIZE) -> bytes:
        rows = self.conn.execute('SELECT v FROM memory WHERE addr >= ? AND addr < ? ORDER BY addr', (start, start + length))
        return bytes(v for (v,) in rows)

    def _steps(self) -> int:
        return self.conn.execute('SELECT steps FROM cpu').fetchone()[0]

    def step(self) -> None:
        try:
            self.conn.execute('UPDATE cpu SET hit = NULL')
            self.conn.execute('INSERT INTO clock VALUES (0)')
        except sqlite3.DatabaseError as e:
            raise Crash(1, e) from e

    def run(self, max_steps: int, io: tuple[int, int] | None = None, wr: tuple[int, int] | None = None) -> Run:
        """Ticks the clock in chunks; a chunk's remaining ticks are no-ops once
        the latch halts or trips the watch, so the chunk grows while runs are
        clean and shrinks when the platform hook is busy.  The chunk is a
        rowid range, never a LIMIT: with a trigger on the target, SQLite
        materialises the whole source before a LIMIT applies (~100 ms)."""
        conn = self.conn
        watch = (*(io or (-1, -2)), *(wr or (-1, -2)))
        if watch != self._watch:
            conn.execute('UPDATE watch SET io_lo = ?, io_hi = ?, wr_lo = ?, wr_hi = ? WHERE id = 1', watch)
            self._watch = watch
        watching = io is not None or wr is not None
        done, chunk = 0, (self._chunk if watching else 1024)
        steps_before = self._steps()
        conn.execute('UPDATE cpu SET hit = NULL')
        while done < max_steps:
            try:
                conn.execute('INSERT INTO clock SELECT n FROM ticks WHERE n < ?', (min(chunk, max_steps - done),))
            except sqlite3.DatabaseError as e:
                raise Crash(done + (self._steps() - steps_before) + 1, e) from e
            steps, halted, hit, ipc = conn.execute('SELECT steps, halted, hit, ipc FROM cpu').fetchone()
            done, steps_before = done + steps - steps_before, steps
            if halted:
                return Run(done, stuck=True)
            if hit is not None:
                conn.execute('UPDATE cpu SET hit = NULL')
                self._chunk = max(32, chunk // 2)
                return Run(done, touched=hit, ipc=ipc)
            chunk = min(chunk * 2, MAX_CHUNK)
            if watching:
                self._chunk = chunk
        return Run(done)

    def trace(self) -> dict:
        """The latch after the last instruction, mnemonic included: the free debugger."""
        cur = self.conn.execute('SELECT * FROM trace')
        return dict(zip([d[0] for d in cur.description], cur.fetchone()))
