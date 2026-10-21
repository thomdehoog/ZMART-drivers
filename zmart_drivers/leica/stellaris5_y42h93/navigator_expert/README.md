# navigator_expert — Leica LAS X (STELLARIS) microscope driver

`navigator_expert` drives a **Leica STELLARIS** confocal from Python. It talks to LAS X two ways:
straight to LAS X's own hardware server (the **direct route**, tried first) and through the **LAS X
Python (CAM) API** (the fallback). It is the Leica driver behind the ZMART controller, and every live
command routes through a two-layer dispatch backbone that handles idle-wait, transient-error retry,
readback confirmation, and structured timing/logging. The public API is **synchronous**, so operator
notebooks keep the thin 1–3-line invocation style used across the ZMART drivers.

- **Author:** Thom de Hoog (ZMB, University of Zurich) · thom.dehoog@zmb.uzh.ch · thomdehoog@gmail.com
- **License:** see the repository root [`LICENSE`](../../../../LICENSE).
- **Status:** **Beta (`7.0.0b1`).** Version 7 reads LAS X's state and sends its changes to LAS X's
  hardware server first, and falls back to the CAM API when the hardware server cannot take a
  command (see [Routes](#routes-direct-first-the-cam-api-behind-it)). Taking a picture, saving it and
  letting LAS X go of its cache are three separate steps. It has been tested on the LAS X
  simulator and on the STELLARIS: on 21 October 2026 the quick hardware acceptance passed with every
  change sent through the direct route and no fallback
  ([`sessions/SCOPE_RUN_2026-10-21_DIRECT_WRITERS.md`](sessions/SCOPE_RUN_2026-10-21_DIRECT_WRITERS.md)).
  The hardware CI has since been widened to the lasers, the filter wheels, the z-stack and the
  objective; that wider run has not yet been made on a microscope. Known limits are listed below.
  Before it is used unattended, the findings in
  [`sessions/RELEASE_CANDIDATE_REVIEW.md`](sessions/RELEASE_CANDIDATE_REVIEW.md) need to be fixed.
  The session notes, plans and reviews are kept in [`sessions/`](sessions/).
- **Known limits of this beta:**
  - Scan mode is never changed by the validators, because changing it removes a job's z-stack.
  - Detector gain cannot be tested on this hardware: LAS X gives the HyD S 2 a single value.
  - A filter wheel's spectrum mode is applied by LAS X but the driver cannot confirm it.
  - Windows must allow long paths, or captures must be saved to a short folder: a path of more than
    259 characters stops a save, and the driver says so.
  - `docs/driver-anatomy.md` and `docs/vendor-contract.md` describe the layout and what another
    microscope must supply.

## Contents

1. [About the LAS X CAM API](#1-about-the-las-x-cam-api)
2. [Requirements & installation](#2-requirements--installation)
3. [Configuration](#3-configuration)
4. [Quick start](#4-quick-start)
5. [Core concepts](#5-core-concepts)
6. [API reference](#6-api-reference)
7. [Architecture](#7-architecture)
8. [Configuration & tuning (profiles)](#8-configuration--tuning-profiles)
9. [Testing](#9-testing)
10. [Invariants & gotchas](#10-invariants--gotchas)
11. [Extending the driver](#11-extending-the-driver)
12. [References](#12-references)

---

## 1. About the LAS X CAM API

Leica LAS X exposes automation through a **Python (CAM) API** delivered as **.NET assemblies** that
this driver loads **in-process** via `pythonnet`. Commands are issued by writing an API model and
calling `UpdateAwaitReceipt`/`UpdateAsync`; an **echo model** (`PyApiCommandEcho`) reports errors.
State is read back through the CAM API and, as a hang-proof fallback, by tailing LAS X log files.

This is the **vendor-specific** layer: it knows LAS X enum names, log paths, and how LAS X hands over
finished captures. It runs **on the LAS X PC** (the API is in-process and
blocking — unlike the gRPC/socket ZMART drivers). Keep LAS X-specific assumptions inside this package.

## 2. Requirements & installation

Live control requires **LAS X installed** on the acquisition PC, with the Navigator Expert add-in
directory that contains the CAM assemblies. Offline work (parsing and the tests) needs no
LAS X.

- **Python 3.11 or newer** (the ZMART Controller needs it) and `pythonnet`, which loads the .NET
  CAM assemblies. Install the repository with the Leica extra from its root folder; this also
  installs the ZMART Controller and `pythonnet` (on Windows):
  `pip install -e ".[leica]"`. Add `test` for the offline suite: `pip install -e ".[leica,test]"`.
- **Import the package** by its full name:
  ```python
  import zmart_drivers.leica.stellaris5_y42h93.navigator_expert as lasx
  from zmart_drivers.leica.stellaris5_y42h93.navigator_expert import (
      connect_python_client,
      set_zoom,
      acquire,
      save,
  )
  ```
  The Leica package is self-contained; its filename helper lives under
  `navigator_expert.procedures.save_data.naming`.
- **Plug it into the ZMART Controller** on the LAS X computer. Register the `for_zmart_controller` folder (its `zmart_driver.json` names the class
  beside it) with the controller once; from then on every session plugs the driver in by name.
  Its settings, `CONNECTION` in that file, are all optional; the file explains each one:
  ```python
  import zmart_controller

  # Once, on the LAS X computer:
  zmart_controller.register_driver(
      "C:/ZMART-drivers/zmart_drivers/leica/stellaris5_y42h93/navigator_expert/for_zmart_controller"
  )

  # In every session:
  zmart_controller.mic.connect("stellaris")
  ```
  The package itself is not the driver: it keeps its own lower-level functions, such as
  `acquire`, for notebooks and scripts that work with LAS X directly.

  `get_xyz` and `set_xyz` give the same answer: one dictionary with the keys `x`, `y` and `z`.
  Each axis has four entries, and every number in them is in micrometres. For example, with the
  origin saved at stage x 63500, y 41500 and a focus of 2000 (z-wide 2000, z-galvo 0):
  ```python
  {
      "x": {
          "position": 100.0,
          "unit": "micrometer",
          "actuator": "motoric",
          "actuators": {"motoric": 63600.0},
          "canvas": [-62500.0, 66500.0],
      },
      "y": {
          "position": 50.0,
          "unit": "micrometer",
          "actuator": "motoric",
          "actuators": {"motoric": 41550.0},
          "canvas": [-40500.0, 58500.0],
      },
      "z": {
          "position": 0.0,
          "unit": "micrometer",
          "actuator": "z-wide",
          "actuators": {"z-wide": 2000.0, "z-galvo": 0.0},
          "canvas": [-2250.0, 6250.0],
      },
      "objective_translation_um": [0.0, 0.0, 0.0],
  }
  ```
  - `position` is where the axis is, measured from the saved origin. For z it is the focus: the
    sum of the two z drives, so it reads the same whichever drive made the move. When a different
    objective is in place than the one the origin was saved under, the calibrated objective
    translation is subtracted too, so the position names the same point of the sample.
  - `unit` is always `'micrometer'`.
  - `actuator` is the motor this answer used: `motoric` for x and y, and `z-wide` for z. The driver
    always moves the focus with z-wide, because the objective offsets are measured on it; the z-galvo
    is only read, and is parked at 0 before a move if it is not there.
  - `actuators` lists every motor of the axis, under the names `get_actuators` gives, each with
    its own reading exactly as LAS X reports it. Nothing is subtracted from these, so they show
    how z-wide and z-galvo share the focus.
  - `canvas` is `[min, max]`, everywhere a picture can show along the axis. On x and y it is the
    stage travel itself, because LAS X reports the field of view only for the objective in place
    now; on z it is the z-wide travel widened by the z-galvo travel.
  - `objective_translation_um` is an extra of this driver: the `[x, y, z]` translation that was
    subtracted for the objective in place, `[0, 0, 0]` under the origin's own objective.

  `set_xyz` moves, waits until every leg is confirmed, and then reads the position back from the
  microscope, so its answer shows where the stage really is rather than the numbers asked for.

### Machine paths this driver assumes

| Purpose | Path (default) |
|---|---|
| CAM API command log | `C:\ProgramData\Leica Microsystems\LAS X\lcsCommand.log` |
| LAS X dialog / MessageBox log | `C:\ProgramData\Leica Microsystems\LAS X\MatrixScreener.log` |
| CAM API assemblies (runtime) | `C:\Program Files\Leica Microsystems CMS GmbH\LAS X\AddIns\NavigatorExpert` |

Defaults live in `vendor_specific/primitive_handling/profiles/profiles.py` (`LogReaderProfile`, `LasxApiProfile`) and are discovered at
runtime where possible. Override via the profile, not at call sites.

## 3. Configuration

- **Connection** — `LasxApiProfile` (`vendor_specific/primitive_handling/profiles/profiles.py`): `runtime_root` (the add-in dir) and
  `delay_ms` (Leica's client-side pacing knob `DelayInMilliseconds`, default 250 ms).
- **Log reader** — `LogReaderProfile`: the `lcsCommand.log` / `MatrixScreener.log` paths + freshness windows.
- **Machine-local calibration & limits** — `driver_setup/store.py` resolves the instrument's calibration
  (image↔stage matrix, per-objective translation), limits, orientation, and origin from a
  machine-local ProgramData. Directly below `navigator_expert`, each subsystem owns an independent
  timestamp tree: `limits/<datetime>/`, `calibration/<datetime>/`,
  `orientation/<datetime>/`, and `origin/<datetime>/`. The newest timestamp in each tree wins.
  Limits, calibration, and orientation seed their own repo defaults when empty. The origin has no
  repo default: it exists only once an operator saves one, and the `ZmartDriver` loads the newest one at
  connect. Setup notebooks append only to their owning tree,
  so publishing one subsystem never duplicates another. The single `limits.json` is
  flat: four typed stage-axis `range` entries, `objective_slot` with `allowed` values,
  and either a typed constraint or explicit `[]` for each setter.
- **The driver loads the configs at connect** — making a `ZmartDriver` is the connection. Its
  constructor opens the CAM client (`vendor_specific/connection/lasx_client.py`) and then loads this
  microscope's three machine-local configs — the **instrument limits**, the **orientation**, and the
  **calibration** — so the whole session works from one consistent picture. Normal image orientation is enabled by `IMAGE_SAVE` in `vendor_specific/primitive_handling/profiles/profiles.py`; only the
  orientation measurement explicitly requests raw pixels. Limits and calibration can still be skipped.
  This is a deliberate ladder: the limits notebook
  is bounded only by the physical backstop, `set_orientation` is bounded by limits, and
  `calibrate_objective_pair` is bounded by limits and expects a measured orientation.
- **Limits handshake** — `connect_limits_handshake(client)` (run by the `ZmartDriver` constructor;
  workflows/validators/notebooks call it directly). It resolves an operator-published ProgramData
  `limits.json`, or uses the bundled defaults directly when none exists. It validates exact flat keys, finite ranges, allowed
  objective slots, and envelope **within the hardcoded physical backstop**
  `limits.checks.STAGE_BACKSTOP_UM`), applies the stage envelope, and installs the command safety gate
  for that client. **If the machine file is invalid (or loading is switched off), the session does not
  go dead — it falls back to the bundled default envelope** (loudly warned, marked a fallback). The
  defaults sit within the physical backstop, and the backstop bounds every move regardless, so an
  over-wide or corrupt file can never authorise a move the defaults forbid. The only fully fail-closed
  state is a client that never handshook at all (every mutating command then refuses, naming the
  notebook). Manual `set_stage_limits(...)` still adjusts the in-memory envelope, but it does not open
  the gate — only a successful handshake does.
- **Command safety gate** — `dispatcher/gate.py`. Every mutating wrapper checks that the session
  completed its limits handshake before the native call fires. Stage commands use the four flat
  axis ranges, objective changes use `objective_slot.allowed`, and every listed setter with `[]` is
  explicitly unrestricted. Setter constraints use `range` or `allowed`. Missing or unknown entries invalidate the file and
  trigger the defaults fallback above. Nothing built on top—the `ZmartDriver` class, controller, workflow, or
  notebook—can bypass this command-layer check.

## 4. Quick start

```python
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert import (
    connect_python_client,
    ping,
    connect_limits_handshake,
    select_job,
    set_zoom,
    set_scan_speed,
    move_xy,
    acquire,
    save,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.procedures.save_data.naming import (
    Naming,
    run_hash,
)

# 1. Connect and validate the scope
client = connect_python_client()
assert ping(client)

# 2. Limits handshake (REQUIRED before any mutating command; run automatically
#    by the `ZmartDriver` class — shown here for the low-level
#    API): resolves and validates an operator-published ProgramData limits.json
#    and installs the limits gate for this client. A MISSING or INVALID file
#    uses the bundled defaults directly (loudly), so
#    state.ok stays True — check state.limits.describe()["is_fallback"] if you
#    need to know whether your measured envelope is really in force.
state = connect_limits_handshake(client)
assert state.ok, state.error  # False only if even the bundled defaults are unusable

# 3. Select and configure a job (live commands return a result dict)
select_job(client, "MyExperiment")
r = set_zoom(client, "MyExperiment", 2.0)
assert r["success"] and r["confirmed"], r["message"]  # check BOTH — see §5
set_scan_speed(client, "MyExperiment", 600)

# 4. Move and acquire
move_xy(client, 65_000, 65_000, unit="um")
acq = acquire(client, "MyExperiment")  # -> AcquisitionResult (RAISES on failure)

# 5. Persist with Leica's private naming helper (a separate step from acquire)
naming = Naming(stem=f"overview_{run_hash()}_K00_M000001_G000001_P000000_V00")
saved = save(client, acq, output_root="D:/runs/demo", naming=naming)
print(saved.image_paths)  # {PlaneIndex(t,z,c): Path, ...}
```

> `acquire()` returns an `AcquisitionResult` dataclass and **raises** on failure — it is *not* a
> `{"success": ...}` dict. Saving is a deliberate second step (see §6).

> The explicit `output_root` above is for the low-level driver API. Through
> `zmart_controller`, the Leica driver saves under this microscope's data location, set up once
> and reported in `get_info()["setup"]["data_location"]` (or under the connection's
> `output_root`, when a session gives one); the workflow files each capture into its own run.

> No machine config yet? The first connect seeds ProgramData from the repo defaults so CI and local
> mock runs work. On the rig, run `driver_setup/limits/notebooks/set_limits.ipynb`,
> `driver_setup/orientation/notebooks/set_orientation.ipynb`, and the calibration notebook to replace those defaults
> with measured values. If a machine file is invalid, the session falls back to the bundled default
> envelope (loudly) rather than refusing — fix the file the warning names and reconnect.

## 5. Core concepts

**The client.** Making a `ZmartDriver` is the normal entry point: it opens the CAM client and
loads this microscope's three machine-local configs (limits, orientation, calibration), so the whole
session works from one consistent picture (`driver.client` is the CAM client); `connect_python_client()` is the lower-level primitive that
only opens and pings the client (the setup notebooks use it, since they load just the one config they
need). Every command/reader takes the returned `client` as its first argument. The CAM client has no
disconnect counterpart — it lives for the process; there is nothing to close when a session ends.

**Which configs to load.** `ZmartDriver({"load_limits": …, "load_calibration": …})` chooses whether
limits and calibration are loaded. `load_limits=False` governs the session with the bundled **default**
envelope (never ungated; the physical backstop still holds); `load_calibration=False` refuses
cross-objective moves rather than computing uncompensated ones. Normal image saves always use the
`IMAGE_SAVE.apply_orientation=True` profile default. Only orientation measurement passes an explicit
identity orientation to obtain raw camera pixels.

**The origin is part of the microscope's configuration.** The frame origin is the stage position
that the ZMART controller calls (0, 0, 0). Like the limits, orientation, and calibration, it is set
once in a setup step and then reused by every session. During that step the operator works with
the driver directly and calls its `set_origin`:

```python
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.for_zmart_controller.zmart_driver import (
    ZmartDriver,
)

driver = ZmartDriver()  # making the driver connects it
driver.set_origin()  # the current position becomes (0, 0, 0)
```

`set_origin` saves stage XY, both z drives, and the current objective to a new
`origin/<datetime>/origin.json`. Every later `connect` loads the newest one, so all sessions share the
same frame until the origin is captured again. The controller itself cannot change the origin; it
only forwards commands. Because the objective is saved too, a later objective change is still
compensated with the calibration, or refused when no calibration covers it.

If no origin has been saved yet, positions are plain stage coordinates and connect logs a warning.
If an origin file exists but is damaged or incomplete, connect stops with a clear error rather than
quietly using a zero origin, which would shift every position in the experiment. To recover, fix or
remove that file, or connect with `load_origin=False` in the connection dict and run `set_origin`
again. (Earlier versions treated the origin as belonging to one session only and did not load it at
connect; that is no longer the case.)

**Command vs. read.** Commands *change* state through the dispatch backbone; reads *observe* state
through `readers`. Reads that gate control flow or become persisted truth have a stricter rule (below).

**The result dictionary.** Every live command returns a stable envelope:

| Key | Meaning |
|---|---|
| `success` | Command accepted/applied (transport ok, no permanent API error). |
| `confirmed` | Readback matched the target (`True`/`False`); `None` if no confirmation ran. |
| `message` | Human-readable summary. |
| `timing` | `{pre_check_s, setup_s, fire_s, check_s, confirm_s, total_s, attempts, confirm_attempts, method}`. |
| `logs` | Ordered `{ts, level, msg}` trace. |
| *(command-specific)* | e.g. `position` (`move_xy`). |

**`success` vs. `confirmed` — read both.** `success=True, confirmed=False` means LAS X accepted the
command but readback didn't confirm the value within the windows (most `set_*` use
`success_on_unconfirmed=True` so a workflow can continue, with the mismatch in `logs`). **Don't treat
`success` alone as "applied"** for setting commands. `success=False` means it failed to fire (transport,
permanent error, failed pre-check) and `confirmed` is `None`.

**Error classification** (`vendor_specific/primitive_handling/profiles/error_classification.py`): messages are matched **permanent-first**
(`out of range`, `is invalid`, `not implemented`, …) then **transient** (`being scanned`, `busy`,
`timeout`, …); unknown → permanent (conservative). Transient errors retry up to `max_retries`.

**Reading state — direct, with api / log / hybrid behind it** (`dispatcher/reader.py`, over
the three readers in `vendor_specific/primitives/` (`direct/direct_reader.py`, `api/api_reader.py` and
`log/log_reader.py`), chosen per datum by
`StateReaderProfile`). Every reading asks the **direct** reader first, the default for every datum
since 13 October 2026: it reads LAS X's current state from its hardware server (`state_tree.py`),
so it is neither late like the CAM API's answers nor waiting for a log line (on the simulator, the
stage in about 1 ms against the CAM API's 20 ms). It answers the stage, the z drives, scan status,
job settings, hardware info, the selected job and the job list, with which job is Navigator
Expert's autofocus job. Only when it fails (LAS X not reachable, an answer that does not count) do
the others answer: `api` (CAM read requests, one at a time per connection), `log` (parse LAS X logs
— never blocks the CAM API, can be stale), `hybrid` (watch both, the first answer that counts as a
success wins). A reading says which source answered. What counts as a success is declared once
per datum in `vendor_specific/primitive_handling/readings/readings.py` (job settings, for example, must name the job asked about and carry
an objective slot and image geometry). On the STELLARIS (15 October 2026) the direct reader was
right where the CAM API was stale: the z-galvo position while a job had a z-galvo stack, and the
selected job just after a switch.

**Waiting on LAS X — one rule** (`dispatcher/timing.py`): every reading, confirmation and delivery
waits **four windows of three seconds**. Within a window both sources are watched; between windows
something is sent again — a command is fired again, an unanswered read is requested again, a
message is delivered again. The driver looks again every `POLL_S` (0.1 s) and checks for an answer
already requested every `ANSWER_POLL_S` (0.01 s). After four windows a reading is unknown, a change
unconfirmed, a delivery failed. The idle check before a command is an ordinary reading under the same
rule, asked of the direct reader and, when it fails, of the API alone, never the log: whether a
command fires must not rest on a log line that may be old. When the direct
reader cannot answer, a reading races the API against the log, a backup for each, because what the
log shows can differ between machines and LAS X versions; the job list then comes from the API alone
(the log's list is incomplete). Outside the rule, by decision: acquisition (sent once, never again, watched for as long as the
scan takes), the one-second wait for LAS X's error report after a command, and waits on files being
written. **Freshness rule:** a fresh-by-age
*log* value must never decide whether a command fires, how it is parameterized, whether it confirms,
or what metadata/calibration is persisted — those must use the API leg. The CAM API can hang; the log
mirror is the hang-proof fallback.

**Units.** Public API *inputs* are micrometers (`unit="um"`/`"mm"`/`"m"` where accepted). Returned
positions are mixed: `get_xy` and `move_xy`'s `position` carry raw meters under bare `x`/`y` —
use the `*_um` keys.

**Common per-call overrides** (`None` = use the profile): `max_retries` (transient-retry ceiling),
`tolerance` (readback tolerance, numeric commands). How long to wait is never a per-call choice.

**Logging:** `logging.getLogger("navigator_expert").setLevel(logging.DEBUG)` — the same trace also
travels in each result's `logs`.

## 6. API reference

All setting commands take `(client, job_name, ...)` and return the result dict of §5.

### Connection
```python
connect_python_client(client_name="PythonClient", api_delay_ms=None) -> client
ping(client) -> bool
```

### State readers

The routed readers return a value or `None` (never raise) and accept `diagnostics=True` for a
source-tagged `Reading` (value + `source` + `observed_at`) plus `mode="api"|"log"|"hybrid"` to
override the profile backend. Exceptions: `ping` and `get_lasx_settings` take exactly the calls
shown; `read_zwide_um` takes only `(client, job_name, *, mode=None)` — no `diagnostics` — and
**can raise** (`RuntimeError`/`ValueError`) when job settings are readable but incomplete or
schema-mismatched (it returns `None` only when the settings cannot be read at all).

Z has no hardware readback through CAM. The settings' `zPosition` is the job's stored setpoint
and it stops refreshing for the drive that carries the job's z-stack; for that drive the Z
extractor (`vendor_specific/primitive_handling/readings/derived.py::z_um_from_settings`) asks LAS X's hardware server where the drive
really is, transparently to every caller — reader, `confirm_move_z`, the `ZmartDriver` class. If the hardware
server cannot be reached, the stacked drive is refused with a clear message instead of answered
with the stale number. The free drive is read from the settings at no extra cost.
`docs/design/z-readback-stacked-drive.md` has the measurements and the open items.

| Function | Call | Returns |
|---|---|---|
| `ping` | `(client)` | `bool` |
| `get_scan_status` | `(client, mode=None)` | status string (e.g. `"eIdle"`) |
| `get_xy` | `(client, mode=None)` | `{"x","y","x_um","y_um"}` |
| `read_zwide_um` | `(client, job_name, mode=None)` | `float` (µm); can raise — see above |
| `get_jobs` | `(client, ...)` | list of job dicts |
| `get_job_by_name` | `(client, job_name, ...)` | job dict |
| `get_selected_job` | `(client, ...)` | selected job dict |
| `get_job_settings` | `(client, job_name, ...)` | raw settings dict |
| `get_hardware_info` | `(client, ...)` | hardware dict |
| `get_fov` / `get_base_fov` | `(client, ...)` | field-of-view info |
| `get_lasx_settings` | `()` | LAS X advanced settings (orientation, …) |
| `get_pending_dialog` | `(*, diagnostics=False)` — no client; log-only | open LAS X dialog text, if any |

### Setting commands — reference

All take `(client, job_name, …)` and return the result dict of §5; `tolerance` overrides the default.
Per-setting commands (below the rule) also take a `setting_index` targeting a specific sequential setting.

| Function | Key parameters | Tolerance / notes |
|---|---|---|
| `set_zoom` | `value` | 0.1 (factor) |
| `set_scan_speed` | `value` | integer speed |
| `set_scan_resonant` | `enable` | `True`/`False` |
| `set_scan_mode` | `mode` | e.g. `"xyz"`, `"xzy"` |
| `set_sequential_mode` | `mode` | `"Line"`/`"Frame"`/`"Stack"` |
| `set_scan_field_rotation` | `angle` | 0.5° |
| `set_image_format` | `format_str` | `"512 x 512"` or `(512, 512)` |
| `set_objective` | `hw_info`, one of `slot_index=`/`name=`/`magnification=` | needs `get_hardware_info()` |
| `set_z_stack_definition` | `begin_um=`, `end_um=` (`old_begin_um=`, `old_end_um=`) | 1.0 µm |
| `set_z_stack_step_size` | `step_size_um` | 0.5 µm |
| `set_z_stack_size` | `size_um` | 1.5 µm |
| — *per-setting (take `setting_index`)* — | | |
| `set_frame_accumulation` | `setting_index, value` | exact match |
| `set_frame_average` | `setting_index, value` | exact match |
| `set_line_accumulation` | `setting_index, value` | exact match |
| `set_line_average` | `setting_index, value` | exact match |
| `set_pinhole_airy` | `setting_index, value` | 0.05 AU |
| `set_detector_gain` | `setting_index, beam_route, value` | 1.0 |
| `set_laser_intensity` | `setting_index, beam_route, line_index, value` | 0.005 (0–1) |
| `set_laser_shutter` | `setting_index, beam_route, activate` | `True` = open |
| `set_filter_wheel_slot` | `setting_index, beam_route, filter_wheel_type, slot_index` | exact match |
| `set_filter_wheel_spectrum` | `setting_index, beam_route, filter_wheel_type, position` | 1 nm |

### Settings model
`make_changeable_copy(get_job_settings(client, job))` (`vendor_specific/primitive_handling/readings/parsing.py`) normalizes raw job
settings into the flat, stable dict the `_confirm_*` functions read back against: `zoom`, `scanSpeed`,
`scanMode`, `stack`, `zPosition`, and `activeSettings[...]` (with `activeDetectors`, `activeLaserLines`,
`filterWheels`). Underscore-prefixed keys (`_beamRoute`, `_lineIndex`, `_index`, `_name`) are
driver-added aliases for stable access.

### Stage & motion
```python
move_xy(client, x, y, unit="um", *, max_retries=None, tolerance=None) -> dict                           # tol 20 µm; result has "position"
move_z(client, job_name, z, unit="um", z_mode="galvo", ...) -> dict                                     # z_mode "galvo"|"zwide"; tol 1 µm
set_stage_limits(*, x_min, x_max, y_min, y_max, z_galvo_min, z_galvo_max, z_wide_min, z_wide_max) -> None
get_stage_limits() -> dict ; apply_stage_limits_from_config(stage_cfg) -> None
```

### Acquisition & job selection
```python
select_job(client, job_name, *, compensate=None) -> dict                         # confirm defaults to hybrid
acquire(client, job, *, poll_interval=None, poll_timeout=None, heartbeat_interval=None,
        start_timeout=None) -> AcquisitionResult                                  # RAISES on failure
save(client, acq, output_root, naming, *, lineage=None, state=None,
     orientation=None, projection=None) -> SavedAcquisition                      # image_paths / naming / release_target
```
**Taking a picture, saving it and clearing LAS X's cache are three steps.** `acquire` (in
`procedures/acquisition/`) runs the selected job, writes nothing, and keeps a note of how the
picture was taken (where the stage stood, which job ran, the state of the microscope). The
`save_data` procedure then writes the waiting pictures, and `clear_cache` lets LAS X go of the
pictures that were saved. The controller's `acquire` runs the first two in order and never the
third. Run `save_data` or `clear_cache` by hand with `run_procedure({"name": "save_data"})` and
`run_procedure({"name": "clear_cache"})`.

`save()` collects the capture from LAS X's cache as LAS X writes it (LAS X's AutoSave is not used)
and writes it as one OME-Zarr store. They land in `<output_root>/data/`, so what is made from
them later (a stitched view, an analysis) becomes a folder beside `data` and is
never confused with it. Everything describing the capture is under
`data/metadata`, one folder per party:

```
data/
  overview_K00_M000001_G000001_P000000_V00_T000000_C00_Z00000.ome.tiff
  metadata/
    ZMART_state/  overview_K00_..._T000000_ZMART_state.json
    vendor/       lasx_data_container/<picture>.lasx-description.xml
```

Every name is the acquisition type, then the position label the workflow gave; the driver adds
only the time point, channel and plane. `<output_root>` is the `output_folder` that `acquire` is
given: the driver writes every file itself, from LAS X's cache, so it saves exactly there and
`get_info` says `chooses_its_own_folder: False`. Without one, it is this session's staging folder
under the microscope's data location.

An OME-Zarr store is placed by the centre of its first voxel (OME-NGFF RFC-5): its y/x
translation is the field centre minus `(n - 1) / 2` pixels, half a pixel in from the frame's
corner; z is the lowest plane's own height. The plane entries of the answer and the OME-TIFF
`PositionX`/`PositionY` are the field centre.

The `state` passed to `save()` is embedded in every plane's OME-XML **and**
printed once per acquisition — the image name without the channel and the
z-slice, which one state spans. Embedded, reading it costs opening a picture;
printed, it can simply be read. `SavedAcquisition.state_paths` names what was
written, and the acquire record carries it as `metadata`.

`vendor/` is LAS X's own account of the same capture, kept verbatim as
provenance and sha256'd in `summary.json` — never read in the normal path, and
there only in case the two accounts disagree.
**OME metadata:** `procedures/save_data/ome_canonical.py` writes the clean canonical ZMART OME-XML
that is stored inside each OME-Zarr image (`OME/METADATA.ome.xml`). Captures are saved as OME-Zarr
only.

**LAS X's cache is let go of by the `clear_cache` procedure, never by acquiring or saving.** Every picture LAS X takes stays in its
Data Container cache until LAS X restarts, so a long time course fills the disk twice. The cache
folder is the one the registry names under `Data Port` (`HKLM\SOFTWARE\Leica Microsystems\Data
Container V2`): `D:\Temp2` on the STELLARIS Y42H93.
When you run it, the driver asks LAS X itself to delete each picture whose capture was saved: the
delete of LAS X's own window, so the cache's files go and the project stays whole
(`procedures/clear_cache/release.py` says how). Only pictures whose copy was written and read back
identical are on its list, and the Navigator canvas is then cleared, which lets go of each
capture's hidden copy. The driver never deletes a file itself. A picture it cannot let go of stays
on the list and is reported, so a later call can try again. The 15 October 2026 STELLARIS check
(fifty and then ten captures let go of, the cache staying flat) was made with the earlier
release-after-every-save; `testing/hardware/check_cache_release.py` now runs `clear_cache` after
each capture and should be run again on the simulator.

**Positions are never made in the Navigator Expert.** They come from the ZMART interface.
`acquire()` and the autofocus procedure capture at the current position.

**Changes made in the LAS X window.** The operator can move the stage, or change the objective or the
job, in LAS X itself, and the driver cannot see that as it happens. Every position it works out uses
the objective that is in at that moment, so the numbers stay right. `acquire()` also reads the stage
once just before the capture and places the pictures from that reading. If it differs from what the
workflow last saw, a note is added to the `unconfirmed` list of the result and the capture goes ahead.
The driver does not bring the sample back into view after a change made by hand; that is up to the
operator or the workflow.

Going from one objective to another and back, with the operator switching in between, is safe for the
same reason: the objective offset is worked out at every move from the lens that is in at that
moment, relative to the lens the origin was saved under, so no history is needed. The focus is always
moved with z-wide alone, so the offset never ends up shared between the two z drives.

**Acquisition settings say only how a capture is triggered.** This driver has one trigger,
`full`: the whole of the LAS X job that is selected. Which job that is belongs to the state
(`set_state({"job": ...})`). How a capture is saved -- the format, where the pixels are read from
(`source`), the projection -- is this microscope's `IMAGE_SAVE` profile, set up once. Where it
goes is the `output_folder` that `acquire` is given, or else the data location set up once.

**Stacks are meant to run the same way as the z moves.** A saved stack holds its planes with z ascending, the same
direction as `set_xyz` and `get_xyz`, and each plane's `z_um` in the result says where it was taken. That `z_um` is
worked out from the job's stack settings, not measured from the picture. This is checked in the code and in the
offline tests only. It has not yet been confirmed on a microscope, for example with a stack of a bead at a known depth.

**`Naming`.** A capture is named by its stem, `{acquisition_type}_{position_label}`: the kind
of acquisition `acquire` is given (`acquisition` when none is), then the workflow's label, for
example `overview_K00_M000001_G000001_P000000_V00`. Characters that cannot be in a file name
become `_`, and a stem longer than 80 characters is refused before anything is captured, so the
whole path stays within Windows' old limit. A stem already used in the folder saved to gets
`_001`, `_002`, ..., so no capture is ever overwritten.

## 7. Architecture

The driver is laid out as the anatomy of a ZMART driver describes
([`docs/driver-anatomy.md`](../../../../docs/driver-anatomy.md) in this repository): one folder
per part, each part only using the parts below it, with the microscope-specific code at the
bottom and the class the controller calls at the top.

```
zmart_drivers/leica/stellaris5_y42h93/navigator_expert/
├── for_zmart_controller/   everything that faces the controller (register this folder with it):
│   ├── zmart_driver.json   the driver's name ("stellaris") and how to reach LAS X on this computer
│   ├── zmart_driver.py     the ZmartDriver class the controller calls, one method per command;
│   │                       the work behind each method is in procedures/
│   └── running_log.py      the driver's rolling log file
├── procedures/         the recipes, one folder for each job:
│   ├── acquisition/    taking a picture and keeping a note of how it was taken:
│   │                   acquire.py (the step) · run_job.py (the LAS X call) · pending_capture.py
│   ├── save_data/      writing the waiting pictures to disk, placed by the measured image
│   │                   orientation so they line up with the stage moves: save_captures.py ·
│   │                   save.py · saved_acquisition.py · naming.py · ome_zarr.py ·
│   │                   ome_canonical.py · materialize.py · command_log.py
│   ├── clear_cache/    having LAS X let go of pictures once they are saved: clear.py · release.py
│   ├── absolute_coordinate_system/   turns the plain readings (stage, z-wide, z-galvo, the
│   │                   objective) into the one absolute coordinate system, with the origin and the
│   │                   objective offsets: coordinates.py · frame.py
│   ├── objective_change_with_compensation/   changing the objective or the job without losing
│   │                   the sample: compensated_changes.py · objective_shift.py
│   ├── autofocus/      running a LAS X autofocus job, capture only
│   └── instrument/     the instrument as a whole: state.py (what it is doing and what can be
│                       changed) · info.py (its description) · menu.py (the procedures it offers)
├── session/            what the driver remembers about one connection: handle.py (the open
│                       connection, the origin, the captures waiting to be saved, the pictures
│                       still in LAS X's cache) · loaded_config.py (the orientation and the
│                       objective offsets, loaded once at connect)
├── driver_setup/       everything for setting up this driver on one microscope, one folder per item:
│                       limits/, origin/, orientation/ (the picture's orientation),
│                       optical_calibration/ and data_location/. Each holds its bundled defaults,
│                       its loader, the code that measures it and its notebook. store.py finds
│                       the saved values under ProgramData; notebook_support.py helps the notebooks
├── dispatcher/         the machinery that runs a command safely, and nothing else. It names no LAS X
│                       object: door.py (the one door: read() and write(); the shape of a command's
│                       result) · reader.py (asks the microscope's sources one after the other until
│                       an answer counts; holds the Reading record) · send_loop.py (sends a command,
│                       checks for an error, sends again if the problem is temporary) ·
│                       confirm_loop.py (reads until the change took effect, and sends again if it
│                       did not; never for an acquisition) · gate.py (when the limits are enforced)
│                       · limit_errors.py
├── vendor_specific/    the only part that knows LAS X:
│   ├── connection/     lasx_client.py (open the client and check it answers) · lasx_runtime.py (the
│   │                   .NET CAM assemblies) · hardware_server.py · tuning.py (the waiting numbers)
│   ├── primitives/     the smallest reads and writes, one folder for each way of reaching LAS X:
│   │                   direct/ (direct_reader.py, state_tree.py), api/ (api_reader.py, delivery.py),
│   │                   log/ (log_reader.py, log_wait.py), interface/ (lasx_navigator.py,
│   │                   lasx_dialogs.py: its buttons and dialogs), capture/ (data_container.py,
│   │                   files.py, lasx_data_container.py, product.py, metadata_map.py)
│   └── primitive_handling/   how the dispatcher treats each command, for this microscope:
│       ├── profiles/       profiles.py (one CommandProfile per command) · transport.py (how a
│       │                   request is sent and its reply awaited) · prechecks.py (is the scanner
│       │                   idle) · error_classification.py (sorting LAS X's errors)
│       ├── settings/       settings.py (one entry per setting of a job) · changes.py (the command
│       │                   functions) · objectives.py · limit_rules.py (what the limits allow)
│       ├── confirmations/  confirmations.py · confirm_specs.py · confirm_select_job.py (how a
│       │                   change is checked afterwards)
│       └── readings/       readings.py (the table of readings) · sources.py (asking the API, the
│                           log or the direct reader once) · state.py (get_xy and the other named
│                           readings) · derived.py (what a value means) · parsing.py (the strings
│                           LAS X hands back)
├── testing/            unit/ (offline) · calibration/ (the calibration suites) · hardware/
│                       (validate_*.py live scripts + mock-backed test_* gates) · helpers/ (the
│                       LAS X mock) · data/
├── sessions/           notes, plans and reviews written while building the driver
└── (the driver's CI, pytest.ini, .coveragerc and requirements-dev.txt live in testing/)
```

**Two-layer dispatch backbone** (`dispatcher/confirm_loop.py` → `confirm_and_fire`):

```
confirm_and_fire (outer)
 ├─ _fire_block (inner, ≤ max_retries+1): pre_check → setup(model) → fire (UpdateAwaitReceipt/Async)
 │                                        → error_check (echo) → retry on transient
 └─ confirm_fn (readback) → on unconfirmed (≤ max_confirm_attempts): idle-correct + re-fire → re-confirm
```
The backbone is deliberately *dumb*: it owns pipeline order, retry ceilings, and timing, and knows
nothing about zoom/objectives/stages. Commands supply small zero-arg callables (extra params pre-bound
with `functools.partial`).

### Routes: direct first, the CAM API behind it

Every change the driver makes goes **first to LAS X's hardware server** (the direct route) as the same
element LAS X itself logs for that command, and **to the CAM API only when the direct route could
not send it**: the hardware server is not reachable, or the command has no direct form. Every
reading likewise asks the direct reader first and then the CAM API.

- **Which route carried a command** is in its result, under `route` (`"direct"` or `"cam"`), and in
  its log as `sent through the direct route`. A fallback is a warning that names the reason.
- **A command is never done twice.** Once an element has gone out to LAS X, the CAM API never takes it
  over, even when LAS X refused it or did not answer: it may already have been carried out. An
  acquisition is never sent again on either route.
- **Every direct change goes through the same limits gate** as a CAM change, and LAS X's own
  refusals are reported as they come.
- **To change the order**, set the environment variable `ZMART_WRITE_ROUTES` before the driver
  starts: `direct,cam` (the default), `cam` (the CAM API only, as in earlier versions) or `direct`
  (no fallback). The order is read for every command.
- **Acquire** names a Data Container project for its image. The direct route chooses one that is open
  in LAS X: the project of the last image, else the one remembered from an earlier acquire, else
  the newest open project, else a new `ZMART_Experiment<N>`.
- **Save and load of an experiment file** are not offered by this driver.

**Where the routes are set up.** `vendor_specific/primitive_handling/profiles/routes.py` lists the
ways of sending that exist (`ROUTES`: `direct` and `cam`) and the order they are tried
(`WRITE_ROUTES`, which `ZMART_WRITE_ROUTES` overrides). The dispatcher's `transport_chain.py` uses the
first route that can send and moves on only when that route reports that nothing has left yet.
`direct_transport.py` is the direct route and `primitives/direct/translations.py` holds what each
command becomes at the hardware server. Reading works the same way: the direct reader first, then
the family in `StateReaderProfile.reading_fallback` (the CAM API, `"api"`), and `waiting_fallback`
for the readings a command waits on.

**Dependency direction.** The anatomy's rule is that each part imports only from the parts below
it: `vendor_specific` → `dispatcher` → `procedures`, with `driver_setup` beside
them. The move that put every file in its part kept the imports as they were, so a few still run
upward today: the readers and the handling notes in `vendor_specific` use the dispatcher's tuning,
envelope and read engine. The dispatcher no longer imports anything from `driver_setup`: the
limits handshake lives in `driver_setup/limits/handshake.py` and installs the limits in the gate. `testing/unit/test_architecture_guard.py` lists each
of them and fails when a new one appears; removing them is follow-up work, one at a time.
The connect-time flow lives in `ZmartDriver.__init__`: it opens the client, installs the limits
gate, and loads the orientation and the calibration (the loaders are in
`session/loaded_config.py`).

**What the move did not change.** The layout follows the anatomy; the behaviour is the release
candidate's, so that the move can be reviewed and tested on hardware on its own. These are the
changes the anatomy asks for next, each in a commit of its own because each changes what a
workflow sees:

- Done since the move: an unconfirmed change no longer stops anything. The driver confirms what
  it can (the send is retried, the readback is retried, a move gets one more look), and a change
  that was sent and accepted but never confirmed is written down under `unconfirmed` in
  `get_state`, in a warning and in the acquisition's answer, while the command carries on. Only a
  refused, failed or contradicted change is a failure. A reading that fails, such as the selected
  job, is unknown rather than a stop. Acquisition stays the one exception the other way: sent
  once, never re-sent, and waited for as long as the scan needs.
- A limits refusal raises `ValueError`; today parts of the gate raise `RuntimeError`.
- The limits gate moves inside the set dispatcher, so that no change calls it itself.
- Done since the move: the logic of the commands left `zmart_adapter.py` for `procedures/`
  (`session/`, `frame.py`, `acquisition/`, `instrument/`), the `output/` folder is gone, and
  the plain coordinate arithmetic is in `procedures/absolute_coordinate_system/coordinates.py`. The coordinate
  system is a procedure of its own (`procedures/absolute_coordinate_system/`), so the `ZmartDriver` class
  converts nothing. The full list of differences from the plan is in
  `docs/driver-anatomy.md` ("Where the Leica tree still differs from the plan").
- The setup notebooks become thin: the measuring is a procedure, the notebook only calls it.

## 8. Configuration & tuning (profiles)

Every command has a frozen `CommandProfile` in `vendor_specific/primitive_handling/profiles/profiles.py` — its complete recipe (pluggable
callables + retry/confirm tuning). Tuning a command = editing its profile; nothing else changes.

```python
@dataclass(frozen=True)
class CommandProfile:
    pre_check_fn = None
    error_check_fn = _default_error_check
    confirm_fn = None
    max_retries = WINDOWS - 1
    max_confirm_attempts = WINDOWS
    refire_on_unconfirmed = True
    confirm_poll_s = WINDOW_S
    confirm_tolerance = None
    success_on_unconfirmed = True  # exhausted readback -> unconfirmed, never hard-fail
    # + acquisition watch / backoff / async knobs
```

Posture is uniform: retry the fire, re-fire between confirm windows, return *unconfirmed* rather than
hard-failing. `ACQUIRE` is the sole deviation (`max_retries=0`, `refire_on_unconfirmed=False`) — it
must never re-send or it would start a duplicate acquisition.

**Default tolerances** (override per call via `tolerance=`):

| Command | Tol | Unit | | Command | Tol | Unit |
|---|---|---|---|---|---|---|
| `set_zoom` | 0.1 | factor | | `set_pinhole_airy` | 0.05 | AU |
| `set_scan_field_rotation` | 0.5 | deg | | `set_detector_gain` | 1.0 | gain |
| `set_z_stack_definition` | 1.0 | µm | | `set_laser_intensity` | 0.005 | frac |
| `set_z_stack_step_size` | 0.5 | µm | | `set_filter_wheel_spectrum` | 1 | nm |
| `set_z_stack_size` | 1.5 | µm | | `move_xy` | 20.0 | µm |
| | | | | `move_z` | 1.0 | µm |

## 9. Testing

```powershell
# Offline suite (no microscope, no LAS X), from the repository root
python -m pip install -e ".[leica,test]"
python -P -m pytest -q zmart_drivers/leica/stellaris5_y42h93/navigator_expert/testing/unit
python -P -m pytest -q zmart_drivers/leica/stellaris5_y42h93/navigator_expert/testing/calibration

# Self-contained gates
python zmart_drivers/leica/stellaris5_y42h93/navigator_expert/testing/run_ci.py             # mock/offline (default)
python zmart_drivers/leica/stellaris5_y42h93/navigator_expert/testing/run_ci.py --mock      # explicit mock/offline
python zmart_drivers/leica/stellaris5_y42h93/navigator_expert/testing/run_ci.py --hardware         # live: the quick acceptance (about five minutes)
python zmart_drivers/leica/stellaris5_y42h93/navigator_expert/testing/run_ci.py --hardware --full  # live: plus the reader probe, parity, and the api/log routes
```

`testing/unit/` is offline against committed synthetic fixtures (stage/limits, log & state readers, acquisition, runtime loading). Follow the project
TDD practice: add a failing offline test first, and assert real values, not just shapes.

**Live hardware validation** (requires a live LAS X — simulator or scope) runs through the
`validate_*.py` *scripts* in `testing/hardware/`, invoked directly or via `testing/run_ci.py --hardware` —
not through pytest. Everything pytest collects is mock-backed and offline, including the
`test_*.py` files in `testing/hardware/`, which drive the same validators against
`MockLasxClient`. (The `hardware`/`slow` markers registered in `pytest.ini` are used by zero
tests today; the mock/hardware split is file-based, not marker-based.) Direct hardware-moving
validator sections run only with their `--allow-*` flags:

```powershell
python -m pytest -q zmart_drivers/leica/stellaris5_y42h93/navigator_expert/testing/hardware   # offline mock gates
python zmart_drivers/leica/stellaris5_y42h93/navigator_expert/testing/hardware/validate_hardware.py --yes --allow-xy --allow-z --allow-objective --allow-acquire --state-reader-mode hybrid
```
Validator JSONL outputs are runtime artifacts, ignored by default. Every validator run also
writes a **Markdown run report** (`hardware_run_report_<timestamp>.md`, in `testing/_report/` when
launched via run_ci) listing every attempted instrument change — including failures and
restores — with confirmation status and timing. **Bench-run instructions** (prerequisites, what
`--hardware` changes on the scope, expected duration, report locations) live in
[`testing/hardware/README.md`](testing/hardware/README.md).

## 10. Invariants & gotchas

These **silently misbehave** instead of failing loudly — respect them or results are wrong without an error:

1. **Movement needs a limits handshake** — a client that never handshook refuses `move_xy`/`move_z`
   fail-closed. Through `connect_microscope` (and so the `ZmartDriver` class) the handshake always runs, falling back
   to the bundled default envelope if the machine file is invalid, so a connected session can always
   move within the defaults; the physical backstop bounds every move regardless.
2. **`acquire()` returns an `AcquisitionResult` and raises on failure** — not a dict; read timing via
   `acq.command_result["timing"]`. Persisting is a separate `save()` call.
3. **For setting commands, check `confirmed`, not just `success`** — most `set_*` return
   `success=True, confirmed=False` when readback never matched (mismatch is in `logs`).
4. **Reads that gate control flow or get persisted must use the API leg** — never let a fresh-by-age
   log value decide whether a command fires or what metadata/calibration is written.
5. **The CAM API can hang** — that's why `readers` has a log mirror and an in-flight API-read cap.
6. **`select_job` confirmation defaults to `hybrid`** — a stale API readback can report the wrong job
   after a switch; the hybrid race only accepts evidence of an actual transition.
7. **Objective changes are best-effort** — a manual turret may pop a "turn the turret manually" dialog
   (surfaced in `MatrixScreener.log` / `get_pending_dialog`); prefer binding the objective via the job.
8. **`PyApiAcquireJob` silently no-ops without `m.JobName`** — returns in ~0 s with no error; the driver
   sets it in the command's `setup_fn`. Check the setup callback before assuming a LAS X bug.
9. **Adapter mutating ops are gated by the flat `limits.json` at the commands layer** — if the machine file
    fails to load/validate at connect, the session falls back to the bundled **default** envelope
    (loudly warned) rather than refusing everything; the connect-time warning names what happened
    (see §3). Out-of-envelope moves still refuse at the commands layer, below the `ZmartDriver` class.
10. **The origin is loaded at connect** — `connect` loads the newest origin saved by the driver's
    `set_origin` setup step, so the controller never sets it. With none saved, positions are plain
    stage coordinates (with a warning); a damaged origin file stops connect with a clear error
    (see §5).

## 11. Extending the driver

Adding a command touches four places, following the pattern every existing command uses:

1. **Confirm function** (`vendor_specific/primitive_handling/confirmations/confirmations.py`) — `_confirm_X(client, ...) -> {"success", "logs"}`
   (skip if no readback is possible).
2. **CommandProfile** (`vendor_specific/primitive_handling/profiles/profiles.py`) — `MY_PARAM = _leica_setting_profile(_confirm_my_param)`.
3. **Command wrapper** (`vendor_specific/primitive_handling/settings/changes.py`) — three phases (pre-checks → `_dispatch(...)` with the
   profile + a `setup_fn` and target-bound `confirm_fn` → post-process). `_dispatch` handles
   client-binding, profile defaults, and the `confirm_and_fire` call.
4. **Export** (`__init__.py`) — add to `__all__` and import it.

Copy the closest existing command of a similar shape.

## 12. References
- ZMART Controller (the microscope-independent layer this driver plugs into). It lives in its own
  repository; until that repository exists, it is the
  [`release-candidate-zmart-controller`](https://github.com/thomdehoog/ZMART-microscopy/tree/release-candidate-zmart-controller)
  branch of ZMART-microscopy.
- Sibling drivers, still under construction in the main ZMART-microscopy repository:
  [ZEISS ZEN API](https://github.com/thomdehoog/ZMART-microscopy/tree/main/zmart_drivers/zeiss/zenapi) (gRPC) and
  [Nikon NIS-Elements](https://github.com/thomdehoog/ZMART-microscopy/tree/main/zmart_drivers/nikon) (socket macro).
- Leica filename implementation: [`procedures/save_data/naming.py`](procedures/save_data/naming.py)
