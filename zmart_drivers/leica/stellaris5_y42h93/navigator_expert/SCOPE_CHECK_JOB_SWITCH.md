# On the microscope: why one job switch waited 12 s

The full run on the STELLARIS on 2026-10-10 ([`SCOPE_RUN_2026-10-10.md`](SCOPE_RUN_2026-10-10.md),
section 3.3) passed, but one job switch, `Overview` to `AF Job` in the adapter step, waited 12.6 s
before the `SelectJob` command went out. 12 s is one full waiting budget: four windows of three
seconds. So one reading made before the switch got no answer at all, and the driver went on anyway.
These are the steps to find out which reading it was, in order of effort. Only step 2 and step 3
move anything.

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
