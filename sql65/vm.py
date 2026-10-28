"""Emulator-agnostic 6502.

Every core -- the trusted Python reference, the SQL core under construction,
and the deliberately broken mutants -- is the same tiny machine: flat 64 KiB
of RAM, six registers, a step button.  One interface means one test suite,
and it means any two cores can be stepped in lockstep and diffed.
"""

from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from dataclasses import dataclass

MEMORY_SIZE = 0x10000

FLAG_BITS = {'N': 0x80, 'V': 0x40, 'B': 0x10, 'D': 0x08, 'I': 0x04, 'Z': 0x02, 'C': 0x01}


@dataclass(frozen=True)
class State:
    """Complete 6502 register state, with power-on defaults."""

    pc: int = 0x0000
    a: int = 0x00
    x: int = 0x00
    y: int = 0x00
    s: int = 0xFD
    p: int = 0x24  # NV1B DIZC -- reserved bit and interrupt-disable set

    def flag(self, name: str) -> bool:
        return bool(self.p & FLAG_BITS[name])

    def replace(self, **changes: int) -> State:
        return dataclasses.replace(self, **changes)

    def __str__(self) -> str:
        flags = ''.join(f if self.flag(f) else f.lower() for f in FLAG_BITS)
        return f'PC={self.pc:04X} A={self.a:02X} X={self.x:02X} Y={self.y:02X} S={self.s:02X} P={self.p:02X} [{flags}]'


class VM(ABC):
    """A 6502 core: registers as a value-object State, memory as flat bytes."""

    @property
    @abstractmethod
    def state(self) -> State: ...

    @state.setter
    @abstractmethod
    def state(self, state: State) -> None: ...

    @property
    def pc(self) -> int:
        """Fast path for run loops; cores override to skip building a State."""
        return self.state.pc

    @abstractmethod
    def read(self, addr: int) -> int: ...

    @abstractmethod
    def write(self, addr: int, value: int) -> None: ...

    @abstractmethod
    def step(self) -> None:
        """Execute one instruction."""

    def run(self, max_steps: int, io: tuple[int, int] | None = None, wr: tuple[int, int] | None = None) -> Run:
        """Execute up to max_steps instructions natively, stopping early when
        the PC pins itself or -- the platform hook -- right after an
        instruction that reads or writes an address in `io` (inclusive
        bounds) or writes one in `wr`.  Memory-mapped hardware lives on that
        hook: the platform stages what a register reads as, the core runs
        until something touches it, the platform applies the side effect.
        A crash is raised as Crash with the count so far.

        Contract: once the PC has pinned itself, what further step()s do is
        unspecified (the Python cores re-execute the pinned instruction, the
        SQL core holds).  Setting state or writing memory un-pins it.
        """
        if io or wr:
            raise NotImplementedError(f'{type(self).__name__} cannot watch addresses')
        pc = self.pc
        for steps in range(1, max_steps + 1):
            try:
                self.step()
            except Exception as e:
                raise Crash(steps, e) from e
            if self.pc == pc:
                return Run(steps, stuck=True)
            pc = self.pc
        return Run(max_steps)

    def run_until_stuck(self, max_steps: int) -> tuple[int, bool]:
        """(steps, stuck): the ROM judges' view of run()."""
        r = self.run(max_steps)
        return r.steps, r.stuck

    NMI, RESET, IRQ = 0xFFFA, 0xFFFC, 0xFFFE

    def interrupt(self, vector: int = NMI) -> None:
        """Take a hardware interrupt: push PC and P (B clear, bit 5 set), set
        I, jump through the vector.  Built from read/write/state, so every
        core has it; an IRQ is refused while I is set."""
        st = self.state
        if vector == self.IRQ and st.flag('I'):
            return
        self.write(0x100 | st.s, st.pc >> 8)
        self.write(0x100 | ((st.s - 1) & 0xFF), st.pc & 0xFF)
        self.write(0x100 | ((st.s - 2) & 0xFF), (st.p | 0x20) & ~0x10)
        target = self.read(vector) | (self.read(vector + 1) << 8)
        self.state = st.replace(pc=target, s=(st.s - 3) & 0xFF, p=st.p | 0x04)

    def load(self, data: bytes, origin: int = 0) -> None:
        assert origin + len(data) <= MEMORY_SIZE
        for addr, value in enumerate(data, origin):
            self.write(addr, value)

    def dump(self, start: int = 0, length: int = MEMORY_SIZE) -> bytes:
        return bytes(self.read(a) for a in range(start, start + length))


@dataclass(frozen=True)
class Run:
    """How a batch run ended: after `steps` instructions, because the PC
    pinned itself (`stuck`), or because the instruction at `ipc` touched
    `touched`, an address in a watched range, or because the budget ran out
    (neither)."""

    steps: int
    stuck: bool = False
    touched: int | None = None
    ipc: int | None = None


class Crash(Exception):
    """A core raised while stepping.  For a ROM judge that is just another failure."""

    def __init__(self, steps: int, cause: Exception):
        super().__init__(f'crashed after {steps} steps: {cause!r}')
        self.steps = steps


class Divergence(AssertionError):
    """Two cores disagreed while stepping in lockstep."""


def run_lockstep(ref: VM, dut: VM, max_steps: int, memory_every: int = 1) -> int:
    """Step both cores together, raising Divergence at the first mismatch.

    Compares full register state after every instruction and full memory
    every `memory_every` instructions (a 64 KiB dump is cheap for the Python
    cores and 15 ms for the SQL core).  Stops early, without complaint, when
    the PC pins itself -- see run_until_stuck's contract.  Returns the
    number of steps taken.
    """
    for n in range(max_steps):
        before = ref.state
        ref.step()
        dut.step()
        if ref.state != dut.state:
            raise Divergence(f'state diverged on step {n} executing {before}:\n  ref {ref.state}\n  dut {dut.state}')
        if (n + 1) % memory_every == 0 or ref.pc == before.pc:
            if (ref_mem := ref.dump()) != (dut_mem := dut.dump()):
                diffs = [a for a in range(MEMORY_SIZE) if ref_mem[a] != dut_mem[a]]
                shown = ', '.join(f'{a:04X}: ref {ref_mem[a]:02X} != dut {dut_mem[a]:02X}' for a in diffs[:8])
                raise Divergence(f'memory diverged by step {n} executing {before} at {len(diffs)} address(es):\n  {shown}')
        if ref.pc == before.pc:
            return n + 1
    return max_steps
