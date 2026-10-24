# navigator_expert: how the Leica driver works inside

This is the part of the Leica driver's manual for people who maintain or extend the
driver. Using the driver, and setting a microscope up, are in the driver's own
[README](../../zmart_drivers/leica/stellaris/navigator_expert/README.md). How every ZMART driver is
laid out is in [the anatomy of a ZMART driver](../driver-anatomy.md).

The short version is [How the Leica driver is built](architecture.md). The sections below are the
full notes, moved here from the README unchanged, so that the README holds only what an operator
needs.

## About the LAS X CAM API

Leica LAS X exposes automation through a **Python (CAM) API** delivered as **.NET assemblies** that
this driver loads **in-process** via `pythonnet`. Commands are issued by writing an API model and
calling `UpdateAwaitReceipt`/`UpdateAsync`; an **echo model** (`PyApiCommandEcho`) reports errors.
State is read back through the CAM API and, as a hang-proof fallback, by tailing LAS X log files.

This is the **vendor-specific** layer: it knows LAS X enum names, log paths, and how LAS X hands over
finished captures. It runs **on the LAS X PC** (the API is in-process and
blocking — unlike the gRPC/socket ZMART drivers). Keep LAS X-specific assumptions inside this package.


## Configuration

- **Connection** — `LasxApiProfile` (`primitives/cam_api/client.py`): `runtime_root` (the add-in dir) and
  `delay_ms` (Leica's client-side pacing knob `DelayInMilliseconds`, default 250 ms).
- **Log reader** — `LogReaderProfile` (`primitives/log/reads.py`): the `lcsCommand.log` / `MatrixScreener.log` paths + freshness windows.
- **Machine-local calibration & limits** — `driver_setup/store.py` resolves the instrument's calibration
  (image↔stage matrix, per-objective translation), limits, orientation, and origin from a
  machine-local ProgramData (`C:\ProgramData\zmart-microscopy\leica\stellaris_y42h93\navigator_expert`;
  on a PC set up with a version before 0.7.0b1 the folder is still called `stellaris5_y42h93`: rename
  it once to `stellaris_y42h93`, or the driver finds no limits and refuses every move).
  Directly below `navigator_expert`, each subsystem owns an independent
  timestamp tree: `limits/<datetime>/`, `calibration/<datetime>/`,
  `orientation/<datetime>/`, and `origin/<datetime>/`. The newest timestamp in each tree wins.
  Limits, calibration, and orientation seed their own repo defaults when empty. The origin has no
  repo default: it exists only once an operator saves one, and the `ZmartControllerPlugin` loads the newest one at
  connect. Setup notebooks append only to their owning tree,
  so publishing one subsystem never duplicates another. The single `limits.json` is
  flat: four typed stage-axis `range` entries, `objective_slot` with `allowed` values,
  and either a typed constraint or explicit `[]` for each setter.
- **The driver loads the configs at connect** — `connect()` (in `methods.py`) is the connection. It
  opens the CAM client (`primitives/cam_api/client.py`) and then loads this
  microscope's three machine-local configs — the **instrument limits**, the **orientation**, and the
  **calibration** — so the whole session works from one consistent picture. Normal image orientation is enabled by `IMAGE_SAVE` in `procedures/save_data/save_waiting.py`; only the
  orientation measurement explicitly requests raw pixels. Limits and calibration can still be skipped.
  This is a deliberate ladder: the limits notebook
  is bounded only by the physical backstop, `set_orientation` is bounded by limits, and
  `calibrate_objective_pair` is bounded by limits and expects a measured orientation.
- **Limits handshake** — `connect_handshake(client)` in `driver_setup/limits/handshake.py` (run by
  `connect()`; validators and setup notebooks call it directly). It resolves an operator-published ProgramData
  `limits.json`, or uses the bundled defaults directly when none exists. It validates exact flat keys, finite ranges, allowed
  objective slots, and envelope **within the hardcoded physical backstop**
  (`STAGE_BACKSTOP_UM` in `driver_setup/limits/limit_rules.py`), applies the stage envelope, and installs the limits gate
  for that client. **If the machine file is invalid (or loading is switched off), the session does not
  go dead — it falls back to the bundled default envelope** (loudly warned, marked a fallback). The
  defaults sit within the physical backstop, and the backstop bounds every move regardless, so an
  over-wide or corrupt file can never authorise a move the defaults forbid. The only fully fail-closed
  state is a client that never handshook at all (every mutating command then refuses, naming the
  notebook). Manual `set_stage_limits(...)` still adjusts the in-memory envelope, but it does not open
  the gate — only a successful handshake does.
- **Limits gate** — `dispatcher/limits.py`. The dispatcher's `run()` asks it once for every change,
  before the request is made: a refused change is never made and never sent. Stage moves use the four flat
  axis ranges and the physical backstop, objective changes use `objective_slot.allowed`, and every listed setter with `[]` is
  explicitly unrestricted. Setter constraints use `range` or `allowed`. Missing or unknown entries invalidate the file and
  trigger the defaults fallback above. Nothing built on top—the `ZmartControllerPlugin` class, controller, workflow, or
  notebook—can go around the gate.


## The controller's `get_xyz` and `set_xyz` answer

`get_xyz` and `set_xyz` give the same answer: one dictionary with the keys `x`, `y` and `z`.
Each axis has four entries, and every number in them is in micrometres. For example, with the
origin saved at stage x 63500, y 41500 and a focus of 2000 (z-wide 2000, z-galvo 0):
```python
{
    "x": {
        "position": 100.0,
        "unit": "micrometer",
        "used_actuator": "motoric",
        "actuator_readings": {"motoric": 63600.0},
        "canvas": [-62500.0, 66500.0],
    },
    "y": {
        "position": 50.0,
        "unit": "micrometer",
        "used_actuator": "motoric",
        "actuator_readings": {"motoric": 41550.0},
        "canvas": [-40500.0, 58500.0],
    },
    "z": {
        "position": 0.0,
        "unit": "micrometer",
        "used_actuator": "z-wide",
        "actuator_readings": {"z-wide": 2000.0, "z-galvo": 0.0},
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
- `used_actuator` is the motor this answer used: `motoric` for x and y, and `z-wide` for z. The driver
  always moves the focus with z-wide, because the objective offsets are measured on it; the z-galvo
  is only read, and is parked at 0 before a move if it is not there.
- `actuator_readings` lists every motor of the axis, under the names `get_actuators` gives, each with
  its own reading exactly as LAS X reports it. Nothing is subtracted from these, so they show
  how z-wide and z-galvo share the focus.
- `canvas` is `[min, max]`, everywhere a picture can show along the axis. On x and y it is the
  stage travel itself, because LAS X reports the field of view only for the objective in place
  now; on z it is the z-wide travel widened by the z-galvo travel.
- `objective_translation_um` is an extra of this driver: the `[x, y, z]` translation that was
  subtracted for the objective in place, `[0, 0, 0]` under the origin's own objective.

`set_xyz` moves, waits until every leg is confirmed, and then reads the position back from the
microscope, so its answer shows where the stage really is rather than the numbers asked for.

## Core concepts

**Connecting.** `connect()` opens the CAM client and loads this microscope's three
machine-local configs (limits, orientation, calibration), so the whole session works from one
consistent picture. The CAM client has no disconnect of its own; it lives for the process.
`close()` forgets what was loaded for the session, and every later call is refused.

**Which configs to load.** `connect(load_limits=..., load_calibration=...)` chooses whether
limits and calibration are loaded. `load_limits=False` governs the session with the bundled
**default** envelope (never ungated; the physical backstop still holds); `load_calibration=False`
refuses cross-objective moves rather than computing uncompensated ones. Normal image saves always
use the `IMAGE_SAVE.apply_orientation=True` profile default. Only orientation measurement passes
an explicit identity orientation to obtain raw camera pixels.

**The origin is part of the microscope's configuration.** The frame origin is the stage position
that the ZMART controller calls (0, 0, 0). Like the limits, orientation, and calibration, it is set
once in a setup step and then reused by every session. During that step the operator works with
the driver directly:

```python
import zmart_drivers.leica.stellaris.navigator_expert as navigator_expert

scope = navigator_expert.connect()
scope.set_origin()  # the current position becomes (0, 0, 0)
```

`set_origin` saves stage XY, both z drives, and the current objective to a new
`origin/<datetime>/origin.json`. Every later `connect` loads the newest one, so all sessions share the
same frame until the origin is captured again. The controller itself cannot change the origin; it
only forwards commands. Because the objective is saved too, a later objective change is still
compensated with the calibration, or refused when no calibration covers it.

If no origin has been saved yet, positions are plain stage coordinates and connect logs a warning.
If an origin file exists but is damaged or incomplete, connect stops with a clear error rather than
quietly using a zero origin, which would shift every position in the experiment. To recover, fix or
remove that file, or connect with `load_origin=False` and run `set_origin`
again. (Earlier versions treated the origin as belonging to one session only and did not load it at
connect; that is no longer the case.)

**Change vs. reading.** A change goes through the dispatcher's `run()` as plain data (the named
changes are in `procedures/changes.py`). A reading goes through `run()` by its name (the named
readings are in `procedures/readings.py`). Reads that gate control flow or become persisted truth
have a stricter rule (below).

**The result dictionary.** Every change, such as `change_setting` and `change_objective`, answers with a stable envelope:

| Key | Meaning |
|---|---|
| `success` | Command accepted/applied (transport ok, no permanent API error). |
| `confirmed` | Readback matched the target (`True`/`False`); `None` if no confirmation ran. |
| `message` | Human-readable summary. |
| `timing` | `{pre_check_s, setup_s, fire_s, check_s, confirm_s, total_s, attempts, confirm_attempts, method}`. |
| `logs` | Ordered `{ts, level, msg}` trace. |
| `route` | The route that carried the change: `"direct"` or `"cam"`; `None` when nothing was sent. |
| *(command-specific)* | e.g. `position` (`move_xy`). |

**`success` vs. `confirmed` — read both.** `success=True, confirmed=False` means LAS X accepted the
change but the readback did not show it within the windows. The workflow can continue, with the
mismatch in `logs`. **Don't treat `success` alone as "applied"** for setting commands.
`success=False` means it was not sent or failed (limits, transport, permanent error, failed
pre-check) and `confirmed` is `None`. The one exception is a capture whose scan was never seen
scanning: it fails.

**Error classification** (`primitives/cam_api/profile.py`): messages are matched **permanent-first**
(`out of range`, `is invalid`, `not implemented`, …) then **transient** (`being scanned`, `busy`,
`timeout`, …); unknown → permanent (conservative). Transient errors retry up to `max_retries`.

**Reading state — direct first, the CAM API behind it** (`dispatcher/read.py`, over
the three readers in `primitives/`: `direct/reads.py`, `cam_api/reads.py` and
`log/reads.py`, chosen per reading by
`StateReaderProfile` in `primitives/direct/profile.py`). Every reading asks the **direct** reader first, the default for every datum
since 13 October 2026: it reads LAS X's current state from its hardware server (`state_tree.py`),
so it is neither late like the CAM API's answers nor waiting for a log line (on the simulator, the
stage in about 1 ms against the CAM API's 20 ms). It answers the stage, the z drives, scan status,
job settings, hardware info, the selected job and the job list, with which job is Navigator
Expert's autofocus job. Only when it fails (LAS X not reachable, an answer that does not count) does
the fallback answer: `api` (CAM read requests, one at a time per connection). `log` (parse LAS X
logs; never blocks the CAM API, can be stale) and `hybrid` (the API, then the log) take part only
where the profile names them: confirming a job change falls back to `hybrid`. A reading says which
source answered. What counts as a success is declared once per reading in
`primitives/direct/profile.py` (job settings, for example, must name the job asked about and carry
an objective slot and image geometry). On the STELLARIS (15 October 2026) the direct reader was
right where the CAM API was stale: the z-galvo position while a job had a z-galvo stack, and the
selected job just after a switch. For the drive that carries the job's z-stack, the CAM API and the
log stay stale by construction, so without the direct reader that one position comes from the
experiment LAS X saves (`read_saved_z_um` in `primitives/cam_api/reads.py`): the position last
commanded through the job.

**Waiting on LAS X — one rule** (the numbers are in `primitives/cam_api/profile.py`): every reading,
confirmation and delivery waits **four windows of three seconds**. Within a window the sources the
read order allows are asked one after the other; between windows something is sent again — a
change is sent again, an unanswered read is requested again, a message is delivered again. The
driver looks again every `POLL_S` (0.1 s) and checks for an answer already requested every
`ANSWER_POLL_S` (0.01 s). After four windows a reading is unknown, a change unconfirmed, a delivery
failed. The idle check before a change is an ordinary reading under the same rule, asked of the
direct reader and, when it fails, of the API alone, never the log: whether a change is sent must
not rest on a log line that may be old. Outside the rule, by decision: the capture (it waits for
the scanner to be idle as long as it takes, is sent once and never again, must be seen scanning
within 15 s, and is watched for as long as the scan takes), the one-second wait for LAS X's error
report after a command, and waits on files being written. **Freshness rule:** a fresh-by-age
*log* value must never decide whether a change is sent, how it is parameterized, whether it
confirms, or what metadata/calibration is persisted — those must use a source that answers on
demand: the direct reader, or the CAM API.

**Units.** Positions are micrometres from the saved origin, in `position()`, `move_to()` and every
plane of a saved capture.

**Logging:** every message of the driver also goes to its running log, `running_log.txt` in the
`logs` folder beside the saved settings. `logging.getLogger("zmart_drivers.leica.stellaris.navigator_expert")`
is the logger of the whole driver; the same trace also travels in each result's `logs`.

## Pictures: taking, saving and clearing

**Taking a picture, saving it and clearing LAS X's cache are three steps.** `acquire` (in
`procedures/acquire.py`) runs the selected job, writes nothing, and keeps a note of how the
picture was taken (where the stage stood, which job ran, the state of the microscope). The
`save_data` procedure then writes the waiting pictures, and `clear_cache` lets LAS X go of the
pictures that were saved. Used by itself, these are `scope.acquire()`, `scope.save()` and
`scope.clear_cache()`. The controller's `acquire` runs the first two in order and never the
third; through the controller, run `save_data` or `clear_cache` with
`run_procedure({"name": "save_data"})` and `run_procedure({"name": "clear_cache"})`.

Saving collects the capture from LAS X's cache as LAS X writes it (LAS X's AutoSave is not used)
and writes it as one OME-Zarr store. They land in `<output_root>/data/`, so what is made from
them later (a stitched view, an analysis) becomes a folder beside `data` and is
never confused with it. Everything describing the capture is under
`data/metadata`, one folder per party:

```
data/
  overview_K00_M000001_G000001_P000000_V00.ome.zarr
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

The microscope's state at the capture is embedded in every plane's OME-XML **and**
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
(`procedures/clear_cache/clear.py` says how). Only pictures whose copy was written and read back
identical are on its list, and the Navigator canvas is then cleared, which lets go of each
capture's hidden copy. The driver never deletes a file itself. A picture it cannot let go of stays
on the list and is reported, so a later call can try again. On the LAS X simulator (21 October
2026), `testing/hardware/check_cache_release.py` ran `clear_cache` after each of ten captures:
every canvas was cleared, and the hidden copies did not grow.

**Positions are never made in the Navigator Expert.** They come from the ZMART interface.
`acquire()` captures at the current position.

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

## Architecture

The driver is laid out as the anatomy of a ZMART driver describes
([`docs/driver-anatomy.md`](../driver-anatomy.md) in this repository). What another
microscope must bring is in [`docs/vendor-contract.md`](../vendor-contract.md).

```
zmart_drivers/leica/stellaris/navigator_expert/
├── __init__.py         connect() and Microscope, nothing else
├── methods.py          what the user calls: connect(), the Microscope (section 4), its Session
│                       (the open connection, the origin, the orientation and calibration loaded
│                       at connect, the captures waiting to be saved, the changes never confirmed)
│                       and the driver's running log
├── procedures/         chains of steps; one file each, a folder when it needs more:
│   ├── changes.py      the named changes: move_xy, move_z, set_objective, change (a job
│   │                   setting), select_job, acquire, take_picture
│   ├── readings.py     the named readings: get_xy, get_selected_job, get_scan_status, ...
│   ├── absolute_coordinate_system.py   the one coordinate system: position, move_to, motors,
│   │                   zero_z_galvo, with the origin and the objective offsets
│   ├── keep_in_view.py changing the objective or the job without losing the sample
│   ├── acquire.py      taking a picture, and the note kept for saving it (PendingCapture)
│   ├── state.py        get_state and set_state
│   ├── answer_dialogs.py   noticing a LAS X warning and pressing OK
│   ├── clear_cache/    having LAS X let go of saved pictures: clear.py · data_container.py ·
│   │                   navigator.py (LAS X's window)
│   └── save_data/      writing the waiting pictures as OME-Zarr, placed by the measured image
│                       orientation: save_waiting.py · save.py · collect.py · metadata.py ·
│                       product.py · naming.py · ome_zarr.py · ome_canonical.py
├── dispatcher/         the same in every driver; imports nothing of the driver:
│                       dispatch.py (run(): a reading or a change, and the shape of a result) ·
│                       read.py (the read loop) · write.py (the write loops: send, then confirm;
│                       the chain of routes) · limits.py (the limits gate)
├── primitives/         one folder for each way of reaching LAS X:
│   ├── cam_api/        the CAM API: client.py (open it) · transfer.py · reads.py · writes.py
│   │                   (every change, as plain data) · parsing.py (what LAS X hands back) ·
│   │                   profile.py (the waiting rule, LAS X's reply checked and sorted)
│   ├── direct/         LAS X's hardware server: client.py · transfer.py · reads.py · writes.py
│   │                   (what each CAM request becomes) · state_tree.py · profile.py (the read
│   │                   order, what counts as an answer, the routes; the profile run() is handed)
│   └── log/            LAS X's log files: reads.py · profile.py (how old an answer may be)
├── driver_setup/       everything for setting up this driver on one microscope, one folder per item:
│                       limits/ (with limit_rules.py, what the limits allow), origin/,
│                       orientation/, optical_calibration/ and data_location/. Each holds its
│                       bundled defaults, its loader, the code that measures it and its notebook.
│                       store.py finds the saved values under ProgramData; notebook_support.py
│                       helps the notebooks
├── zmart_controller_plugin/   everything that faces the controller, which finds it here by name:
│   ├── zmart_controller_plugin.json   the driver's name ("stellaris") and how to reach LAS X on this computer
│   ├── zmart_controller_plugin.py     the ZmartControllerPlugin class the controller calls, one method per command,
│   │                       each a call of the Microscope
│   ├── info.py             the description get_info answers
│   └── procedure_menu.py   the named procedures get_procedures lists and run_procedure runs
├── testing/            unit/ (offline) · calibration/ (the calibration suites) · hardware/
│                       (validate_*.py live scripts + mock-backed test_* gates) · helpers/ (the
│                       LAS X mock) · data/
└── (the driver's CI, pytest.ini, .coveragerc and requirements-dev.txt live in testing/)
```

**The model.**

```
methods ──► procedures ──► dispatcher run() ──► primitives ──► LAS X
```

**How a change goes through `run()`.** A change is plain data, a `Write` built in
`primitives/cam_api/writes.py`. `procedures/changes.py` hands it to
`dispatch.run(direct_profile, client, write, dialogs=answer_dialogs)`:

```
run()
 ├─ limits gate, once: refused → nothing is made or sent
 ├─ make the request (the direct profile)
 └─ confirm_and_fire (dispatcher/write.py)
     ├─ fire block (≤ max_retries + 1): wait for idle → fill → answer dialogs, send → check the reply
     │                                  → send again on a temporary error
     └─ read back, one window (≤ max_confirm_attempts): not shown → wait for idle, send again
```

A change marked `never_resend` (the capture) is sent once and gets one window. The write loops are
deliberately dumb: they own the order of the steps, the retry ceilings and the timing, and know
nothing of zoom, objectives or stages.

A reading is run by its name: `run(direct_profile, client, "xy")`. The direct reader answers first,
in the caller's own thread; when it cannot, the fallback in its read order answers.

### Routes: direct first, the CAM API behind it

Every change the driver makes goes **first to LAS X's hardware server** (the direct route) as the same
element LAS X itself logs for that command, and **to the CAM API only when the direct route could
not send it**: the hardware server is not reachable, or the command has no direct form. Every
reading likewise asks the direct reader first and then the CAM API.

- **Which route carried a command** is in its result, under `route` (`"direct"` or `"cam"`), and in
  its log as `sent through the direct route`. A fallback is a warning that names the reason.
- **A command is never done twice.** Once an element has gone out to LAS X, the CAM API never takes it
  over, even when LAS X refused it or did not answer: it may already have been carried out. A
  capture is never sent again on either route.
- **Every direct change goes through the same limits gate** as a CAM change, and LAS X's own
  refusals are reported as they come.
- **To change the order**, set the environment variable `ZMART_WRITE_ROUTES` before the driver
  starts: `direct,cam` (the default), `cam` (the CAM API only, as in earlier versions) or `direct`
  (no fallback). The order is read for every command.
- **Acquire** names a Data Container project for its image. The direct route chooses one that is open
  in LAS X: the project of the last image, else the one remembered from an earlier acquire, else
  the newest open project, else a new `ZMART_Experiment<N>`.
- **An experiment file is saved only to read the stacked z drive**, when the hardware server cannot be
  reached: under one name, `{ScanningTemplate}_ZMART_z`, in Navigator Expert's ScanningTemplates
  folder, overwritten each time. Loading an experiment is not offered.

**Where the routes are set up.** `primitives/direct/profile.py` lists the ways of sending that
exist (`ROUTES`: `direct` and `cam`) and the order they are tried (`WRITE_ROUTES`, which
`ZMART_WRITE_ROUTES` overrides). The dispatcher's `TransportChain` (`dispatcher/write.py`) uses the
first route that can send and moves on only when that route reports that nothing has left yet.
`primitives/direct/transfer.py` is the direct route and `primitives/direct/writes.py` holds what each
command becomes at the hardware server. Reading works the same way: the direct reader first, then
the family in `StateReaderProfile.reading_fallback` (the CAM API, `"api"`), and `waiting_fallback`
for the readings a change waits on.

**Dependency direction.** The primitives import only the dispatcher (and each other). The dispatcher
imports nothing of the driver. The procedures and `driver_setup/` use the primitives, the dispatcher
and each other; `methods.py` uses all of them. `zmart_controller_plugin/` uses `methods.py`, and
reaches into the procedures, the readings and the limits for `get_info` and the procedure menu.
`testing/unit/test_architecture_guard.py` holds
the table and fails on any import against it, and on any file or folder outside the anatomy.
The connect-time flow lives in `connect()` (`methods.py`): it opens the client, installs the limits
gate, and loads the orientation and the calibration.

## Configuration & tuning (profiles)

Every change is one `Write` (`primitives/cam_api/writes.py`): its name and values for the limits,
the LAS X request and how it is filled in, and what belongs to this change alone. Every setting of a
job is one row of the table `SETTINGS` there. Tuning a change = editing its `Write`; nothing else
changes.

```python
@dataclass(frozen=True)
class Write:
    command: str            # its name in the limits
    values: dict            # what the limits gate is shown
    request: str            # e.g. "PyApiMoveHardwareXY"
    fill: Callable          # writes the fields into the request
    description: str
    ready: Expect | None    # waited for before every send (the scanner idle)
    confirm: Expect | None  # read back, one window at a time, and how close counts
    without_receipt: bool   # UpdateAsync, no reply checked
    accepted_replies: tuple # LAS X replies that mean "already so"
    never_resend: bool
    experiment: Callable | None  # opens the experiment a capture goes into
```

How every change is sent is one profile, `WRITING` in `primitives/direct/profile.py`: up to
`WINDOWS - 1` sends again after a temporary error, `WINDOWS` read-back windows, and sent again
between them. The capture is the one exception: `never_resend`, sent once, after the scanner is idle
however long that takes. Which source each reading asks is `StateReaderProfile` in the same file.

**Default tolerances** (override per call via `tolerance=`):

| Command | Tol | Unit | | Command | Tol | Unit |
|---|---|---|---|---|---|---|
| `set_zoom` | 0.1 | factor | | `set_pinhole_airy` | 0.05 | AU |
| `set_scan_field_rotation` | 0.5 | deg | | `set_detector_gain` | 1.0 | gain |
| `set_z_stack_definition` | 1.0 | µm | | `set_laser_intensity` | 0.005 | frac |
| `set_z_stack_step_size` | 0.5 | µm | | `set_filter_wheel_spectrum` | 1 | nm |
| `set_z_stack_size` | 1.5 | µm | | `move_xy` | 20.0 | µm |
| | | | | `move_z` | 1.0 | µm |

## Testing

```powershell
# Offline suite (no microscope, no LAS X), from the repository root
python -m pip install -e ".[leica,test]"
python -P -m pytest -q zmart_drivers/leica/stellaris/navigator_expert/testing/unit
python -P -m pytest -q zmart_drivers/leica/stellaris/navigator_expert/testing/calibration

# Self-contained gates
python zmart_drivers/leica/stellaris/navigator_expert/testing/run_ci.py             # mock/offline (default)
python zmart_drivers/leica/stellaris/navigator_expert/testing/run_ci.py --mock      # explicit mock/offline
python zmart_drivers/leica/stellaris/navigator_expert/testing/run_ci.py --hardware         # live: the quick acceptance (about five minutes)
python zmart_drivers/leica/stellaris/navigator_expert/testing/run_ci.py --hardware --full  # live: plus the reader probe, parity, and the api/log routes
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
python -m pytest -q zmart_drivers/leica/stellaris/navigator_expert/testing/hardware   # offline mock gates
python zmart_drivers/leica/stellaris/navigator_expert/testing/hardware/validate_hardware.py --yes --allow-xy --allow-z --allow-objective --allow-acquire --state-reader-mode hybrid
```
Validator JSONL outputs are runtime artifacts, ignored by default. Every validator run also
writes a **Markdown run report** (`hardware_run_report_<timestamp>.md`, in `testing/report/` when
launched via run_ci) listing every attempted instrument change — including failures and
restores — with confirmation status and timing. **Bench-run instructions** (prerequisites, what
`--hardware` changes on the scope, expected duration, report locations) live in
[`testing/hardware/README.md`](../../zmart_drivers/leica/stellaris/navigator_expert/testing/hardware/README.md).

## Invariants & gotchas

These **silently misbehave** instead of failing loudly — respect them or results are wrong without an error:

1. **Movement needs a limits handshake** — a client that never handshook refuses every move
   fail-closed. Through `connect()` (and so the `ZmartControllerPlugin` class) the handshake always runs, falling back
   to the bundled default envelope if the machine file is invalid, so a connected session can always
   move within the defaults; the physical backstop bounds every move regardless.
2. **`acquire()` writes nothing** — it raises when the capture fails, and otherwise keeps the
   picture in LAS X's cache. Writing the files is the separate `save()`.
3. **For a change, check `confirmed`** — `change_setting` answers `confirmed=False` when the
   readback never matched; the change is recorded in `state()` and the mismatch is in `logs`.
4. **Reads that gate control flow or get persisted must use a source that answers on demand** (the
   direct reader, or the CAM API) — never let a fresh-by-age log value decide whether a change is
   sent or what metadata/calibration is written.
5. **The CAM API can hang** — that's why the direct reader goes first, CAM API reads take turns one
   at a time per connection, and a read is given up at the end of its share of the window.
6. **`select_job` is confirmed by the direct reader; its fallback is `hybrid`** — a stale API readback
   can report the wrong job after a switch, so when the API's answer from before the change is
   missing or already names the target, the log alone confirms.
7. **Objective changes are best-effort** — a manual turret may pop a "turn the turret manually" dialog
   (surfaced in `MatrixScreener.log` / `get_pending_dialog`); prefer binding the objective via the job.
8. **`PyApiAcquireJob` silently no-ops without `m.JobName`** — returns in ~0 s with no error; the driver
   sets it in the write's `fill` (`primitives/cam_api/writes.py`). Check it before assuming a LAS X bug.
9. **Every change is gated by the flat `limits.json` in the dispatcher's limits gate** — if the machine file
    fails to load/validate at connect, the session falls back to the bundled **default** envelope
    (loudly warned) rather than refusing everything; the connect-time warning names what happened
    (see §3). Out-of-envelope moves still refuse at the gate, below the `ZmartControllerPlugin` class.
10. **The origin is loaded at connect** — `connect` loads the newest origin saved by the driver's
    `set_origin` setup step, so the controller never sets it. With none saved, positions are plain
    stage coordinates (with a warning); a damaged origin file stops connect with a clear error
    (see §5).

## Extending the driver

**A new setting of a job** is one entry in the table `SETTINGS` in `primitives/cam_api/writes.py`:
its name, its arguments, the LAS X request and how it is filled in, which argument holds the value,
where the value stands in the job's settings read back, and its tolerance. `change_setting` then
changes it, and the limits know it as `set_<name>` (`driver_setup/limits/limit_rules.py`); add a row
to the table of settings in section 4. Without an entry in `primitives/direct/writes.py` the CAM API
carries it.

**A new kind of change** is a builder in `primitives/cam_api/writes.py` that returns a `Write`
(section 8), and a named change in `procedures/changes.py` that checks its arguments and hands the
`Write` to `dispatch.run(direct_profile, client, write, dialogs=answer_dialogs)`. The limits, the
idle wait, the send and the read-back are the dispatcher's. The `Microscope` (`methods.py`) gets a
method for it and a row in section 4; a test checks that every method of the `Microscope` is in this
README.

Copy the closest existing change of a similar shape.

## References
- ZMART Controller (the microscope-independent layer this driver plugs into). It lives in its own
  repository; until that repository exists, it is the
  [`release-candidate-zmart-controller`](https://github.com/ZMB-UZH/ZMART-microscopy/tree/release-candidate-zmart-controller)
  branch of ZMART-microscopy.
- Sibling drivers, still under construction in the main ZMART-microscopy repository:
  [ZEISS ZEN API](https://github.com/ZMB-UZH/ZMART-microscopy/tree/main/zmart_drivers/zeiss/zenapi) (gRPC) and
  [Nikon NIS-Elements](https://github.com/ZMB-UZH/ZMART-microscopy/tree/main/zmart_drivers/nikon) (socket macro).
- Leica filename implementation: [`procedures/save_data/naming.py`](../../zmart_drivers/leica/stellaris/navigator_expert/procedures/save_data/naming.py)
