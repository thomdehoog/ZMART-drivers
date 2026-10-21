# Run on the microscope: `agent/anatomy-refactor-rc-direct-writers` at `1e3043f`, 2026-10-21

This is the record of the quick hardware acceptance of the direct writers on the
STELLARIS at host `ZMB-Y42H93-STI8`, with `run_ci.py --hardware` against the
running LAS X. In this branch every change is sent straight to LAS X's hardware
server first, and the CAM API sends it only when the direct route cannot.
The result in one line: **passed**, after the branch was brought up to date with
`agent/anatomy-refactor-rc-acquire-save-clear`. All eight steps were green, and
the installed-driver acceptance checklist passed on all eight points.

- **Branch and commit:** `agent/anatomy-refactor-rc-direct-writers` at `1e3043f`, the merge of `agent/anatomy-refactor-rc-acquire-save-clear` (at `2d71a4b`) into `24b97c0` ("Leica: the direct writers in the new anatomy, direct first and the CAM API behind them").
- **Microscope:** LAS X 4.9.2.31016 with the NavigatorExpert add-in, real optics, not the simulator. The objective in place was the HC PL APO CS2 10x/0.40 DRY.
- **Loaded experiment:** three jobs, `Overview`, `HiRes` and `AF Job`. `Overview` was selected at the start and at the end.
- **Configuration used:** the machine's own limits of 2026-07-21; the limits handshake reported `source: machine`, not a fallback.
- **Python:** 3.11.17 in the conda env `zmart-drivers-readers`, with `zmart-controller` 0.4.0rc1.
- **Write routes:** the default, `direct,cam`. `ZMART_WRITE_ROUTES` was not set.
- **Run by:** an automated session, at Thom's request, with the stage clear.
- **Not run:** the whole offline suite. After the merge, the twelve test files nearest to the writers and the dispatcher were run (443 passed). `--full` was not used, so the api, log and hybrid reader routes were not run.

---

## 1. The live run

`python testing/run_ci.py --hardware`, 2 min 5 s in total.

| Step | Result |
|---|---|
| limits: mock self-check before any hardware | OK, 158 passed |
| hardware: the stage to the CI baseline | OK, 36 pass, 0 fail, 4 skip |
| hardware: installed driver (register folder, connect by name) | OK, 43 pass, 0 fail |
| hardware: zmart adapter (move / state / acquire) | OK, 68 pass, 0 fail, 1 skip |
| hardware: end-to-end validator, direct reader | OK, 113 pass, 0 fail, 3 skip |
| hardware: the buttons the driver presses (Jobs, Experiment, Clear) and LAS X's dialogs | OK, all checks passed |
| hardware: saving from LAS X's cache (one capture, checked) | OK, all checks passed |
| hardware: letting go of LAS X's cache (10 captures, the canvas cleared) | OK, 0 failed, every canvas cleared |

**Acceptance checklist** from the installed-driver run, all PASS:

- installed by folder
- connected by name, every `get_*` answer fits the contract
- every command answers in the controller's shape
- a limits file governs the session
- the limits gate refuses before anything moves
- a move is read back where it was asked
- the controller accepts an acquisition
- everything restored

### What the direct writers carried

Every change in the run has a direct translation: the stage moves to the CI baseline and the
ten-position XY pattern, the z-galvo round trip, the job switches (`AF Job`, `HiRes`, `Overview`,
each confirmed in about 0.1 to 0.3 s), zoom, scan speed, resonant flip, sequential mode, scan-field
rotation, image format, frame and line accumulation and average, the pinhole, and the captures. The
route chain logs a warning each time the direct route cannot send and the CAM API takes over; no
such warning was logged in this run, so the direct route sent every command.

### Saving and clearing

- **One capture of `Overview`** (512 x 512, two channels, 8 bit) was saved as one valid OME-Zarr 0.5
  store with valid OME 2016-06 metadata and the log beside the data.
- **Ten captures, each saved and then cleared**, in 47 s. LAS X's cache (`D:\Temp2`) did not grow,
  and the hidden copies went down slightly (1.0 MB to 0.2 MB).

### What was skipped, and why

None of the skips is a problem. They are the validators' deliberate opt-outs: autofocus and the
objective turret are not switched on by `run_ci`, the HyD S 2 exposes no writable gain, and the
baseline step leaves moves, state and acquisition to the later steps. Detector gain, laser
intensity, the shutter, the filter wheels, the z-stack and the objective slot have direct
translations but were not exercised by this run.

---

## 2. What to look at

### 2.1 Before the merge, two checks failed in the test scripts, not in the writers

The branch was first run as it was on GitHub (`24b97c0`). Two checks failed: "the whole job ran" in
the adapter step and "log beside the data" in the cache step. Both read `trigger` and `log` from the
controller's `acquire` answer, which carries only the files and the planes. Commit `88c84be` on
`agent/anatomy-refactor-rc-acquire-save-clear` makes these checks read them from the saved files,
and the direct-writers branch had split off just before it. With that fix the two steps passed on
the microscope, and after the merge the whole run passed.

### 2.2 Paths longer than 260 characters break saving on this computer

Long paths are switched off in Windows on this computer (`LongPathsEnabled` is 0). While a capture is
written, the OME-Zarr writer keeps temporary chunk files whose paths, under
`testing\_report\captures`, are about 252 characters long with the label `ci`. A rerun of the cache
step with the label `ci_recheck2` made them 261 characters, and every save then failed with
`FileNotFoundError` on a `.partial` file. The real run is not affected, but a deeper checkout or a
longer label will fail in a way that does not say why. Either long paths are switched on, or the
captures go to a shorter folder.

### 2.3 Before the merge, a file was held open twice while a capture was renamed into place

In the first run, two of the ten captures reported "renamed into place after 0.1 s; something held
a file in it open". Both captures were saved. This did not happen in the run after the merge.

---

## 3. Side effects on this computer

- `register_driver` wrote this checkout into the controller's list of drivers,
  `C:\ProgramData\zmart-microscopy\zmart-controller\drivers.json`: `stellaris` now points at
  `C:\Users\t.de\conda\repos\zmart-drivers-direct-writers\...\navigator_expert\for_zmart_controller\zmart_driver.json`.
  Anything that connects by name will load this branch, and so send through the direct writers,
  until the entry is changed.
- The run reports live in `testing/_report/` (ignored by git).
