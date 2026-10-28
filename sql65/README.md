# sql65: the SQL 6502

A 6502 CPU written in SQLite, vendored from sql65 (not published on its own), commit d856249: the
files this repository runs, unchanged.

| file | what |
|---|---|
| `core.sql` | the CPU: a 64 KiB `memory` table, a 256-row `decode` table, a one-row `cpu` latch, and a `clock` whose trigger executes one instruction in one `UPDATE` |
| `sqlvm.py` | the CPU behind a small Python interface: load memory, read state, run until a watched address is written |
| `vm.py` | that interface |

`core.sql` is generated in sql65 from the instruction set written as data (`isa.py`, `gencore.py`,
not vendored), and there it passes nestest and Klaus Dormann's functional and decimal test ROMs,
at about 215,000 instructions a second.
