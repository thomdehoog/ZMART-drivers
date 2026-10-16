# On the microscope: saving straight from LAS X's cache

The driver can now save a capture straight from LAS X's own cache (the folder the registry names,
`D:\Temp2` on the STELLARIS Y42H93), instead of waiting
for AutoSave's file. It saves one copy (OME-Zarr by default, or OME-TIFF) with LAS X's full record
of the capture in it, a max projection beside a stack, and a log of what the driver did. On the
LAS X simulator every check passed (11 October 2026), including 40 captures in a row, a
2048 × 2048 image and an 84-plane stack, all identical to AutoSave's files.

The simulator could not show nine things. This sheet runs them on the STELLARIS. Each test is one
command and one report; the commands only select a job, capture, and read. They never edit a LAS X
file, and they move the stage only to where it already stands.

---

## Before the session

1. **Update all four** on the microscope PC, in the local clones in the home folder (not from Z:):
   `ZMART-controller`, `ZMART-drivers`, `ZMART-interface` and `ZMART-viewer`, each to
   `release-candidate`. They go together: the controller's `get_info` now says where the
   microscope saves and what is set up on it, the interface reads the driver's stores, and the
   viewer reads the driver's projections.
2. **Set the data location once**: open
   `configuration/data_location/notebooks/set_data_location.ipynb`, write the folder (a local disk,
   for example `D:\ZMART-data`) and run it. Without it the driver saves nothing and says so.
3. **AutoSave can stay off.** The script does not compare the capture with AutoSave's file any
   more: the cache route does not use AutoSave, and the check never asks about it. What you
   compare instead is the report against what LAS X shows for the same capture.

Run each test from a folder of your choice, in the driver's environment; the report is written
there as `check_cache_route_<label>.json`, and the last line says `ALL CHECKS PASSED` or which
check failed:

```
python <driver>\testing\hardware\check_cache_route.py --job "<job>" --label <label>
```

---

## The tests

| # | In LAS X first | Command (`--job`, `--label`) | Look at |
|---|---|---|---|
| 1 | A single-plane job, 8 bit | `--job HiRes --label hires-8bit` | All checks pass; the report's `metadata`: microscope model and serial, objective, each channel's detector and dye match what LAS X shows |
| 2 | Set the same job to 12 bit, then 16 bit (by hand) | `--label hires-12bit`, then `--label hires-16bit` | `type, significant bits` is `uint16, 12`, then `uint16, 16`. Set the job back afterwards |
| 3 | A job with **2 channels and a z-stack** | `--job <job> --label two-channel-stack` | `size (x, y, z, c)` has both channels and as many planes as LAS X's stack; in `planes`, both channels of a plane have the same canvas z, and `dt s` grows plane by plane |
| 4a | A stack made by **z-galvo**, with z-wide at its usual height | `--label galvo-stack` | In each plane's `drives`: z-wide stays at LAS X's z-wide position, z-galvo runs from the stack's begin to its end; the canvas z steps by the stack's step |
| 4b | The same stack made by **z-wide** | `--label wide-stack` | z-wide runs from begin to end, z-galvo stays put; the canvas z steps the same way |
| 4c | The same stack set up **top-down** (begin above end) | `--label stack-top-down` | In `planes`, depth 0 has the lowest canvas z and each depth is higher, as in 4b; the store's first slice is the lowest |
| 5 | A job with **two sequential settings** (different lasers or detectors) | `--label sequential` | Each channel's laser, detector, gain and detection band are those of its own setting in LAS X |
| 6 | — (no longer run) | — | AutoSave is off for every test now, so the others already show that a capture is saved without it |
| 7 | — (any report) | — | `laser power, as LAS X writes it`: does `OutputPowerWatt` hold real numbers on this system, and in which unit? |
| 8 | Start the interface on the STELLARIS | A short run: overview, focus, targets | In the run folder: each `<type>/data` holds only `.ome.zarr` stores; stacks have `<type>/projections`; every capture has a `<type>/log` file; there is no `positions` folder; the viewer shows the overview and the projections |

For 4a and 4b, keep LAS X's own numbers at hand: the z-wide position and the stack's begin and end
as LAS X shows them. On the simulator z-wide never moves, so these two are the first real test of
the plane heights.

---

## If a check fails

- **`captured and saved` is false**: the report's `error` says why. "does not know where to save"
  means step 2 above was skipped. "no picture of job … appeared" means LAS X did not put the
  capture in its cache; send the report and say what LAS X showed.
- **A metadata value is wrong** (a dye, a detector, a height): note what LAS X shows for it, and
  send the report.

## What to send back

The `check_cache_route_*.json` reports, and for any test whose values do not match LAS X, what LAS X
showed. `"cache"` is already the default source (`actions/profiles.py`, `IMAGE_SAVE.source`,
14 October 2026), so AutoSave stays off in normal use; with all the tests passing, the
repositories go to `main`.
