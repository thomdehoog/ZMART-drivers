# On the microscope: why one job switch waited 12 s

The full run on the STELLARIS on 2026-10-10 ([`SCOPE_RUN_2026-10-10.md`](SCOPE_RUN_2026-10-10.md),
section 3.3) passed, but one job switch, `Overview` to `AF Job` in the adapter step, waited 12.6 s
before the `SelectJob` command went out. 12 s is one full waiting budget: four windows of three
seconds. So one reading made before the switch got no answer at all, and the driver went on anyway.
These are the steps to find out which reading it was, in order of effort. Only step 2 and step 3
move anything.

> **Answered on the microscope, 2026-10-10 (late evening).** The steps below were run, and the
> answer is not a reading that got no answer. It is the log parser being run by dozens of threads at
> once. See [What was found](#what-was-found) at the end.

---

## What the switch reads before the command goes out

Measured on the LAS X simulator with the probe of step 3: each reading answers in under 0.1 s there,
and the switch takes about 0.7 s.

| Reading | If it gets no answer for 12 s |
|---|---|
| the selected job (API and log race) | unknown: the job is selected anyway |
| the job list (API) | the job looks unknown: the switch is refused |
| the job list again (API), the baseline for the switch confirmation | unknown: the switch is sent anyway |
| the stage position | the switch is refused (the objective compensation needs it) |
| the current job's settings | the objective slot is unknown: the switch is sent anyway |
| the current job's z-wide | the switch is refused |
| the scanner status (the idle check, API only) | the switch is refused |

The switch on the microscope passed, so it was one of the three that carry on. The likeliest is the
selected job: on the real microscope the API's answer to "which job is selected" is known to lag
(`actions/profiles.py`), and a log line about the selected job counts for only 2 s.

A reading that gets no answer writes a warning that names it, for example:

```
selected_job: no answer counted as a success in 4 windows of 3s
```

---

## Step 1. Look in the reports of the run you already made

No LAS X needed, nothing moves. In the driver folder on the microscope PC
(`zmart_drivers\leica\stellaris5_y42h93\navigator_expert`), in PowerShell:

```powershell
Select-String -Path testing\_report\* -Pattern "no answer counted"
```

A line timed between 23:26:42 and 23:26:54 names the reading. If the reports hold none, the console
output of that run may: look for the same text around those times. If either names the reading,
that answers the question; steps 2 and 3 are not needed.

## Step 2. The quick acceptance on the latest commit

About five minutes. The stage must be clear, as for every hardware run. From the repository root:

```powershell
git pull
python zmart_drivers\leica\stellaris5_y42h93\navigator_expert\testing\run_ci.py --hardware
```

This also covers the commit after the full run (`c1c0092`: the scanner status races the API against
the log again, and the idle check asks the API only), which the full run did not include. Its adapter
step switches jobs the same way, so look at the time of `set_state: switch job` in the output. If it
is about 12 s again, run step 1 on the new reports.

## Step 3. The timing probe, only if the wait comes back unexplained

The probe switches from the selected job to every other job and back, through the driver the way a
workflow does, and prints every reading the switch makes with its source and duration. A reading
that got no answer shows `NONE` and about 12 s. It ends on the job it started with and acquires
nothing, but a job that uses another objective makes the driver move the stage to keep the sample
point, so the stage must be clear. From the repository root:

```powershell
python zmart_drivers\leica\stellaris5_y42h93\navigator_expert\testing\hardware\probe_job_switch_timing.py --yes --rounds 3
```

A line looks like this:

```
  [   2.55s] selected_job  job=None       mode=None   -> log   in  0.06s
```

The reading that took about 12 s with `NONE` is the one. Note which job the switch went to and from.

---

## What to send back

- the line or lines naming the reading (step 1), or the probe's output (step 3);
- the time of `set_state: switch job` in the step 2 run.

If it is the selected job, the switch was correct all along: it was sent and LAS X confirmed it.
What it costs is the 12 s, and whether the "is a switch needed?" check should wait the full rule is
then the decision to make.

---

## What was found

Run on the STELLARIS on 2026-10-10, 23:45 to 00:05, on `b085022` (the branch head with this
document). Everything below was measured, nothing is guessed.

### The three steps

- **Step 1.** The reports hold no "no answer counted" line. The console output of the full run holds
  exactly one, `selected_job: no answer counted as a success in 4 windows of 3s`, but it came from
  the passive reader probe step at 23:25, not from the adapter step at 23:26. So it shows that a
  routed selected-job read can wait out its budget on this scope, but it does not explain the switch.
- **Step 2.** `run_ci.py --hardware` on `b085022`: PASSED, all eight acceptance points. The adapter
  step's `set_state: switch job` took **10.5 s** again (12.6 s in the full run). The driver log shows
  nothing at all between the last restore move at 23:50:55.97 and the `SelectJob` command at
  23:51:06.1; the command itself was confirmed from the log in 0.35 s.
- **Step 3.** `probe_job_switch_timing.py --yes --rounds 3`: twelve switches, every one in
  **0.84 to 1.05 s**, every reading answered from the API in under 0.12 s, no `NONE`. The probe does
  not reproduce the wait.

So the wait is not in the switch itself. It is in what comes just before it in the validator: a
burst of stage moves. The validator switches straight after the move phase, which does the XY
pattern and the z round trips, about fifteen moves in a few seconds. The probe switches from rest.

### The reproduction

`testing/hardware/probe_switch_after_moves.py` (added with this section) does ten XY moves of 25 um,
then a switch, with the readings timer, a timer on the log parser and the pre-fire checks, and a
count of live threads. Three runs on the scope:

| | Switch after the burst | Switch back, from rest |
|---|---:|---:|
| run 1 | 9.83 s | 0.92 s |
| run 2 | 10.61 s | 0.89 s |
| run 3 | 8.25 s | 0.89 s |

In every run the whole wait sat inside one call, `_selected_job_name_from_log` in
`actions/confirm_select_job.py`, which `prepare_select_job` makes before the API baseline read: it
took 9.86 s, then 7.47 s, where it takes 0.1 s from rest. It is a single, synchronous
`log_reader.parse_log()`.

The thread count tells why:

```
[   1.20s] --- burst of 10 XY moves                [threads alive: 2]
[   7.03s] --- moves done                          [threads alive: 57]
[   7.08s] --- switch to 'AF Job'                  [threads alive: 60]
  [   7.11s] SLOW confirm_select_job._selected_job_name_from_log took  7.47s
[  15.33s] --- switch back                         [threads alive: 1]
```

During the burst, 65 calls to `parse_log` took 9.9 to 10.8 s each, all started between 3.9 s and
5.6 s and all finished together at about 15.5 s. They are the log legs of the readings' races in
`dispatcher/read.py`: every move makes five or six readings (position, selected job, job settings,
idle check), each `_watch` starts a `_log_worker` thread, and each poll of that worker is a full
`parse_log`. When the API wins the race `stop.set()` is called, but a worker already inside
`parse_log` runs that parse to the end. One parse reads the last 4 MB of `lcsCommand.log` (17.2 MB
on this PC) and runs regexes over it: 0.095 s alone, CPU-bound, under the GIL. Thirty parses started
at once take 4.5 s in total. Moves come every 50 to 100 ms, so new log legs start faster than the old
ones can finish their parse, the parses slow each other down, and the pile grows for as long as the
burst lasts. The switch's own synchronous parse then queues behind sixty of them.

In short: the readings' log legs are stopped by flag, not interrupted, and one poll is a 0.1 s
CPU-bound parse of 4 MB. Under a burst of commands the abandoned polls pile up and starve the next
synchronous log read. Nothing was wrong with LAS X, and no reading went unanswered in the switch.

### What this touches

- Any synchronous log read right after a burst of commands: the pre-fire check of a job switch,
  and any reading whose log leg has to finish a parse before its API answer can be taken. The
  validators' per-command timings during the burst looked fine because the API leg won each race
  while the log legs were still parsing in the background.
- The decision from section 7 of `WAITING_PLAN.md`, `POLL_S` = 0.1 s chosen from a 66 ms parse: that
  measurement was one parse at a time. With concurrent legs the effective cost is far higher.

### Not done here

No driver code was changed. The obvious places to look, for Thom to decide: let `_log_worker`
check `stop` before each parse and skip the parse when the API has already won; share one parsed
snapshot between concurrent log legs (one parse per `POLL_S`, not one per leg); or make
`_tail_lines` read far less than 4 MB, since the readings only need the last few seconds of the log.

---

## The fix, and what to check on the microscope

Built on 2026-10-11 from the places listed above. Measured on the LAS X simulator, not yet on the
microscope.

### What changed

- **One parse of the log at a time, shared by readers asking together** (`log_reader.SharedParse`).
  However many readings race, only one parse runs at any moment; readers that ask while it runs wait
  for it to end and share the next one. A reader only ever gets a parse begun after it asked: a first
  version that reused a snapshot for 0.1 s handed out the position from just before a move, fresh by
  its line's age, and it won the race (the simulator's adapter step read 0 instead of 25 um after a
  move). A snapshot counts only for the two log files it was parsed from. The tail the reader reads
  stays 4 MB: job settings and the job list can be older than the last few seconds.
- **A race that is already won starts no new parse** (`_log_worker` checks before each one).
- **A question already sent to LAS X is seen through** to its answer or the window's end, even when
  the log wins meanwhile. With the shared snapshot the log wins most races at once, and abandoning
  the API question just sent, every time, made the simulator drop the next question under a burst:
  one reading in a few then waited a whole window, 5 s. Once a race is won, no new question is sent.

### Measured on the simulator

`probe_switch_after_moves.py --yes`: ten small XY moves, then a job switch, then back.

| | Threads alive after the burst | Switch after the burst | Switch back |
|---|---:|---:|---:|
| before the fix, 3 runs | 4, 73, 66 | 1.05 s, **9.34 s**, **11.62 s** | 1.0 to 1.3 s |
| after the fix, 3 runs | 2 | 0.75 to 0.88 s | 1.09 to 1.17 s |

After the fix no reading took a second or more, and no question to LAS X went unanswered. The
adapter validator passed with 70 checks, its `set_state: switch job` in 1.0 s.

### On the microscope

The stage must be clear: the probe moves the stage by 25 um and back, and a job switch can move it
to keep the sample point. Nothing is acquired. From the repository root, LAS X running:

```powershell
git pull
python zmart_drivers\leica\stellaris5_y42h93\navigator_expert\testing\hardware\probe_switch_after_moves.py --yes
```

Run it three times. On the microscope it showed, before the fix: about sixty threads alive after the
burst, and the switch after it taking 8 to 11 s. What to look for now:

- `threads alive` after `moves done`: a handful, not sixty;
- the switch after the burst: about a second, like the switch back;
- no `SLOW` line naming `_selected_job_name_from_log` or `parse_log`.

Then the quick acceptance, which runs the same switch straight after its own move phase:

```powershell
python zmart_drivers\leica\stellaris5_y42h93\navigator_expert\testing\run_ci.py --hardware
```

It should pass all eight acceptance points, with `set_state: switch job` in the adapter step taking
about a second instead of 10 to 12 s.

### What to send back

- the three probe runs' lines for `moves done`, the two switches, and any `SLOW` line;
- the time of `set_state: switch job` in the `run_ci.py --hardware` run, and its result.
