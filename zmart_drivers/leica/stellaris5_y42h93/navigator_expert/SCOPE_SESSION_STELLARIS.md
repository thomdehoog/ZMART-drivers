# On the STELLARIS: the readers, LAS X's warnings, and letting go of the cache

The branch `readers-for-stellaris` brings four changes to the driver. Each was seen working on
the LAS X simulator (14 October 2026), never yet on the microscope:

- **The job settings come from LAS X's own JSON answers.** The direct reader now reads every
  job's settings and the hardware information as LAS X writes them for the CAM API, so it also
  reads the settings of jobs other than the selected one, and a filter wheel's spectrum mode.
- **A warning no longer stops a run.** When LAS X shows a warning, its hardware server waits
  until someone clicks it; one warning held it for 33 hours on 13 October. The driver now reads
  each dialog from `LAS X.log`. A warning whose only button is OK is answered and its text is
  written to the driver's log. Any other dialog stays open for you, and the command that met it
  is refused with the dialog's text.
- **The cache release also clears the Navigator canvas.** Every capture leaves a hidden copy
  in LAS X's cache (`TileScan <n>`, 1.3 to 2 times the capture's pixels), held by the Navigator
  canvas. Releasing a capture (`cleanup_source`) now also presses the canvas's Clear button,
  which lets go of all of them. Clear works only while LAS X shows the canvas (Navigator Expert's
  Experiment tab), so before each capture the driver shows that tab when another one is shown. Releasing is set in the driver's profile (`IMAGE_SAVE`) and stays
  off until this session has passed; the check scripts below switch it on for their own run.
- **Saving the experiment waits for the `.lrp` itself**, the file the driver reads the job's
  z-stack from.

One session of about an hour runs all four. Steps 2, 3 and 5 only read, or capture where the
stage already stands.

---

## Before the session

1. **Get the branch** on the microscope PC, in the local clone in the home folder (never from
   Z:): `git fetch` and `git switch readers-for-stellaris` in `ZMART-drivers`. `ZMART-controller`
   stays on `release-candidate`.
2. **Install comtypes** in the driver's environment, from conda-forge:
   `conda install -c conda-forge comtypes`. Without it the driver still works, but it answers no
   warning and clears no canvas; its log then says "Windows UI Automation is not available".
3. **The data location** must be set up
   (`configuration/data_location/notebooks/set_data_location.ipynb`), as for
   [`SCOPE_CHECK_CACHE_ROUTE.md`](SCOPE_CHECK_CACHE_ROUTE.md).
4. **In LAS X:** Navigator Expert open on its **Experiment** tab, which shows the Navigator
   canvas with its Clear button (not the Jobs tab). **AutoSave off**: the cache route is used
   without it, and steps 1 and 5 check that it is off. Only step 4 switches it on.
5. **A folder for the reports**, for example `D:\ZMART-check`. Run every command from it, in
   the driver's environment. `<driver>` stands for
   `ZMART-drivers\zmart_drivers\leica\stellaris5_y42h93\navigator_expert`.

---

## Step 1. The CI against the microscope

```
python <driver>\testing\run_ci.py --hardware
```

It runs the installed driver, a move and a capture, every reversible setting through the direct
readers, one capture saved from LAS X's cache and checked, and ten captures let go of from the
cache with the Navigator canvas cleared, all with AutoSave off. Before each capture the driver
shows Navigator Expert's Experiment tab by itself, when another tab is shown.
Add `--job <name>` when the job to capture is not called HiRes.

**Look at:**

- Everything is green. If a cache step fails with "AutoSave is on", switch AutoSave off; if
  the last step fails with "Experiment button was not found", open Navigator Expert; then run
  again.
- The first lines: if they say the machine's `limits.json` "did not validate", the session runs on
  the bundled default limits. Note it; the limits are then published again with
  `configuration/limits/notebooks/set_limits.ipynb`.

## Step 2. The three readers side by side (about 1 minute, reads only)

```
python <driver>\testing\hardware\benchmark_readers.py --out D:\ZMART-check\readers --job HiRes
python <driver>\testing\hardware\benchmark_report.py D:\ZMART-check\readers > readers.md
```

Then run it once more with `--job` set to a job that is **not** selected in LAS X (for example
`"AF Job"`), and `--out D:\ZMART-check\readers-other`.

**Look at, in `readers.md`:**

- **Readers at rest:** no failed rounds for `direct`, and how long one round takes for each
  reader.
- **Agreement:** a value in bold is one where a reader differs from the direct reader. For the
  unselected job, the direct and api columns should agree; that is the new JSON reading.

## Step 3. LAS X's warnings (about 10 minutes)

For this step you need a warning that LAS X shows with **only an OK button**, which you can
call up by hand. Each part watches for 5 minutes; call the warning up during that time.

```
python <driver>\testing\hardware\watch_lasx_warnings.py --look --minutes 5
```

`--look` presses nothing. **Look at:**

- When the warning opens, the script prints `the log says: Warning: '<its text>'`.
- Next to it, `on screen: 1 dialog window(s), buttons [('ButtonMessageOk',)]`.
- Close the warning yourself.

```
python <driver>\testing\hardware\watch_lasx_warnings.py --minutes 5
```

Now the driver watches, as it does once it is connected. **Look at:**

- Call up the same warning: within about a second it closes by itself.
- The script prints `LAS X warning answered with OK: '<its text>'`.
- A dialog with any other button stays open and is printed as `not answered`.

If `--look` finds the warning in the log but `0 dialog window(s)` on screen, the microscope's
dialog windows are built differently from the simulator's. Take a screenshot of the dialog and
note what was printed.

## Step 4. Saving straight from the cache (as planned)

Tests 1 to 8 of [`SCOPE_CHECK_CACHE_ROUTE.md`](SCOPE_CHECK_CACHE_ROUTE.md), unchanged. These
compare each capture with AutoSave's file, so **switch AutoSave on** for them, into a project
of its own; test 6 switches it off again. Leave it off for step 5.

For a job with a z-stack (tests 3 and 4), the driver reads the stack's z position from the
experiment it saves, which is the `.lrp` save fix. **Look at:** the output must not contain
`save failed; not parsing the stale on-disk LRP`, nor `the experiment save failed - no current Z
available`.

## Step 5. Letting go of the cache over many captures (about 5 minutes)

With AutoSave off:

```
python <driver>\testing\hardware\check_cache_release.py --job HiRes --captures 50 --label hires-50 --autosave-off
```

Every ten captures it prints how much LAS X's cache grew: in total, the hidden copies, and the
rest. **Look at:**

- The last line says `ALL CHECKS PASSED`, which means every capture was saved and the canvas
  was cleared at every release.
- The hidden copies stay near +0 MB. On the simulator, about 0.2 MB of descriptions per capture
  stays in the cache while LAS X's window holds it; compare the `rest` per capture with that.

Then once starting from **the Jobs tab** in Navigator Expert:

```
python <driver>\testing\hardware\check_cache_release.py --job HiRes --captures 10 --label from-jobs-tab --autosave-off
```

At the first capture the screen switches to the Experiment tab by itself (it did so cleanly on
the simulator), and the run passes like the first. If it says `FAILED: the canvas cleared at
every release`, note what the screen showed and what the output says after "could not be
shown".

---

## What to send back

- the run_ci summary from step 1
- `readers.md` and `readers-other.md` from step 2
- what step 3 printed, and the warning you used
- the `check_cache_route_*.json` reports (step 4)
- `check_cache_release_hires-50.json` and `check_cache_release_from-jobs-tab.json` (step 5)

If all of it passes, the branch is ready to go to `release-candidate`.
