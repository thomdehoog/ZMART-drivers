# On the STELLARIS: the re-run (task sheet for a session at the microscope)

You are at the STELLARIS PC with Thom. Work through this sheet from top to bottom, one step at
a time. Tell Thom what you are about to do before every step that moves the stage or captures,
and wait for his go. Background: [`NEXT_SESSION.md`](NEXT_SESSION.md) (where the branch stands)
and [`SCOPE_SESSION_STELLARIS.md`](SCOPE_SESSION_STELLARIS.md) (the full session sheet).

## Why a re-run

On 15 October steps 1, 2 and 5 of the session sheet passed on this microscope. Two changes came
after that run and have only been seen on the LAS X simulator:

- **Presses through UI Automation no longer bring LAS X to the front.** The client is
  Microsoft's `CUIAutomation8` with `AutoSetFocus` off (`vendor_interface/lasx_dialogs.py`).
  Before, every Clear and every Jobs/Experiment switch put LAS X in front of the operator's
  window.
- **A picture is new when its pixel file was written since the capture began**
  (`output/lasx_data_container.py`). LAS X writes a picture's description again about 4 s after
  the capture; with two captures of one job close together, the older one was taken for new.

Steps 3 and 4 were not done.

## 0. Bring the clone up to date

The branch's history was rewritten on 15 October (the commit messages were cleaned; the code is
the same), so a plain `git pull` will refuse. In the `ZMART-drivers` clone in the home folder:

1. `git status`: if it shows changed files, stop and ask Thom what to do with them.
2. `git fetch`, then `git switch readers-for-stellaris`, then
   `git reset --hard origin/readers-for-stellaris`.
3. `git log --oneline -1` must show the newest commit of the branch on GitHub.

## 1. The CI, with another window in front (about 3 minutes)

Before: AutoSave off, Navigator Expert open, at least two jobs that are not autofocus jobs, the
stage clear (the CI first goes to X 63,500 um, Y 41,500 um, z-wide 0, z-galvo 0). Ask Thom to
click another window (a browser, this terminal), so that **LAS X is not in front**, and to keep
his hands off for the run.

```
python <driver>\testing\run_ci.py --hardware
```

`<driver>` is `ZMART-drivers\zmart_drivers\leica\stellaris\navigator_expert`; run it
in the driver's environment, with `ZMART-controller` on `PYTHONPATH` if it is not installed.

**Pass:** all seven steps `OK`, and **LAS X stayed behind the other window the whole time**
(ask Thom: did it come forward, even for a moment?). Search the output for `| FAIL |`,
`| WARN |` and `Traceback`: there should be none.

## 2. Step 4 of the session sheet: saving straight from the cache

The tests of [`SCOPE_CHECK_CACHE_ROUTE.md`](SCOPE_CHECK_CACHE_ROUTE.md), one at a time, each with
its own `--label`. **Changed since that sheet was written:** `check_cache_route.py` no longer
compares the capture with AutoSave's file (commit "on the STELLARIS, the cache route asks
nothing of AutoSave"). So:

- AutoSave can stay off; leave out test 6 (switching AutoSave off).
- Ignore the sheet's "identical to AutoSave" column. Check instead what the report says
  against what LAS X shows: bit depth (tests 1 and 2), both channels and the plane heights of a
  stack (tests 3 and 4), each channel's own laser and detector (test 5), the laser power
  fields (test 7).
- For the stack tests, the output must not contain `save failed; not parsing the stale on-disk
  LRP`, nor `the experiment save failed - no current Z available`.
- Test 8 (a short run in the interface) only if Thom wants it today.

Afterwards, bring `SCOPE_CHECK_CACHE_ROUTE.md` and step 4 of `SCOPE_SESSION_STELLARIS.md` in
line with the script (no AutoSave comparison), as a separate commit.

## 3. Step 3 of the session sheet: LAS X's warnings (only if possible)

Only if Thom knows a warning that LAS X shows with **only an OK button** and that he can call up
by hand. Follow step 3 of the session sheet (`watch_lasx_warnings.py --look`, then without
`--look`). If no such warning can be called up, write that down and skip it.

## What to report to Thom

- the CI summary from 1, and whether LAS X came forward;
- per test of 2, its label, `ALL CHECKS PASSED` or what failed, and any value that differs
  from what LAS X shows;
- 3: done (and what was printed) or skipped (and why).

If everything passes, the next step is Thom's decision: switch releasing on in the profile
(`IMAGE_SAVE.cleanup_cache = True`) and merge the branch into `release-candidate`.

## Rules for this session

- **Commits:** by Thom de Hoog (`thomdehoog@gmail.com`) alone, with no assistant lines (no
  "Co-Authored-By", no session links); commit with an explicit pathspec.
- **Pushing:** only when Thom says so.
- **This is a public branch:** it holds code that calls LAS X, and nothing about how LAS X is
  built inside.
- **Fixes:** a failing test first, then the change, then the targeted tests; the full suite
  once at the end.
- **The cache folder** on this microscope is `D:\Temp2` (the one the registry names).
