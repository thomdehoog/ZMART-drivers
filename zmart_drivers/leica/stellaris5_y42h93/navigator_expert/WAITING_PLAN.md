# Plan: one way to wait on LAS X (`navigator_expert`)

- **Branch:** `leica-anatomy` at `ad28266`
- **Date:** 2026-10-10
- **Status:** plan only. No driver code has changed.
- **Decided by Thom:** one rule everywhere, four windows of three seconds ("4x3"). Within a window the first answer that counts as a success wins, from whichever source gives it. No source is preferred and nothing has its own timeout. One poll interval for every wait on LAS X. No pause between delivery tries. The idle check is an ordinary reading under the same rule. Acquiring is never repeated and keeps its own handling; the error report after a command stays at 1 s. The settings read while saving waits 4x3 too.

---

## 1. Why: what the simulator run showed

The full hardware CI (`run_ci.py --hardware --full`) on the LAS X simulator failed in two places. Both were reproduced afterwards with a small script that only read state or switched jobs and switched back.

**1. A job switch is refused after LAS X confirmed it.** After a job switch the driver reads the job's objective, to move the stage if the lens changed (`actions/objective_shift.py:126`). In the run that reading came back empty, so the driver refused the switch (`objective_shift.py:168`). Reproduced four times in a row:

| Switch | The API's confirmation question | Settings read straight after |
|---|---|---|
| Overview → AF Job | still running, abandoned | **empty, after 0.06 s** |
| AF Job → Overview | finished | slot 0 (from the log) |
| Overview → AF Job | finished | slot 0 |
| AF Job → Overview | still running, abandoned | slot 0 (the log happened to be fresh) |

The log won the switch confirmation, so the API question was abandoned while still running. The driver allows one API question at a time per connection. The settings reading that followed found the API busy and **dropped the API entirely** instead of waiting for it (`dispatcher/read.py:300`, `api_pending = api_results is not None`). The log had nothing fresh for that job, so the reading ended empty, with no waiting at all. Both sources do carry the objective; on this simulator all three jobs use slot 0, so this switch did not even change the lens.

**2. The CI hangs forever in "log only" mode.** Before every command the driver waits for the scanner to be idle, with no time limit (`dispatcher/prechecks.py:27`, called with `timeout=None`). The comment there says the check always asks the API; the code uses the run's source setting. The log on this machine has no scanner status (`log: value=None`), so in log-only mode "idle" never comes. The run sat for 20 minutes at the job-switch round trip, twice.

**The common cause.** Readings and confirmations wait in different ways, with different numbers, and a reading counts any fresh answer as a success. Waiting on LAS X has six different rules for readings alone:

| Where | What | Today |
|---|---|---|
| `actions/profiles.py:89–130` | `xy_timeout_s`, `job_settings_timeout_s`, `jobs_timeout_s`, `selected_job_timeout_s`, `hardware_info_timeout_s`, `scan_status_timeout_s` | 2 s, one window |
| `actions/profiles.py:85` | `hybrid_log_grace_s` | the log may replace an API answer that came up to 0.25 s earlier |
| `actions/profiles.py:119–120`, `vendor_interface/log_wait.py:55` | `selected_job_log_confirm_timeout_s`, `selected_job_log_poll_timeout_s` | the log side of a job switch stops after 2 s inside a 3 s window; 5 s when called directly |
| `vendor_interface/api_reader.py:100,169,235,267,306` | `timeout=1.0, max_retries=3` per API question | 3 tries of 1 s, cut short by the 2 s |
| `actions/confirm_select_job.py:237,282` | `max_retries=1` | 1 try |
| `dispatcher/read.py:547`, `output/ome_canonical.py:34–35` | `get_job_settings_bounded`, `JOB_SETTINGS_READ_TIMEOUT_S = 1.0`, `JOB_SETTINGS_API_TIMEOUT_S = 0.25` | 1 s in total, one API try of 0.25 s (settings read while saving) |

Delivery of a message to LAS X (`UpdateAwaitReceipt`, `RECEIPT_TIMEOUT = 2`) has its own spread: 3 tries with 0.5 s pauses for commands (`dispatcher/change.py:104`), 2 tries for saving and loading the scan-field experiment (`scanfields/files.py:185,259`), 1 try in readings (`api_reader.py:143`), and a silently ignored try when handing over a job name (`api_reader.py:181`).

Saving the scan-field experiment has its own wait on top (`scanfields/files.py:150`): after delivery the driver waits up to 30 s for the experiment file to change, then for it to stop growing. Loading is delivered and never confirmed (`confirmed: False`).

How often the driver looks again is just as mixed:

| Interval | Where |
|---|---|
| 0.01 s (38 places) | most confirmations (`actions/confirmations.py`), the API questions (`vendor_interface/api_reader.py`), the error report after a command (`dispatcher/change.py:134`), the API side of a job switch |
| 0.1 s | the stage-move confirmation (`confirmations.py:1006`), acquisition (`confirmations.py:1071`, `profiles.py:445`), the log side of a job switch (`profiles.py:121`), saving the scan-field experiment (`scanfields/files.py:152`) |
| 0.05 s | the idle check (`dispatcher/prechecks.py:76`) |
| 0.005 s | the two races checking which source answered (`dispatcher/read.py`, `confirmations.py`): the driver checking its own threads, not asking LAS X |
| 0.5 s | the pause between delivery tries (`dispatcher/change.py:104`) |

Confirmations alone already follow 4x3 (`CONFIRM_POLL_S = 3` in `dispatcher/tuning.py:13`, `max_confirm_attempts = 4` in `actions/profiles.py:233`).

---

## 2. The rule

Every wait on LAS X, whether a reading, a confirmation or a delivery, is the same (the exceptions are listed in section 5):

1. **Four windows of three seconds.** `WINDOWS = 4` and `WINDOW_S = 3` in `dispatcher/tuning.py`, read at call time, with the poll intervals in section 2.1. No other number anywhere says how long to wait on LAS X or how often to look.
2. **Within a window both sources are watched.** The API gets one read request; when its answer comes back but is not a success, the read is requested again straight away. A busy API is waited for, never skipped. The log is read again every `POLL_S`. The driver's own bookkeeping (which source has answered) blocks on the answers instead of polling, so it needs no interval.
3. **The first answer that counts as a success wins.** No source is preferred. `hybrid_log_grace_s` goes.
4. **What counts as a success is defined once per reading** (section 4). A confirmation is the same reading with a stricter success: "shows the target value, observed after the command was sent".
5. **Between windows something is sent again; that is the point of 4x3.** A command is fired again, after waiting for idle (as today). A reading whose request got no answer in the window is requested again: LAS X sometimes drops a request, and today each API read is re-sent after 1 s for that reason (`max_retries=3`); under the rule the re-request happens at the window boundary, like every other re-send. A delivery is tried again. The one exception is acquisition, which is never sent again (section 5).
6. **After four windows:** the reading is unknown, the change is unconfirmed, the delivery failed. The result carries the last answer from each source and how old it was, as the diagnostics do today.

The source setting (`api`, `log`, `hybrid` in `StateReaderProfile`) stays. It says which sources take part, not how long to wait. In log-only mode a reading the log cannot give fails after 12 s instead of hanging.

### 2.1 Poll intervals

The terms used in the table below and in the rest of this plan:

- **Window:** 3 seconds. Every wait on LAS X has at most 4 windows, so 12 s at most.
- **Look again (`POLL_S`, proposed 0.1 s):** a new look at the source that shows the state. That is the LAS X log for readings and confirmations, and the experiment file for `save_experiment`. One read of the log took 64–71 ms (median 66 ms, 30 reads) on the simulator PC on 2026-10-10, so looking faster gains little and keeps a CPU core busy re-reading the log. To be confirmed on the scope PC (section 7, step 0).
- **Check for the answer (`ANSWER_POLL_S`, proposed 0.01 s, as today):** see whether a request already sent has been answered, a field on the local API object. One API read took 60–80 ms on the simulator; at 0.1 s a 65 ms answer would be noticed at 100 ms.
- **Send again:** what happens between windows when there is no success yet.

Both poll intervals live in `dispatcher/tuning.py` next to `WINDOWS` and `WINDOW_S`.

| What waits | Look again | Check for the answer | Send again between windows | Longest wait | Today |
|---|---|---|---|---|---|
| **Reading** (position, job settings, job list, selected job, hardware info, scanner status) | 0.1 s | 0.01 s | request the read again | 4 windows (12 s) | 1 window of 2 s |
| **Confirming a command** (setting, move, job switch) | 0.1 s | 0.01 s | fire the command again | 4 windows (12 s) | 4 windows (12 s); the log side of a job switch stops at 2 s |
| **Idle check before a command** | 0.1 s | 0.01 s | request the read again | 4 windows (12 s) | no limit |
| **Delivering a message** to LAS X | — | each try waits for "received" | deliver again | 4 windows (12 s) | 3 tries of 2 s, 0.5 s pauses |
| **`save_experiment`: has LAS X started saving** | 0.1 s (the file) | — | send the save again | 4 windows (12 s) | 30 s |
| **Error report after a command** | — | 0.01 s | nothing | 1 s | unchanged |
| **Acquisition** (started, running, finished) | 0.1 s | — | **never** | 15 s to start, then until finished | unchanged |
| **Files being written** | 0.5 s | — | nothing | until the file stops growing | unchanged |

An API read is requested once per window, and again within the window whenever its answer is not a success. "Each try waits for received" means `UpdateAwaitReceipt` itself blocks until LAS X answers "received" or the window ends; the driver checks nothing in between. Sending the save again is safe: it writes the same file again. The driver's own bookkeeping (which source has answered) has no interval: it waits for an answer to arrive.

---

## 3. The mechanism

One function in `dispatcher/read.py`:

```python
def wait_for(datum, client, *, accept=None, between=None, job_name=None,
             observed_after=None, mode=None) -> Reading
```

- `datum` names a row of the readings table in `actions/get.py`.
- `accept(value) -> bool` narrows success for this caller. `None` means the row's own success rule (section 4). A caller's `accept` is applied on top of the row's rule, never instead of it.
- `observed_after` (a time) rejects answers observed before it. Confirmations pass the moment the command was sent; this is the gate `_reading_value_after` and `_readback` already apply by hand.
- `between()` runs between windows. The dispatcher passes "wait for idle, fire again" for confirmations; for a reading it is the re-request of an unanswered read, which `wait_for` does itself.
- It returns a `Reading` (value, source, observed_at, age_s, error) whose value is `None` when no source succeeded in four windows. The existing `diagnostics=` switch keeps working on top.

**What it replaces.** `_route_read` and `_log_rescue_concurrent` (`dispatcher/read.py:225–349`) and `_capped_api_read` go. The routed readers (`get_xy`, `get_job_settings`, …) keep their names and signatures, minus `timeout`/`poll_interval`/`max_retries`, and become one-line calls to `wait_for`. Callers outside `dispatcher/` do not change.

**The API side.** Each function in `vendor_interface/api_reader.py` makes one read request: deliver, then check for the answer every `ANSWER_POLL_S` until a deadline the caller passes in (the end of the current window). Its own `timeout`/`max_retries` go. A request still unanswered at the end of the window is given up and the next window requests the read again; because the request's own check stops at the window's end, it frees the API for the next one. The one-request-at-a-time rule per connection stays (it guards LAS X's shared reply fields); `wait_for` waits for the slot instead of giving up on it.

**The log side.** `log_wait.wait_for_selected_job_log` stops owning a timeout; its "what counts as a match" check becomes the `selected_job` row's confirmation rule, and the window is `wait_for`'s. `selected_job_log_confirm_timeout_s` (2 s), `selected_job_log_poll_timeout_s` (5 s) and `selected_job_log_poll_interval_s` (0.1 s) are removed from `StateReaderProfile`. No reason for the 2 s cut-off is recorded in the code or its history in this repository; it predates the import on 2026-09-30.

**Confirmations.** `race_confirmations` (`actions/confirmations.py:66`) and the per-setting poll loops (`_confirm_readback` and the bespoke `_confirm_*`) become calls to `wait_for("job_settings", accept=<target check>, observed_after=command_sent, between=refire)`. The window loop in `confirm_and_fire` (`dispatcher/change.py:651`) moves into `wait_for`; the dispatcher keeps what it owns today (fire block, refire, `refire_on_unconfirmed`, timing, result shape). This is the larger half of the work and comes after the readings (section 7).

**Delivery.** `_fire_with_receipt` becomes four tries of `UpdateAwaitReceipt(WINDOW_S)`, no pause between: each try already waits up to a full window for LAS X to say "received", so a pause adds nothing. `scanfields/files.py` uses it instead of its own two tries. Inside a reading, delivery happens within the current window. A failed job-name handover (`api_reader.py:181`) fails that try of the reading instead of being ignored.

**The idle check.** `check_idle` (`dispatcher/prechecks.py:27`) becomes `wait_for("scan_status", accept=is_idle)`: 4x3, `POLL_S`, through the run's source setting. Its own `timeout`, the 0.05 s sleep and the separate heartbeat go; the window checkpoints are the heartbeat.

**The settings read while saving.** `output/ome_canonical.py:135` reads the job's settings to write the physical sampling into the saved files. Today it gives up after 1 s (`JOB_SETTINGS_READ_TIMEOUT_S`) with one 0.25 s API try (`JOB_SETTINGS_API_TIMEOUT_S`) and then keeps LAS X's own values, including the z-spacing its own comment calls known-wrong for native AutoSave. Under the rule it calls `get_job_settings` like every other caller: 4x3. `get_job_settings_bounded` and both constants are removed. A save can then wait up to 12 s for the settings, and a slow LAS X no longer silently costs correct metadata.

**Saving the scan-field experiment.** "Has LAS X started saving" (the file's modification time changes) is a wait on LAS X and moves under 4x3. "Is the file finished" (stops growing) is a file being written and stays with the file waits in section 5. The 30 s limit and the 0.1 s interval go. Confirming a load is not part of this plan.

---

## 4. The readings table

`actions/get.py` keeps one row per reading. Each row gets a `success` rule; the `timeout_attr` goes. Freshness limits stay per reading, as part of success, because they describe how fast each thing changes.

| Reading | Success | Log freshness (kept) | Sources |
|---|---|---|---|
| `scan_status` | a status string other than `Unknown` (today's `trust_status`) | 0.5 s | api, log |
| `hardware_info` | a non-empty dict | 2.0 s | api, log |
| `xy` | `x_um` and `y_um` present and finite | 1.0 s | api, log |
| `jobs` | a non-empty list, every entry with a `Name` | — | api only |
| `selected_job` | a dict with a `Name` | 2.0 s | api, log |
| `job_settings` | `jobName` equals the job asked for, an objective `slotIndex`, and image geometry ready (`derived.settings_geometry_ready`) | 2.0 s | api, log |

Callers that need more say so with `accept`: `read_zwide_um` needs `zPosition`; a confirmation needs the target value.

---

## 5. What stays outside the rule

These are not "is LAS X answering" waits; they last as long as a physical process does.

- **Acquiring, as a whole.** For a command, 4x3 means firing it again between windows, and a second acquire starts a second acquisition. So acquiring keeps exactly what it has today (`ACQUIRE`, `actions/profiles.py:433`): fired once through `UpdateAsync` with no delivery retry (`fire_async=True`, `max_retries=0`), never fired again (`refire_on_unconfirmed=False`, one confirm attempt), and its own watch over the one acquisition, `start_timeout = 15` and `heartbeat_interval = 30`. Every other command the driver sends can be repeated safely: moves are absolute, and settings and job switches end in the same state when sent twice.
- **The error report after a command:** `ECHO_SETTLE_TIMEOUT_S = 1.0` (`dispatcher/change.py:59`) stays as it is.
- **Files being written:** `output/files.py:87` (gives up on no progress, looks every 0.5 s), the "is the file finished" part of saving the scan-field experiment, `save_timeout = 60` in `procedures/measure_limits.py:141`, `wait_for_save` in `configuration/notebook_support.py:73`.
- **Freshness per reading** (how old a log answer may be): kept per reading, section 4.
- **One API question at a time per connection:** it protects LAS X's shared reply fields. `wait_for` waits for it instead of skipping it.

---

## 6. Tests first

All offline, with a fake clock and fake sources so they run in milliseconds. Each one fails on today's code.

1. **A busy API is waited for.** The API slot is held by an earlier question that finishes after 0.5 s; the log has nothing fresh. `get_job_settings` returns the API's settings with slot 0. Today: `None` in 0.06 s. This is the CI failure.
2. **An incomplete answer does not win.** The log answers first with settings lacking an objective; the API answers later with slot 0. The result is the API's.
3. **No head start.** The API answers first with a complete answer, the log 0.1 s later. The API's answer is returned at once.
4. **Four windows of three seconds.** Sources that never succeed: the reading returns unknown after exactly `WINDOWS × WINDOW_S` on the fake clock, and the record shows four windows. Changing the two constants changes the behaviour; nothing else does.
4a. **An unanswered request is requested again at the next window, not before.** A fake API drops the first read request: the record shows the second request sent at 3 s, and the reading succeeds with its answer. A request answered with something that is not a success is requested again within the window.
5. **An old log answer never wins**, even if complete.
6. **The idle check ends.** In log-only mode with no scanner status in the log, `check_idle` fails after 12 s instead of looping. It asks through the run's source setting; in hybrid mode the API's "idle" wins.
7. **The switch keeps its objective.** `select_job` with calibration loaded, where the log confirms first and the API is left busy: the objective reading succeeds and the switch is not refused.
8. **Confirmations fire again only between windows**, never within one. Acquiring is delivered once and never fired again, also when its confirmation fails and when delivery is not acknowledged.
9. **Delivery** is tried four times with `UpdateAwaitReceipt(3)` and no pause between, in commands and in the scan-field save and load alike.
10. **Saving the scan-field experiment** fails after 4x3 when the file never changes, instead of after 30 s.
11. **A guard test:** no literal timeout, retry count, window length, poll interval or sleep for waiting on LAS X outside `dispatcher/tuning.py` in `actions/`, `dispatcher/`, `vendor_interface/`, `scanfields/` and `output/` (the section 5 waits are listed as named exceptions).

Existing tests that pin today's numbers (2 s reads, the grace window, `max_retries`, poll intervals) are updated to the rule, not deleted.

---

## 7. Order of work

0. Measure how long one read of the LAS X log takes (`log_reader.parse_log`), on the simulator and on the scope PC, and choose `POLL_S` from it. The measurement and the choice are written down in `dispatcher/tuning.py` beside the number.
1. Tests 1–5 and 11, red.
2. `wait_for`, the `success` column, the one-question API functions, and the routed readers on top. Remove the reading timeouts, the grace window and the API-side retries. Tests 1–5 green; the existing suite green.
3. `check_idle` and `objective_shift` on the new readings. Tests 6 and 7.
4. Confirmations onto `wait_for`: `race_confirmations`, the poll loops, `confirm_select_job`, `log_wait`, the dispatcher's window loop. Test 8.
5. Delivery and the scan-field save. Tests 9 and 10.
6. Guard test green; README, docstrings and the `profiles.py` comments say the one rule.
7. `run_ci.py` offline, then `run_ci.py --hardware --full` on the simulator: the adapter step passes and the log-reader step finishes.

Each step is its own commit and leaves the suite green.

---

## 8. Open questions for Thom

None. Decided by Thom on 2026-10-10: the settings read while saving waits 4x3 like every other reading (section 3, "The settings read while saving").

---

## 9. Relation to the refactoring plan

`REFACTORING_PLAN.md` (branch `leica-refactoring-plan`) Step 3 makes the profiles pure data. This plan removes numbers from the profiles and adds none, so it fits under Step 3 and does not depend on it. It does not touch what Step 2 decides about unconfirmed changes.

---

## 10. How the build differs from this plan (2026-10-10)

- **The confirmation window loop stays in the dispatcher.** `confirm_and_fire` already runs four windows of `WINDOW_S` with the command fired again between; each confirmation now watches one window through the same mechanism (`get_job_settings(..., deadline=...)` and friends). Moving the loop into `wait_for` would have meant rewriting `select_job`'s transition-admissibility gate for no gain.
- **Saving the scan-field experiment keeps its own waits for now.** Its delivery follows the rule, but "has LAS X started saving" keeps today's limits: 30 s by default, 120 s in `strip_template`, an escalating 120/120/180/240 s in the template restore (`scanfields/strip_restore.py:270`), 60 s in the adapter and in `measure_limits`. Nothing records why they are that long, and a too-short wait could break saving or restoring an operator's template on the microscope. Open for Thom.
- **Per-call `pre_check_timeout` is removed** from every setter and from `acquire`, and `select_job` lost `poll_timeout`/`poll_interval`: how long to wait is never a per-call choice. `validate_hardware.py` lost `--log-select-confirm-timeout-s`.
- **The transient-error tries in the fire block** default to `WINDOWS` in all (`max_retries = WINDOWS - 1`), the same count as before.
- **The tests run on real time with shrunk windows** (0.05 s across the suite, 0.5 s in `test_waiting_rule.py`) instead of a fake clock, because the mechanism runs threads.
- **The guard test** covers `actions/`, `dispatcher/` and `vendor_interface/`; `scanfields/` joins once its save waits are decided, and `output/` holds only file waits.

## 11. Follow-up: state the log writes only when it changes

On the LAS X simulator PC (2026-10-10), log-only readings of the **selected job** and the **scanner status** found nothing and waited out all four windows (12.0 s each); in API and hybrid mode both answered in under 50 ms.

Both are in the logs. The NavigatorExpert log (`MatrixScreener.log`) writes the scanner status as `Acquire/AcquisitionState = N` lines (44 that day: 4 and 3 while scanning, 0 when idle), and job switches are written too (a switch to "AF Job" was confirmed from the log). But the log writes these **only when they change**. The last line stays true until the next one, while the driver refuses any line older than its freshness limit (`scan_status_log_max_age_s` = 0.5 s, `selected_job_log_max_age_s` = 2 s). Half a second after a scan ends, the log's "idle" is thrown away although the scanner is still idle.

Decided 2026-10-10: the scanner status is an API-only reading, like the job list. Its log leg, its 0.5 s freshness limit and the log reader's `AcquisitionState` parsing are removed; the idle check before a command and the end-of-acquisition wait both ask the API, so commands no longer fail in a log-only run (the log-only simulator step then passed with no failures: 81 passed, 24 warnings). Still to decide: how freshness should work for state the log writes only on change. The log-only setting readbacks after a change (eleven settings unconfirmed after 12 s each in the log-only CI step) belong to the same question.
