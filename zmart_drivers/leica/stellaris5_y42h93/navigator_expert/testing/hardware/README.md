# Hardware validation: the same command on the simulator and on the scope

The bench entry point is `testing/run_ci.py` (the individual `validate_*.py`
scripts stay directly runnable for debugging):

```powershell
cd zmart_drivers/leica/stellaris5_y42h93/navigator_expert

# Offline, no LAS X: the whole suite against the LAS X mock
python testing/run_ci.py

# Live, about five minutes: the quick acceptance against LAS X (simulator or scope)
python testing/run_ci.py --hardware

# Live, 15 to 30 minutes: the quick set plus the reader diagnostics
python testing/run_ci.py --hardware --full
```

The validators do not know whether LAS X is in simulator mode or driving
real optics; that is LAS X's own switch. Run `--hardware` against the
simulator first, then the same command on the microscope. All green on the
simulator, then all green on the scope, is the definition of production
ready.

**`--hardware`, the quick acceptance**, runs in order:

1. **The limits self-check**, against the in-process mock, in this install,
   before LAS X is touched. If it fails the run hard-aborts and no hardware
   validator runs, so a broken limits gate can never reach the stage.
2. **The installed driver** (`validate_installed_driver.py`): the path an
   experiment takes. It registers this folder with the controller
   (`register_driver`), connects by name (`connect("stellaris")`), lets the
   controller's own `validate_driver` check every `get_*` answer, asks every
   command, proves that the limits gate refuses a far move before anything
   moves, makes one small move and back, and takes one acquisition that the
   controller's `check_acquire_answer` accepts. Its report ends with the
   **acceptance checklist**, one line per point, which `run_ci` prints in
   its summary.
3. **The adapter round-trip** (`validate_zmart_adapter.py`): first the
   four-axis CI baseline, then `set_origin` and `set_xyz`, the state
   round-trip, and an acquire through LAS X native AutoSave.
4. **The end-to-end validator in the hybrid reader route**
   (`validate_hardware.py`): every reversible setting, the XY pattern, the
   z-galvo round-trip, and an acquire. Hybrid is the production route, so
   this step is fatal.

**`--hardware --full`** adds the diagnostics, once per release or after a
change to the readers: the passive reader probe (api / log / hybrid), the
side-by-side reader parity check, and the end-to-end validator through the
api and log routes as well. Those two routes stay diagnostic: they document
bench-specific reader disagreements without failing the run.

Hardware validation uses only production driver modules, nothing under
`experimental/`.

Before the first stage-moving validator, hardware CI enforces and verifies the raw
four-axis baseline **X = 63,500 um, Y = 41,500 um, Z-wide = 0 um, Z-galvo =
0 um**. Z-wide has a physical lower backstop of 0 um: the CI excursion is
restricted to a non-negative delta (default +3 um), so the validator never
commands a negative Z-wide target and restores it to 0 um.

## Prerequisites

- LAS X running (simulator or scope) with the NavigatorExpert CAM add-in;
  no modal dialog open (a dialog blocks the whole CAM API).
- A template/experiment loaded with **at least two jobs** (e.g. Overview +
  HiRes) so job-selection round-trips have a target.
- Stage clear (no sample you care about): `--hardware` first moves to the
  fixed four-axis baseline above, then moves XY in a
  10-position pattern (±25 µm around the current position) and does a ±2 µm
  z-galvo round-trip, plus one or more capture+save smoke checks. Park the
  stage inside the calibrated envelope first — the validators refuse to move
  if the start position is outside limits. That refusal is a **SKIP**, not a
  failure (the LAS X simulator commonly homes at 0,0, outside a real machine's
  envelope): it means "reposition to exercise this phase," not "the driver is
  broken."
- **Machine-local limits available in ProgramData** (the single `limits.json`
  in the newest snapshot under `C:\ProgramData\zmart-microscopy\...`, alongside
  `calibration.json` or `calibrations/<name>/calibration.json`,
  `orientation.json` + `origin.json`). If ProgramData is empty, the repo
  defaults are copied there first. Every validator runs the connect-time limits
  handshake (`limits: connect handshake` in the report): it validates schema,
  finite numbers, and containment within the hardcoded physical backstop
  (`motion.limits.STAGE_BACKSTOP_UM`). Run the three setup notebooks on the rig
  to replace defaults with measured values. (`--mock` uses a hermetic
  ProgramData root and exercises the same real handshake.)
- Driver requirements installed (`pip install -r testing/requirements-dev.txt`).

## What `--hardware` changes on the instrument (all restored in `finally`)

- Reversible per-job settings: zoom, scan speed, resonant flip, sequential
  mode, scan-field rotation, image format, frame/line accumulation+average,
  pinhole, detector gain (only if the detector exposes a writable range).
- Job selection: every reported job is selected once, then the original is
  restored. (`validate_readers_side_by_side --allow-job-switch` is NOT part of
  the run_ci set — it pops the manual-turret dialog; run it manually if wanted.)
- Stage: the adapter validator first enforces the fixed four-axis CI baseline,
  then does `set_origin` + small frame moves and restores to that baseline. The
  later XY pattern and z-galvo round-trips restore to the same captured start.
- Frame origin: `set_origin` is a driver setup step, so the validator calls the
  adapter's `set_origin` on the session's driver handle rather than through the
  controller. Because connect loads the newest saved origin, the validator then
  deletes the `origin/<datetime>/` record its own `set_origin` wrote. The
  microscope's saved origin is left exactly as it was before the run.
- Acquisition: the adapter validator reads the notebook-critical live
  `get_info()` snapshot (output root, tile positions, focus positions) and one
  acquire+save smoke through LAS X native AutoSave. Each end-to-end reader route
  runs an acquire command in its reader mode; file materialization is proven once
  through the adapter path.
- **NOT touched by run_ci**: objective turret. Opt in via a direct run only
  when the operator wants it, e.g.
  `python tests/hardware/validate_hardware.py --yes --allow-objective --allow-acquire`.

Every attempted change — including failed attempts and every restore — is
recorded in the Markdown run report with its success+CONFIRMED /
success+UNCONFIRMED / FAILED result, attempt counts, and timing.

## Expected duration

- `python testing/run_ci.py`: mock/offline, no LAS X required.
- `python testing/run_ci.py --hardware`: about five minutes against a live LAS X
  session. `--full`: 15 to 30 minutes, dominated by per-command confirmation
  polling (up to 3 × 3 s readback windows per setting write, × 3 reader
  routes). Against the in-process mock the same paths run in seconds.

## Where the results land

- **Markdown run reports** (one per validator run, human-readable):
  `tests/_report/hardware_run_report_<YYYYMMDD-HHMMSS>.md` — run metadata
  (date, host, mock-or-live, driver commit), summary table per phase, timing
  overview (per-phase and per-reader-mode latency, slowest actions,
  unconfirmed/failed changes), then the chronological detail of every
  attempted action. Paths are printed at the end of the run_ci output.
  Direct script runs write to the working directory unless `--report-dir`
  is given.
- JSONL step records: `tests/_report/hardware_validate_{api,log,hybrid}.jsonl`,
  `zmart_adapter_validate.jsonl`; step summary in `ci_summary.json`.

## Reader modes

The side-by-side validator reads every routed datum (xy, jobs, selected_job,
scan_status, hardware_info, job_settings) explicitly in `mode="api"`,
`mode="log"`, and `mode="hybrid"` through `readers.router`, records value /
provenance / freshness (age) / latency per mode, and cross-checks modes
against each other (xy within 1 µm; discrete values equal). Router-level
hybrid reads are verified working against the mock (they degrade to the api
leg when no fresh log value exists). A log-mode `None` is the router's
fail-closed answer for a stale/absent log and is recorded as SKIP; a hybrid
`None` while api delivered is recorded as a structured FAIL, not a crash.
(The hybrid *confirmation* race's API-leg self-block, CF-01, is fixed; the
select_job round-trips in `--hardware` exercise the repaired race — check
the report for which leg confirmed, and how fast.)

## Offline gates (no LAS X)

Normal CI (`python testing/run_ci.py`, default offline mode) keeps the hardware
suite's health checked via the mock-backed wrappers, which also assert the
run report is produced:

```powershell
python -m pytest -q tests/hardware   # test_validate_*.py + test_stress_hardware.py
python tests/hardware/validate_readers_side_by_side.py --mock --yes   # offline smoke
python tests/hardware/validate_hardware.py --mock --allow-xy --allow-z --allow-objective --allow-acquire
```

The limits enforcement itself has a permanent adversarial gate in normal CI
(`tests/unit/test_limits_adversarial.py`): malformed/poisoned limits files,
NaN/inf targets, unset-envelope refusals, backstop containment, and
gate-bypass attempts through every entry point (commands, adapter,
controller). It must stay green before any bench run.
