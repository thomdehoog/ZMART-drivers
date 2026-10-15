# Next session: branch `readers-for-stellaris`

Where this branch stands (15 October 2026, evening) and what to do next. Read this first, then
[`STELLARIS_RERUN.md`](STELLARIS_RERUN.md) and
[`SCOPE_SESSION_STELLARIS.md`](SCOPE_SESSION_STELLARIS.md).

## Where it stands

- **Branch** `readers-for-stellaris` on `ZMART-drivers`, from `release-candidate` at `74b3451`.
  The commits of 15 October afternoon are **not pushed yet**; Thom decides when.
- **The re-run on the STELLARIS (15 October 2026, afternoon and evening) is done:**
  - **Step 1, the CI with another window in front:** passed twice; LAS X stayed behind the
    other window the whole time.
  - **Step 4, the cache route** (`SCOPE_CHECK_CACHE_ROUTE.md`), each report checked against
    what LAS X shows:
    - passed: 1 (single plane, 8 bit), 2 (12 and 16 bit), 3 (two channels and a z-stack),
      4a (z-galvo stack with z-wide at its working height), 4b (z-wide stack), 5 (two
      sequential settings), 7 (the laser power fields);
    - **4c (a stack set up top-down) found a bug:** the z pixel size was saved negative
      (`PhysicalSizeZ = -2.409`), which OME does not allow. **Fixed** (see below), and run
      again on the microscope (`stack-top-down-2`): all checks passed, the z pixel size
      +2.409 um, plane 0 the lowest and the highest on top, the planes' times showing the
      stack was taken from the top; LAS X's own record has begin above end;
    - 6 is no longer run (the check does not use AutoSave); 8 (a run in the interface) was
      not done.
  - **Step 3, LAS X's warnings:** skipped again; no warning with only an OK button could be
    called up.
- **The complete hardware CI passed on the STELLARIS after all of today's changes**
  (`run_ci.py --hardware --full`, 11 minutes): every step OK, with two warnings in the
  diagnostic routes (see "Found on the microscope").
- **Offline:** the full suite (`run_ci.py`) passes.

## What this session added

1. **The direct reader is complete.** Every reading the driver makes comes from LAS X's state
   tree first, with the CAM API only as the fallback:
   - **The job list** now comes from the direct reader too. Which job is Navigator Expert's
     autofocus job is read from each job's own `IsAutoFocusOnStart`, next to its name, so it
     does not depend on the job ids, which change. On the STELLARIS it marks the AF job alone,
     in agreement with the CAM API.
   - **Calibration** (pixel size, image format, z-stack heights), **measuring the limits**
     (each corner's stage position) and the **validator's readbacks** read directly. They
     had been pinned to the CAM API before the direct reader existed.
   - Two CAM API readings stay on purpose: the validator's independent check that a job
     switch happened, and the job-switch confirmation's fallback.
2. **Top-down stacks** (test 4c): the z pixel size is the distance between planes, always
   positive (`output/lasx_description.py`). Today's top-down capture, read again with the fix,
   gives +2.409 um, its planes still running downwards.
3. **The CI is a full suite** (`testing/run_ci.py`; details in `testing/hardware/README.md`):
   - **`--hardware`** (about 5 minutes): the limits gate, the stage baseline, the installed
     driver, the adapter, the end-to-end validator through the direct reader, **the buttons**,
     saving from the cache, and letting go of it.
   - **`--hardware --full`** (about 11 minutes on the STELLARIS) adds **the three readers side
     by side** (direct, CAM API, log), the reader probe, the parity check, and the validator
     through the api, log and hybrid routes.
   - **New, `check_buttons.py`:** presses Jobs on purpose, checks that the driver brings the
     canvas back with Experiment, presses Clear, and checks that LAS X shows no dialog.
   - **New, `benchmark_readers.py --check`:** fails when a reader fails or the CAM API
     disagrees with the direct reader (z-galvo excepted, see below).
   - **The cache release now checks Clear's effect:** it fails when the hidden copies in LAS
     X's cache grew over the run. Before, only the press counted.
   - **The save check reports metadata the OME schema refuses** as a failed check, instead of
     stopping without a report (as it did on 4c).
4. **The sheets** (`SCOPE_CHECK_CACHE_ROUTE.md`, step 4 of `SCOPE_SESSION_STELLARIS.md`) no
   longer compare with AutoSave's files; each report is checked against what LAS X shows.

## Found on the microscope

- **The CAM API's z-galvo is stale while the job has a z-galvo stack:** after a move it kept
  answering the stack's middle (8.64 um), while the direct reader saw the move at once. This
  made the validator's z check fail until it read directly. The reader check does not compare
  the CAM API's z-galvo for this reason.
- **The CAM API's selected job can be stale after a switch** (it said Overview after a switch
  to HiRes): the warning in the validator's api route. The driver confirms switches directly.
- **The log route is slow:** one job switch was not seen in the log in time, and the route
  took 345 s. Diagnostic only.
- **The CAM API's autofocus flag for "AF Job" changed during the day:** False in the morning,
  True in the afternoon. The direct reader's `IsAutoFocusOnStart` agreed with the afternoon's.
  Why it changed is not known.
- **LAS X recalculates a stack with "System Optimized"** when it captures: a z-wide stack
  shown as 201 steps of 0.05 um was taken as 6 planes 2.41 um apart (to 2.04 um past the
  set end), and a z-galvo stack shown as 4.41 to 11.64 um was taken at 5.02 to 12.25 um. LAS
  X's own record of the capture agreed with the driver's report both times: the driver
  reports what was taken, not what the panel showed.
- **The Clear button stays enabled on the STELLARIS**, also on an empty canvas (it turned grey
  on the simulator). Its effect shows only in the cache, which the release check measures:
  the hidden copies shrank by 0.6 MB over ten captures.
- **Laser power:** `OutputPowerWatt` holds no real watts: "0" for the 405 nm laser, "85"
  beside `OutputPowerPercentage` 0.85 for the white light laser.
- **The bit-depth tests** (12 and 16 bit) were labelled right, but the pixels were photon
  counts of 0 to 4, so they did not show the full range coming through.
- **HiRes was at zoom 1.25** by the end, so its settings give 1.82 um per pixel where test 1
  (zoom 1) gave 2.27 um.

## Next: on the STELLARIS

`git fetch`, then `git reset --hard origin/readers-for-stellaris` in the clone (the history was
rewritten on 15 October), once the commits are pushed. Then:

- **Test 8**, a short run in the interface, if Thom wants it.
- **Step 3**, if a warning with only an OK button can be called up by hand.

## After the session

- **Done (15 October 2026, evening):**
  - releasing is on in the profile (`IMAGE_SAVE.cleanup_cache = True`, renamed from
    `cleanup_source`); the driver never deletes a file itself: a capture LAS X gives no way to let
    go of is refused before anything is saved;
  - **AutoSave is gone from the driver:** every capture is read from LAS X's cache
    (`output/lasx_native_autosave.py`, the profile's `source` and the connection status's
    AutoSave line are removed);
  - **the CAM API is nowhere the first choice for reading:** the end of a capture
    (`confirm_acquire`) is watched through the direct reader, and a job switch is decided by the
    direct reader alone when it answers (a stale log line could stop a needed switch before);
    the CAM API sends the commands, and reads only when the direct reader cannot answer;
  - these notes moved into `sessions/`.
- **Open:**
  - the hardware CI after these changes, whose captures check the end-of-capture watch;
  - merge into `release-candidate`;
  - then promote to `main` with the other repositories.

## Open points

- **"Can not start while live scan is running!"** came once on the simulator, 3 s after LAS X
  reported idle. Not explained; not seen again.
- **Comments that predate the microscope:** `output/lasx_description.py` still says a job with
  several sequential settings, and a z-wide stack, have "not been seen on a microscope yet";
  both were seen and read right on 15 October.
- **Lint:** `ruff check` finds 293 points and `ruff format` would rewrite 145 files. Lint stays
  non-fatal in the CI; cleaning it up is a large change of style only.
- **`testing/unit/test_state_readers.py`** sometimes fails when run together with other test
  files, in different tests each time, also before this session's changes; on its own it
  passes. An order effect between test files, not yet found.
- **The simulator PC's machine limits** (`limits.json` from July) lack newer entries, so runs
  there use the driver's bundled default limits.
- **The cache folder** is the one the registry names: `D:\Temp2` on the STELLARIS, `D:\Temp` on
  the simulator PC.
- **`DCLogFile.log`**: LAS X's Data Container client writes it into the folder a script is
  started from. Start scripts from a reports folder (`D:\ZMART-check` on the STELLARIS).

## How things are run

- **On the STELLARIS:** the clone `C:\Users\t.de\conda\repos\zmart-drivers-readers-for-stellaris`,
  environment `C:\Users\t.de\conda\zmart-drivers-readers` (the ZMART controller is installed in
  it), started from `D:\ZMART-check`; the data location is `D:\ZMART-data`.
  ```
  python <clone>\zmart_drivers\leica\stellaris5_y42h93\navigator_expert\testing\run_ci.py                    # offline
  python <clone>\zmart_drivers\leica\stellaris5_y42h93\navigator_expert\testing\run_ci.py --hardware --full  # live
  ```
- **On the simulator PC:** offline tests from `testing/`, environment `zmart-integration`, with
  `TMP`/`TEMP` on D: and the controller clone on `PYTHONPATH` (that environment has an older
  controller installed):
  ```
  set PYTHONPATH=<home>\ZMART-controller
  python -m pytest -q -p no:cacheprovider --ignore=hardware unit calibration
  ```
  After LAS X initialises the stage, it stands at 0, 0, outside its limits; the CI's first step
  moves it to the baseline.

## Ground rules for this branch

- Commits by Thom de Hoog alone (`thomdehoog@gmail.com`), with no assistant lines.
- Commit with an explicit pathspec; push only when Thom says so.
- This is a public branch: it holds code that calls LAS X, and nothing about how LAS X is
  built inside.
- Test first: a failing test, then the change, then the targeted tests; the full suite once at
  the end.
