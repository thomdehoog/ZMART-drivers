# navigator_expert — Leica LAS X (STELLARIS) microscope driver

`navigator_expert` drives a **Leica STELLARIS** confocal from Python through the **LAS X Python
(CAM) API**. It is the Leica driver behind the ZMART controller, and every live command routes
through a two-layer dispatch backbone that handles idle-wait, transient-error retry, readback
confirmation, and structured timing/logging. The public API is **synchronous**, so operator
notebooks keep the thin 1–3-line invocation style used across the ZMART drivers.

- **Author:** Thom de Hoog (ZMB, University of Zurich) · thom.dehoog@zmb.uzh.ch · thomdehoog@gmail.com
- **License:** see the repository root [`LICENSE`](../../../../LICENSE).
- **Status:** **Release candidate (`6.0.0rc1`), not yet released.** The driver has been tested on the
  LAS X simulator and on a real STELLARIS. The latest full run on the microscope is recorded in
  [`SCOPE_RUN_2026-10-10.md`](SCOPE_RUN_2026-10-10.md). Before it is released for unattended use,
  the findings in [`RELEASE_CANDIDATE_REVIEW.md`](RELEASE_CANDIDATE_REVIEW.md) need to be fixed.

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

This is the **vendor-specific** layer: it knows LAS X enum names, log paths, the `.lrp`/`.rgn`/`.xml`
scan-field template formats, and OME exports. It runs **on the LAS X PC** (the API is in-process and
blocking — unlike the gRPC/socket ZMART drivers). Keep LAS X-specific assumptions inside this package.

## 2. Requirements & installation

Live control requires **LAS X installed** on the acquisition PC, with the Navigator Expert add-in
directory that contains the CAM assemblies. Offline work (parsing, template edits, tests) needs no
LAS X.

- **Python 3.11 or newer** (the ZMART Controller needs it) and `pythonnet`, which loads the .NET
  CAM assemblies. Install the repository with the Leica extra from its root folder; this also
  installs the ZMART Controller and `pythonnet` (on Windows):
  `pip install -e ".[leica]"`. Add `test` for the offline suite: `pip install -e ".[leica,test]"`.
- **Import the package** by its full name:
  ```python
  import zmart_drivers.leica.stellaris5_y42h93.navigator_expert as lasx
  from zmart_drivers.leica.stellaris5_y42h93.navigator_expert import connect_python_client, set_zoom, acquire, save
  ```
  The Leica package is self-contained; its filename helper lives under
  `navigator_expert.acquisition.naming`.
- **Plug it into the ZMART Controller** on the LAS X computer. Register `zmart_controller_plugin.py`,
  in this folder, with the controller once; from then on every session plugs the driver in by name.
  Its settings, `CONNECTION` in that file, are all optional; the file explains each one:
  ```python
  import zmart_controller

  # Once, on the LAS X computer:
  zmart_controller.register_driver("C:/ZMART-drivers/zmart_drivers/leica/stellaris5_y42h93/navigator_expert")

  # In every session:
  zmart_controller.mic.connect("stellaris")
  ```
  The package itself is not the driver: it keeps its own lower-level functions, such as
  `acquire`, for notebooks and scripts that work with LAS X directly.

  `get_xyz` and `set_xyz` give the same answer: one dictionary with the keys `x`, `y` and `z`.
  Each axis has four entries, and every number in them is in micrometres. For example, with the
  origin saved at stage x 63500, y 41500 and a focus of 2000 (z-wide 2000, z-galvo 0):
  ```python
  {'x': {'position': 100.0, 'unit': 'micrometer', 'actuators': {'motoric': 63600.0},                 'canvas': [-62500.0, 66500.0]},
   'y': {'position': 50.0,  'unit': 'micrometer', 'actuators': {'motoric': 41550.0},                 'canvas': [-40500.0, 58500.0]},
   'z': {'position': 0.0,   'unit': 'micrometer', 'actuators': {'z-wide': 2000.0, 'z-galvo': 0.0},  'canvas': [-2250.0, 6250.0]},
   'objective_translation_um': [0.0, 0.0, 0.0]}
  ```
  - `position` is where the axis is, measured from the saved origin. For z it is the focus: the
    sum of the two z drives, so it reads the same whichever drive made the move. When a different
    objective is in place than the one the origin was saved under, the calibrated objective
    translation is subtracted too, so the position names the same point of the sample.
  - `unit` is always `'micrometer'`.
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
| Scan-field templates | `%APPDATA%\Leica Microsystems\LAS X\MatrixScreener6\User_*\ScanningTemplates` |

Defaults live in `actions/profiles.py` (`LogReaderProfile`, `LasxApiProfile`) and are discovered at
runtime where possible. Override via the profile, not at call sites.

## 3. Configuration

- **Connection** — `LasxApiProfile` (`actions/profiles.py`): `runtime_root` (the add-in dir) and
  `delay_ms` (Leica's client-side pacing knob `DelayInMilliseconds`, default 250 ms).
- **Log reader** — `LogReaderProfile`: the `lcsCommand.log` / `MatrixScreener.log` paths + freshness windows.
- **Machine-local calibration & limits** — `configuration/store.py` resolves the instrument's calibration
  (image↔stage matrix, per-objective translation), limits, orientation, and origin from a
  machine-local ProgramData. Directly below `navigator_expert`, each subsystem owns an independent
  timestamp tree: `limits/<datetime>/`, `calibration/<datetime>/`,
  `orientation/<datetime>/`, and `origin/<datetime>/`. The newest timestamp in each tree wins.
  Limits, calibration, and orientation seed their own repo defaults when empty. The origin has no
  repo default: it exists only once an operator saves one, and the adapter loads the newest one at
  connect. Setup notebooks append only to their owning tree,
  so publishing one subsystem never duplicates another. The single `limits.json` is
  flat: four typed stage-axis `range` entries, `objective_slot` with `allowed` values,
  and either a typed constraint or explicit `[]` for each setter. Backlash remains a
  command routine, not configuration.
- **The driver loads the configs at connect** — `connect_microscope(...)` (`connect.py`)
  is the driver's own front door. It opens the CAM client and then loads this microscope's three
  machine-local configs — the **instrument limits**, the **orientation**, and the **calibration** — so the
  whole session works from one consistent picture. The zmart adapter's `connect()` simply delegates to
  it. Normal image orientation is enabled by `IMAGE_SAVE` in `actions/profiles.py`; only the
  orientation measurement explicitly requests raw pixels. Limits and calibration can still be skipped.
  This is a deliberate ladder: the limits notebook
  is bounded only by the physical backstop, `set_orientation` is bounded by limits, and
  `calibrate_objective_pair` is bounded by limits and expects a measured orientation.
- **Limits handshake** — `connect_limits_handshake(client)` (run by `connect_microscope`;
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
  trigger the defaults fallback above. Nothing built on top—adapter, controller, workflow, or
  notebook—can bypass this command-layer check.

## 4. Quick start

```python
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert import (
    connect_python_client, ping,
    connect_limits_handshake, select_job, set_zoom, set_scan_speed,
    move_xy, acquire, save,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.acquisition.naming import Naming, run_hash

# 1. Connect and validate the scope
client = connect_python_client()
assert ping(client)

# 2. Limits handshake (REQUIRED before any mutating command; run automatically
#    by connect_microscope and the zmart adapter — shown here for the low-level
#    API): resolves and validates an operator-published ProgramData limits.json
#    and installs the limits gate for this client. A MISSING or INVALID file
#    uses the bundled defaults directly (loudly), so
#    state.ok stays True — check state.limits.describe()["is_fallback"] if you
#    need to know whether your measured envelope is really in force.
state = connect_limits_handshake(client)
assert state.ok, state.error   # False only if even the bundled defaults are unusable

# 3. Select and configure a job (live commands return a result dict)
select_job(client, "MyExperiment")
r = set_zoom(client, "MyExperiment", 2.0)
assert r["success"] and r["confirmed"], r["message"]     # check BOTH — see §5
set_scan_speed(client, "MyExperiment", 600)

# 4. Move and acquire
move_xy(client, 65_000, 65_000, unit="um")
acq = acquire(client, "MyExperiment")                     # -> AcquisitionResult (RAISES on failure)

# 5. Persist with Leica's private naming helper (a separate step from acquire)
naming = Naming(folder="overview", hash6=run_hash())
saved = save(client, acq, output_root="D:/runs/demo", naming=naming)
print(saved.image_paths)                                  # {PlaneIndex(t,z,c): Path, ...}
```

> `acquire()` returns an `AcquisitionResult` dataclass and **raises** on failure — it is *not* a
> `{"success": ...}` dict. Saving is a deliberate second step (see §6).

> The explicit `output_root` above is for the low-level driver API. Through
> `zmart_controller`, the Leica adapter discovers the `ZMART-microscopy` root
> beside LAS X native AutoSave and reports it through `get_info()["output_root"]`;
> the workflow creates the experiment/acquisition folders under that root.

> No machine config yet? The first connect seeds ProgramData from the repo defaults so CI and local
> mock runs work. On the rig, run `configuration/limits/notebooks/set_limits.ipynb`,
> `configuration/image_stage_registration/notebooks/set_orientation.ipynb`, and the calibration notebook to replace those defaults
> with measured values. If a machine file is invalid, the session falls back to the bundled default
> envelope (loudly) rather than refusing — fix the file the warning names and reconnect.

## 5. Core concepts

**The client.** `connect_microscope(...)` is the normal entry point: it opens the CAM client and
loads this microscope's three machine-local configs (limits, orientation, calibration), so the whole
session works from one consistent picture; `connect_python_client()` is the lower-level primitive that
only opens and pings the client (the setup notebooks use it, since they load just the one config they
need). Every command/reader takes the returned `client` as its first argument. The CAM client has no
disconnect counterpart — it lives for the process; there is nothing to close when a session ends.

**Which configs to load.** `connect_microscope(load_limits=…, load_calibration=…)` chooses whether
limits and calibration are loaded. `load_limits=False` governs the session with the bundled **default**
envelope (never ungated; the physical backstop still holds); `load_calibration=False` refuses
cross-objective moves rather than computing uncompensated ones. Normal image saves always use the
`IMAGE_SAVE.apply_orientation=True` profile default. Only orientation measurement passes an explicit
identity orientation to obtain raw camera pixels.

**The origin is part of the microscope's configuration.** The frame origin is the stage position
that the ZMART controller calls (0, 0, 0). Like the limits, orientation, and calibration, it is set
once in a setup step and then reused by every session. During that step the operator works with
the driver directly and calls the adapter's `set_origin`:

```python
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.zmart_adapter import zmart_adapter as adapter

handle = adapter.connect(adapter.CONNECTION)
adapter.set_origin(handle)  # the current position becomes (0, 0, 0)
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

**Live vs. file.** `set_zoom(...)` talks to the running scope and confirms by reading hardware back;
`lrp_set_zoom(...)` edits a `.lrp` template *file* (nothing happens on the scope until LAS X reloads
it). There is a deliberate parallel API for each — don't mix them (see §6).

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

**Error classification** (`vendor_interface/errors.py`): messages are matched **permanent-first**
(`out of range`, `is invalid`, `not implemented`, …) then **transient** (`being scanned`, `busy`,
`timeout`, …); unknown → permanent (conservative). Transient errors retry up to `max_retries`.

**Reading state — api / log / hybrid** (`dispatcher/read.py`, over `vendor_interface/api_reader.py` and `log_reader.py`, chosen per datum by `StateReaderProfile`;
default `hybrid` for all routed datums): `api` (CAM read requests, one at a time per connection), `log`
(parse LAS X logs — never blocks the CAM API, can be stale), `hybrid` (watch both, the first answer
that counts as a success wins — no source is preferred; the legs' staleness profiles are
complementary, so one usually delivers). What counts as a success is declared once per datum in
`actions/get.py` (job settings, for example, must name the job asked about and carry an objective
slot and image geometry).

**Waiting on LAS X — one rule** (`dispatcher/tuning.py`): every reading, confirmation and delivery
waits **four windows of three seconds**. Within a window both sources are watched; between windows
something is sent again — a command is fired again, an unanswered read is requested again, a
message is delivered again. The driver looks again every `POLL_S` (0.1 s) and checks for an answer
already requested every `ANSWER_POLL_S` (0.01 s). After four windows a reading is unknown, a change
unconfirmed, a delivery failed. The idle check before a command is an ordinary reading under the same
rule, asked of the API alone: whether a command fires is the API's to decide. Every other reading
races the API against the log, a backup for each, because what the log shows can differ between
machines and LAS X versions; only the job list is API-only (the log's list is incomplete). Outside the rule, by decision: acquisition (sent once, never again, watched for as long as the
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
extractor (`actions/derived.py::z_um_from_settings`) saves the experiment and reads the job's
`ZPosition` from the `.lrp` instead (~0.4 s), transparently to every caller — reader,
`confirm_move_z`, the adapter. The free drive is read from the settings at no extra cost.
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
`make_changeable_copy(get_job_settings(client, job))` (`vendor_interface/parsing.py`) normalizes raw job
settings into the flat, stable dict the `_confirm_*` functions read back against: `zoom`, `scanSpeed`,
`scanMode`, `stack`, `zPosition`, and `activeSettings[...]` (with `activeDetectors`, `activeLaserLines`,
`filterWheels`). Underscore-prefixed keys (`_beamRoute`, `_lineIndex`, `_index`, `_name`) are
driver-added aliases for stable access.

### Stage & motion
```python
move_xy(client, x, y, unit="um", *, max_retries=None, tolerance=None) -> dict                           # tol 20 µm; result has "position"
move_z(client, job_name, z, unit="um", z_mode="galvo", ...) -> dict                                     # z_mode "galvo"|"zwide"; tol 1 µm
move_galvo_to_pixel(client, px, py, ...) -> dict                                                        # pan galvo to a pixel (no stage move)
set_stage_limits(*, x_min, x_max, y_min, y_max, z_galvo_min, z_galvo_max, z_wide_min, z_wide_max) -> None
get_stage_limits() -> dict ; apply_stage_limits_from_config(stage_cfg) -> None
```

### Acquisition & job selection
```python
select_job(client, job_name, *, compensate=None) -> dict                         # confirm defaults to hybrid
acquire(client, job, *, poll_interval=None, poll_timeout=None, heartbeat_interval=None,
        start_timeout=None) -> AcquisitionResult                                  # RAISES on failure
save(client, acq, output_root, naming, *, lineage=None, fix_ome=True,
     cleanup_source=False) -> SavedAcquisition                                    # image_paths / xml_paths / naming
```
`save()` collects LAS X native AutoSave output into a neutral product and
writes canonical single-plane OME-TIFFs with OME-XML embedded in each image.
They land in `<output_root>/<folder>/data/`, so what is made from
them later (a stitched view, an analysis) becomes a folder beside `data` and is
never confused with it. Everything describing the capture is under
`data/metadata`, one folder per party:

```
overview/
  data/
    overview_<hash6>_K00_M000001_G000001_P000000_V00_T000000_C00_Z00000.ome.tiff
    metadata/
      ZMART_state/  overview_<hash6>_..._T000000_ZMART_state.json
      vendor/       lasx_native_autosave/{source_embedded.ome.xml, *.xlef, metadata_*.xlif}
```

The `state` passed to `save()` is embedded in every plane's OME-XML **and**
printed once per acquisition — the image name without the channel and the
z-slice, which one state spans. Embedded, reading it costs opening a picture;
printed, it can simply be read. `SavedAcquisition.state_paths` names what was
written, and the adapter's acquire record carries it as `metadata`.

`vendor/` is LAS X's own account of the same capture, kept verbatim as
provenance and sha256'd in `summary.json` — never read in the normal path, and
there only in case the two accounts disagree.
**OME metadata:** `output/ome.py` repairs known Leica OME violations (e.g. laser `Wavelength="0"`)
in place, preserving byte formatting; `output/ome_canonical.py` writes clean canonical ZMART OME;
`save(..., fix_ome=True)` validates/repairs each written file.

**Acquiring never touches the scanning template.** Positions are never made in the Navigator Expert:
they come from the ZMART interface. `acquire()` and the autofocus procedure capture at the current
position and leave the LAS X template alone.

**Extra in-place backlash rounds are off by default.** The acquisition setting
`backlash_rounds` defaults to `0`; pass a positive whole number to opt in for a
particular capture. The normal XY move still uses the driver's consistent final
approach to the requested position.

**`Naming` constraints.** The `folder` must be kebab-case lowercase (`"overview"`,
`"target-scan"`); `Naming` raises `ValueError` on `"Prescan"` or `"target_scan"`. Through the
adapter, the `folder` acquisition setting (default `"scan"`) is checked before the scan fires, so a
bad name never wastes a capture. Calling `save()` directly, the raise comes after the capture, so
validate names before acquiring. The adapter's driver-owned helper gives every
acquired position a unique hash, and the workflow supplies the `K/M/G/P/V` position label.

### Templates / scan-fields (offline-capable)

**Parse saved templates** (read-only, stdlib ElementTree — no fragile regex; `scanfields/parsers.py`,
except `parse_lrp` in `scanfields/lrp.py`):
`parse_lrp` (full job-settings tree) · `parse_scan_positions` · `parse_acquisition_positions` ·
`parse_base_grid` · `parse_focus_points` · `parse_rgn_geometries` · `parse_rgn_tile_colors` ·
`parse_matrix_settings` · `plan_tiles_from_geometries` (planning).

**Active experiment:** `save_experiment` (fires save, confirms via file mtime + stable size) ·
`load_experiment` (receipt only — verify with a follow-up save) · `save_and_read_lrp` (save +
`parse_lrp` in one call) · `get_template_state` (`"fresh"`/`"unstripped"`/`"stripped"`/`"unreadable"`
— the adapter treats `"unreadable"` as a hard pre-acquire error) ·
`find_scanning_templates_dir` · `strip_template` / `restore_template` / `strip_template_in_place`
(remove/restore operator-drawn scan fields, regions, focus points around an automated run).

**Offline template edits** (`experimental/lrp_edits/`) — a **parallel, file-based** API mirroring the
live `set_*` commands (`lrp_set_zoom` vs `set_zoom`, …), since file editing has no readback. Route
every edit through `apply_lrp_change(...)` (**save → edit → reorder → load → save → verify**;
`reorder_jobs` keeps the active job selected). It also provides ROI authoring — `make_rectangle` /
`make_ellipse` / `make_polygon`, `lrp_add_roi`, `lrp_clear_rois` — and pixel↔stage↔pan/zoom coordinate
math — `mask_contour_to_roi`, `roi_translation_to_pan`, `galvo_pan_for_pixel` (see the
coordinate-frame docstring atop `experimental/lrp_edits/roi.py`). Despite the `experimental/` name this
code is **load-bearing** (used by `move_galvo_to_pixel`, `disable_roi_scan`, `reset_pan`) — read it as
"offline template editor", not "unstable".

## 7. Architecture

The driver is laid out as the anatomy of a ZMART driver describes
([`docs/driver-anatomy.md`](../../../../docs/driver-anatomy.md) in this repository): one folder
per part, each part only using the parts below it, with the microscope-specific code at the
bottom and the class the controller calls at the top.

```
zmart_drivers/leica/stellaris5_y42h93/navigator_expert/
├── zmart_driver.json   the driver's name ("stellaris") and how to reach LAS X on this computer
├── zmart_driver.py     the ZmartDriver class the controller calls, one method per command
├── zmart_adapter/      the functions behind those methods, one per command (still holds the
│                       coordinate arithmetic; see "What the move did not change" below)
├── zmart_controller_plugin.py   the older module shape, kept until the class has run on hardware
├── connect.py          the connect flow: open LAS X, load this microscope's configuration
├── vendor_interface/   the only part that knows LAS X: lasx_runtime.py (the .NET CAM assemblies),
│                       api_reader.py and log_reader.py (the two ways to read state), log_wait.py,
│                       parsing.py (the strings LAS X hands back), errors.py (sorting LAS X's errors)
├── dispatcher/         the engines that run an action safely: read.py (api/log/hybrid routing),
│                       change.py (confirm_and_fire), gate.py and checks.py (the limits gate and its
│                       rulebook), prechecks.py, envelope.py, tuning.py (the timing constants)
├── actions/            get.py (which source answers each reading) · derived.py · set.py (every
│                       set_*/move_*/acquire/select_job) · confirmations.py · confirm_specs.py ·
│                       confirm_select_job.py · profiles.py (one CommandProfile per command) ·
│                       objectives.py · objective_shift.py · galvo.py · acquire.py (the capture)
├── procedures/         backlash.py · measure_orientation.py · measure_limits.py ·
│                       calibrate_objective_pair.py · check_calibration.py · adopt_calibration.py ·
│                       calibration_common.py · algorithms/ (registration and focus scoring)
├── output/             what comes out of one acquisition: lasx_native_autosave.py (collect) ·
│                       files.py · materialize.py · ome.py · ome_canonical.py · naming.py ·
│                       product.py · save.py
├── configuration/      what is measured once per microscope. limits/, image_stage_registration/
│                       and optical_calibration/ each hold their defaults/ and their notebook;
│                       store.py resolves the machine-local snapshots under ProgramData;
│                       session_state.py holds what one connection loaded; notebook_support.py
├── scanfields/         .lrp/.rgn/.xml parsing + templates (tile layout; a workflow concern, kept
│                       here until it has a home elsewhere)
├── experimental/       lrp_edits/: offline template editors without live-state readback
├── testing/            unit/ (offline) · calibration/ (the calibration suites) · hardware/
│                       (validate_*.py live scripts + mock-backed test_* gates) · helpers/ (the
│                       LAS X mock) · data/
└── (the driver's CI, pytest.ini, .coveragerc and requirements-dev.txt live in testing/)
```

**Two-layer dispatch backbone** (`dispatcher/change.py` → `confirm_and_fire`):

```
confirm_and_fire (outer)
 ├─ _fire_block (inner, ≤ max_retries+1): pre_check → setup(model) → fire (UpdateAwaitReceipt/Async)
 │                                        → error_check (echo) → retry on transient
 └─ confirm_fn (readback) → on unconfirmed (≤ max_confirm_attempts): idle-correct + re-fire → re-confirm
```
The backbone is deliberately *dumb*: it owns pipeline order, retry ceilings, and timing, and knows
nothing about zoom/objectives/stages. Commands supply small zero-arg callables (extra params pre-bound
with `functools.partial`).

**Dependency direction.** The anatomy's rule is that each part imports only from the parts below
it: `vendor_interface` → `dispatcher` → `actions` → `procedures`, with `output` and
`configuration` beside them. The move that put every file in its part kept the imports as they
were, so a few still run upward today: the read engine reads the actions' table of readings,
`api_reader.py` reads the tuning and the profiles, the gate loads the limits item, and the
limits item validates through the rulebook. `testing/unit/test_architecture_guard.py` lists each
of them and fails when a new one appears; removing them is follow-up work, one at a time.
`connect.py` is the connect-time composition point: `connect_microscope` reaches into the gate,
the registration and the calibration (via function-local imports) to load the machine configs in
one place. It is the flow that `ZmartDriver.__init__` runs, and will fold into it.

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
- The limits gate moves inside the set dispatcher, so that no action calls it itself.
- The coordinate arithmetic (origin, orientation, objective offsets) leaves `zmart_adapter/` for
  `configuration/coordinates.py` and the actions for position.
- The setup notebooks become thin: the measuring is a procedure, the notebook only calls it.
- `scanfields/` finds a home outside the driver, or becomes a procedure.
- `zmart_controller_plugin.py` goes, once `zmart_driver.py` has driven the real STELLARIS.

## 8. Configuration & tuning (profiles)

Every command has a frozen `CommandProfile` in `actions/profiles.py` — its complete recipe (pluggable
callables + retry/confirm tuning). Tuning a command = editing its profile; nothing else changes.

```python
@dataclass(frozen=True)
class CommandProfile:
    pre_check_fn=None ; error_check_fn=_default_error_check ; confirm_fn=None
    max_retries=WINDOWS-1 ; max_confirm_attempts=WINDOWS ; refire_on_unconfirmed=True
    confirm_poll_s=WINDOW_S ; confirm_tolerance=None
    success_on_unconfirmed=True                # exhausted readback -> unconfirmed, never hard-fail
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

`testing/unit/` is offline against committed synthetic fixtures (template parsing, strip/restore,
position parsers, stage/limits, log & state readers, acquisition, runtime loading). Follow the project
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
   fail-closed. Through `connect_microscope` (and the adapter) the handshake always runs, falling back
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
9. **Edit templates only through `apply_lrp_change`** — a raw `.lrp` edit won't take effect and can
   select the wrong job after reload.
10. **`load_experiment` confirms only the receipt, not on-disk state** — follow with `save_experiment`
    (or use `apply_lrp_change`, which does).
11. **Adapter mutating ops are gated by the flat `limits.json` at the commands layer** — if the machine file
    fails to load/validate at connect, the session falls back to the bundled **default** envelope
    (loudly warned) rather than refusing everything; the connect-time warning names what happened
    (see §3). Out-of-envelope moves still refuse at the commands layer, below the adapter.
12. **The origin is loaded at connect** — `connect` loads the newest origin saved by the adapter's
    `set_origin` setup step, so the controller never sets it. With none saved, positions are plain
    stage coordinates (with a warning); a damaged origin file stops connect with a clear error
    (see §5).

## 11. Extending the driver

Adding a command touches four places, following the pattern every existing command uses:

1. **Confirm function** (`actions/confirmations.py`) — `_confirm_X(client, ...) -> {"success", "logs"}`
   (skip if no readback is possible).
2. **CommandProfile** (`actions/profiles.py`) — `MY_PARAM = _leica_setting_profile(_confirm_my_param)`.
3. **Command wrapper** (`actions/set.py`) — three phases (pre-checks → `_dispatch(...)` with the
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
- Leica filename implementation: [`output/naming.py`](output/naming.py)
