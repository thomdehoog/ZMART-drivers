# The check at the microscope

`navigator_ci.py` is the plain Navigator driver end to end on the STELLARIS, with LAS X showing its
Navigator window. It is the 50-step check that passed on the STELLARIS on 15 October 2026, run through
the driver's own parts: the readings, the changes through the limits gate, saving from LAS X's cache and
letting go of it.

Run it through the driver's CI, from the repository root, in the driver's environment on the LAS X
computer:

```bash
python zmart_drivers/leica/stellaris/navigator/testing/run_ci.py --hardware --out D:/ZMART-check/navigator-ci
```

`run_ci.py` first proves the limits gate offline (`testing/unit/test_limits.py`) and refuses to touch
the microscope when that fails. `--tiles N` sets how many fields the tile scan takes (three by default)
and `--allow-objective` also turns to another objective of the same immersion and back. The report is
`navigator_ci.json` in `--out`, beside the saved pictures; the last line printed says
`ALL CHECKS PASSED` or what failed.

## Before you start

- LAS X is open, idle, and shows its **Navigator** (not Navigator Expert). The driver refuses to connect
  when Navigator Expert's jobs are shown.
- A dry objective is selected, or one whose immersion you are happy to keep: the stage moves.
- This microscope's limits are published (`driver_setup/limits/notebooks/set_limits.ipynb`), or the
  check runs within the driver's cautious defaults and says so.
- The stage and both z drives stand inside those limits: the check reads them first and stops when
  they do not, before anything moves.

## What the check changes on the instrument, and puts back

| Step | What moves or changes | Put back |
|---|---|---|
| 2 | Zoom, scan speed, scan field rotation, line average, pinhole: each to a value inside its own range | each to its starting value |
| 3 | z-galvo and z-wide by +2 µm; the stage by +25 µm in x | each back |
| 4 (`--allow-objective` only) | another objective of the same immersion | the starting objective; z-wide is left where LAS X put it and reported |
| 5 | one capture into the LAS X project the driver chooses | saved, then let go of in the Data Container |
| 6 | a tile scan over `--tiles` fields in a row, a field and a tenth apart (about 2.6 mm for three fields with the 10x at zoom 1; the other way near the edge of the limits) | the stage at the first field; the scan mode and the tile scan settings as they were; the picture saved and let go of |

LAS X's Information and Warning boxes with only an OK button are answered by the driver while the
check runs; the running log lists each one.

## Offline, without LAS X

`python zmart_drivers/leica/stellaris/navigator/testing/run_ci.py` runs the offline suite against the
stand-in for LAS X's plain Navigator: the driver on its own, then the driver inside the ZMART
Controller when the controller is installed. The reports land in `testing/report/`.
