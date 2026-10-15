# Next session: branch `readers-for-stellaris`

Where this branch stands (15 October 2026) and what to do next. Read this first, then
[`SCOPE_SESSION_STELLARIS.md`](SCOPE_SESSION_STELLARIS.md).

## Where it stands

- **Branch** `readers-for-stellaris` on `ZMART-drivers`, from `release-candidate` at `74b3451`,
  pushed.
- **On the STELLARIS (15 October 2026, `6dc3752`), steps 1, 2 and 5 of the session sheet
  passed:** the hardware CI all green; the three readers agree with no failed round; 50 and
  then 10 captures let go of from the cache, the canvas cleared every time.
  - **Step 3 (LAS X's warnings) is not done:** no warning with only an OK button could be
    called up by hand.
  - **Step 4 (the cache route checks against AutoSave's files) is not done.**
- **On the LAS X simulator, everything is green** (`run_ci.py --hardware`, all seven steps);
  offline, 1588 tests (`unit` and `calibration`) and the hardware scripts' tests pass.

## What the branch brings

1. **Captures are read from LAS X's cache by default** (`IMAGE_SAVE.source = "cache"`), so
   AutoSave stays off. The connection status says AutoSave is not needed, instead of failed.
2. **The direct reader reads every job's settings and the hardware info** from LAS X's own
   JSON answers, the same as the CAM API returns: other jobs' settings, a filter wheel's
   spectrum mode.
3. **A LAS X warning with only an OK button is answered** and its text logged
   (`vendor_interface/lasx_dialogs.py`, Windows UI Automation through comtypes). Any other
   dialog stays open, and the command that met it is refused with the dialog's text.
4. **Letting go of LAS X's cache** (`output/lasx_cache_release.py`), set in the profile
   (`IMAGE_SAVE.cleanup_source`, off until the STELLARIS passes):
   - LAS X deletes the capture once it is saved and read back identical;
   - the Navigator canvas is cleared, which frees the hidden copies it holds (`TileScan <n>`,
     1.3 to 2 times each capture's pixels);
   - before each such capture, while LAS X is idle, Navigator Expert's Experiment tab is shown
     if it is not, so that Clear can be pressed (`vendor_interface/lasx_navigator.py`). The
     canvas counts as shown when its Clear button can be reached; nothing is pressed when it
     already is;
   - a picture is new when its pixel file was written since the capture began: LAS X writes
     a picture's description again about 4 s after the capture, and keeps a released
     picture's description without its pixels;
   - presses through UI Automation do not bring LAS X to the front: the client is Microsoft's
     `CUIAutomation8` with `AutoSetFocus` off (by default UI Automation sets focus before a
     press, which brought LAS X forward at every Clear).
5. **Saving the experiment waits for the `.lrp`** the job's z-stack is read from.
6. **The CI** (`testing/run_ci.py --hardware`):
   - first takes the stage to the CI baseline (X 63,500 um, Y 41,500 um, z-wide 0, z-galvo 0);
   - runs the fatal end-to-end check on the direct readers, the driver's default (it used to
     force `hybrid`, so the direct readers were never checked end to end);
   - saves one capture from the cache and checks it, then lets go of ten. It does not ask
     whether AutoSave is on: LAS X writes the file that says so only when it closes, so the
     answer can be out of date;
   - needs no job names: it captures the job selected in LAS X unless `--job` names another,
     switches between the jobs that are not autofocus jobs, and picks a zoom inside the job's
     own range.

## Next: on the STELLARIS

`git pull` the branch in the local clone in the home folder, then, following
[`SCOPE_SESSION_STELLARIS.md`](SCOPE_SESSION_STELLARIS.md):

- **Step 1 once more,** `run_ci.py --hardware`, since the two fixes after the run (the front
  window, the pixel file's time) were seen on the simulator only. With another window in
  front, LAS X should stay behind it.
- **Step 4,** the cache route checks against AutoSave's files.
- **Step 3,** if a warning with only an OK button can be called up by hand.

## After the session

- **All passes:**
  - switch releasing on in the profile (`IMAGE_SAVE.cleanup_source = True`);
  - merge into `release-candidate`;
  - then promote to `main` with the other repositories.
- **Something fails:** the sheet says what to note per step. Most likely to differ on the
  microscope, as they were only seen on the simulator:
  - the paths to Navigator Expert's Clear and Experiment buttons (the message names the
    step not found);
  - the shape of the warning dialogs;
  - the direct readers on real hardware, also during stage moves.

## Open points

- **"Can not start while live scan is running!"** came once on the simulator, 3 s after LAS X
  reported idle, during 10 captures right after a project was deleted. Not explained; not seen
  again.
- **The simulator PC's machine limits** (`limits.json` from July) lack newer entries, so runs
  there use the driver's bundled default limits.
- **The cache folder** is the one the registry names: `D:\Temp2` on the STELLARIS, `D:\Temp` on
  the simulator PC.
- **The test environment `zmart-integration`** has an older ZMART controller installed. Put
  the controller clone on `PYTHONPATH` for the tests, or a few controller-contract tests fail.
- **`DCLogFile.log`**: LAS X's Data Container client writes it into the folder a script is
  started from; one sits untracked in this driver folder. Not committed.

## How things are run

- **Offline tests**, from `testing/`, environment `zmart-integration`, with `TMP`/`TEMP` on D:
  ```
  set PYTHONPATH=<home>\ZMART-controller
  python -m pytest -q -p no:cacheprovider --ignore=hardware unit calibration
  ```
- **Live**, environment `zmart-microscopy` (pythonnet, pywin32, comtypes):
  ```
  set PYTHONPATH=<ZMART-drivers clone>;<home>\ZMART-controller
  python testing\run_ci.py --hardware
  ```
- **On the simulator:**
  - the data location is set to a scratch folder on D:;
  - after LAS X initialises the stage, the stage stands at 0, 0, outside its limits; the
    CI's first step moves it to the baseline.

## Ground rules for this branch

- Commits by Thom de Hoog alone (`thomdehoog@gmail.com`), with no assistant lines.
- Commit with an explicit pathspec; push only when Thom says so.
- This is a public branch: it holds code that calls LAS X, and nothing about how LAS X is
  built inside.
- Test first: a failing test, then the change, then the targeted tests; the full suite once at
  the end.
