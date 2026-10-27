# rdk — Evident FLUOVIEW FV4000 microscope driver

`rdk` drives an **Evident FLUOVIEW FV4000** confocal from Python, through FLUOVIEW's Remote
Development Kit (RDK), the FV40S-RMTDK option. It works on its own, from a notebook or a script, and it
is the Evident driver behind the ZMART Controller. Every change is checked against this microscope's
limits before it is sent, and read back afterwards, so you know what the microscope really did.

- **Author:** Thom de Hoog (ZMB, University of Zurich) · thom.dehoog@zmb.uzh.ch · thomdehoog@gmail.com
- **License:** see the repository root [`LICENSE`](../../../../LICENSE).
- **Status:** **Beta (`0.7.0b9`), written from the manual and tested offline only.** The driver is
  built from the FV40S-RMTDK instruction manual (FLUOVIEW FV4000 remote development kit) and tested
  against a pretend FLUOVIEW that follows it. It has not yet run against an FV4000 (27 October 2026).
  The first thing to run at the microscope is the hardware check in
  [`testing/hardware/README.md`](testing/hardware/README.md); the section
  [What to check at the microscope](#what-to-check-at-the-microscope) below lists what was assumed
  from the manual and must be looked at there.
- **Known limits of this beta:**
  - The z axis is FLUOVIEW's focus motor (its "Z stage"). A z piezo, when there is one, is read and
    reported beside it but never moved to focus; `move_piezo` moves it on request.
  - Multi-area (MATL) imaging is set up through `change_setting`, but `acquire` captures with the
    main protocol only; a MATL run is not read back and saved by the driver.
  - The image size and pixel size of the current settings are not reported by FLUOVIEW's interface.
    The driver learns them from the first picture under each objective; until then the state says
    they are not known. See [Setting up the driver](#setting-up-the-driver).
  - A laser power outside what the laser can give is clipped by FLUOVIEW, as the manual says; the
    driver records such a change as unconfirmed rather than failing it.

## How to call the driver

The driver talks to FLUOVIEW over the network (XML-RPC, port 8080 by default), so it runs on the
FLUOVIEW computer or on any computer that reaches it. FLUOVIEW must be open with the RDK switched on.
Install the driver with Python 3.11 or newer, from the repository's root folder:

```bash
pip install -e ".[evident]"
```

Set the microscope up once before the first experiment (see [Setting up the driver](#setting-up-the-driver)
below). Then every session starts with `connect()`, which reaches FLUOVIEW, puts it in REMOTE mode,
loads this microscope's settings and hands back a `Microscope`. Each call in the table below is a method
of that `Microscope`:

```python
import zmart_drivers.evident.fv4000.rdk as fv4000

scope = fv4000.connect()  # FLUOVIEW on this computer
# scope = fv4000.connect(host="10.100.100.100")       # FLUOVIEW on another computer

scope.move_to(100.0, 250.0, 0.0)  # micrometres from the saved origin
scope.change_setting("z_stack_slices", value=21)  # read back from FLUOVIEW
scope.set_state({"channels": {"CH1": {"hv": 550}}})  # several settings at once
scope.acquire("A1")  # the imaging set up in FLUOVIEW runs
scope.save()  # written as OME-Zarr

scope.close()  # FLUOVIEW goes back to LOCAL mode
```

What every call does for you:

- **The limits are asked first.** A move or a setting outside this microscope's limits is refused
  before anything is sent.
- **Every change is read back.** A change that was sent but never seen is recorded, and `state()` lists
  it; the work goes on.
- **A capture is sent once.** However busy FLUOVIEW is, a capture is never sent a second time, so you
  never get two pictures for one call.
- **FLUOVIEW is handed back.** `close()` puts FLUOVIEW in LOCAL mode again, so the person at the screen
  can use it.

The connection words `connect()` takes, with their defaults, are in `DEFAULT_CONNECTION` in
`methods.py`: `host` (127.0.0.1), `port` (8080), `remote_mode` (True: take REMOTE mode while
connected), `escape_objective` (False: lower the objective during XY moves, as FLUOVIEW's escape
does), `config` (a folder of settings to use, see below), `output_root` (where this session saves) and
the three `load_*` switches.

## The calls

| Call | What it does |
|---|---|
| `position()` | Where the stage is: `x`, `y` and `z` in micrometres from the origin, each with its drives' own readings (`z` shows the focus motor and the piezo) and the range a picture can be taken in. |
| `move_to(x, y, z)` | Move there, and answer as `position()`, read back once the stage has arrived (within 1 µm in XY, 0.5 µm in z). |
| `motors()` | The drives of each axis, and the one used to move it: the stage for x and y, the focus motor for z. |
| `set_origin()` | Make the current position the origin, and save it for every later connection. |
| `move_piezo(z_um)` | Move the z piezo to an absolute position, within its own limits. The focus motor does not move. |
| `objectives()` | The objectives on the nosepiece, by slot and name, and the one in use. |
| `change_objective(slot=None, name=None)` | Turn to another objective, and move the stage by the measured offset so the same spot stays in view. Refused until the calibration covers both objectives. |
| `settings()` | The settings `change_setting` can change, each with the arguments it takes. |
| `change_setting(name, **arguments)` | Change one setting and read it back; see the table below. |
| `load_settings_from(image_path)` | Recall the whole imaging setup a saved picture was taken with (FLUOVIEW's `loadParameter`). The path is as FLUOVIEW's computer sees it. |
| `acquisition_settings()` | How a capture is triggered: `full`, the imaging set up in FLUOVIEW. |
| `acquire(position_label=None, acquisition_type="acquisition", output_folder=None)` | Start the imaging set up in FLUOVIEW (channels, z-stack, time lapse as they are), wait until it has run, and keep a note of the capture. Nothing is written by the driver yet; FLUOVIEW writes its own `.oir` file. |
| `save(label=None)` | Read the waiting captures back from FLUOVIEW and write them as OME-Zarr, with a projection and a log. |
| `stop()` | Stop the imaging that is running. |
| `live(on=True)` | Start or stop FLUOVIEW's live, repeating scan, as the Live button does. |
| `state()` | Everything changeable (objective, scanner, z-stack, time lapse, channels, lasers) and what can only be read: the FLUOVIEW version, the scan state, the limits in force, what is set up, the changes that were never confirmed. |
| `set_state(changeable)` | Apply several settings at once, in a fixed order: the objective first (with its compensation), then the rest. |
| `close()` | Hand FLUOVIEW back (LOCAL mode) and close the session. Every later call is refused. |
| `client` | The RDK client itself, for the setup notebooks. |

### The settings `change_setting` can change

The name is the first column. The arguments are given by name, for example
`change_setting("laser_intensity", laser_id="LASER_488", value=2.5)`. Channel and laser ids are the
ones FLUOVIEW reports (`state()["changeable"]["channels"]` and `["lasers"]`). Lengths are in
micrometres and are sent to FLUOVIEW in nanometres; the read-back is exact unless a tolerance is given.

| Name | Arguments | FLUOVIEW setting |
|---|---|---|
| `channel_enable` | `channel_id`, `enable` | `LSM_CHANNEL_ENABLE_SETTING` |
| `channel_hv` | `channel_id`, `value` (a PMT's high voltage) | `LSM_CHANNEL_HV_SETTING` |
| `channel_gain` | `channel_id`, `value` | `LSM_CHANNEL_GAIN_SETTING` |
| `channel_offset` | `channel_id`, `value` | `LSM_CHANNEL_OFFSET_SETTING` |
| `channel_digital_gain` | `channel_id`, `value` | `LSM_CHANNEL_DIGITALGAIN_SETTING` |
| `channel_laser` | `channel_id`, `laser_id` (the laser this channel scans with) | `LSM_CHANNEL_LASER_SETTING` |
| `laser_enable` | `laser_id`, `enable` | `LSM_LASER_ENABLE_SETTING` |
| `laser_intensity` | `laser_id`, `value` in percent; FLUOVIEW clips what the laser cannot give | `LSM_LASER_INTENSITY_SETTING` |
| `z_stack_enable` | `enable` | `LSM_Z_COORDINATE_ENABLE_SETTING` |
| `z_stack_slices` | `value` | `LSM_Z_COORDINATE_SLICE_NUM_SETTING` |
| `z_stack_step` | `value_um` | `LSM_Z_COORDINATE_STEP_SIZE_SETTING` |
| `z_stack_start`, `z_stack_end` | `value_um`, counted from FLUOVIEW's own Z origin | `LSM_Z_COORDINATE_START_SETTING`, `..._END_SETTING` |
| `z_stack_range` | `value_um` | `LSM_Z_COORDINATE_RANGE_SETTING` |
| `z_origin` | `value_um`: FLUOVIEW's Z origin, the zero of a z-stack | `LSM_Z_COORDINATE_BASE_SETTING` |
| `z_type` | `method`: how the stack is defined, as FLUOVIEW names it | `LSM_Z_COORDINATE_TYPE_SETTING` |
| `time_lapse_enable` | `enable` | `LSM_TIME_LAPS_ENABLE_SETTING` |
| `time_lapse_repeat` | `value` | `LSM_TIME_LAPS_REPEAT_TIMES_SETTING` |
| `time_lapse_interval` | `value_s` (sent in the finest unit FLUOVIEW offers for it) | `LSM_TIME_LAPS_INTERVAL_SETTING` |
| `scanner_type` | `value`: `GALVANO` or `RESONANT` | `LSM_SCANNER_TYPE_SETTING` |
| `scanner_mode` | `value`: `ONEWAY` or `ROUNDTRIP` | `LSM_SCANNER_MODE_SETTING` |
| `brightness_adjustment_create`, `_enable`, `_delete_all` | none; `enable`; none: the brightness-over-depth points of a stack | `LSM_BRIGHTNESS_ADJUSTMENT_*` |
| `matl_create` | `columns`, `rows`: a new multi-area group | `MATL_ROI_CREATE` |
| `matl_well_duplicate` | `source_well`, `target_well` | `MATL_ROI_WELL_DUPLICATE` |
| `matl_delete`, `matl_enable` | `group_id`; `group_id`, `enable` | `MATL_ROI_DELETE`, `MATL_ROI_ENABLE_SETTING` |
| `matl_xy`, `matl_z`, `matl_z_origin` | `group_id`, `x_um`, `y_um`; `group_id`, `z_um`: a group's position, checked against the limits like a move | `MATL_ROI_XY_POSITION_SETTING`, `..._Z_POSITION_SETTING`, `..._Z_ORIGIN_SETTING` |
| `matl_area_delete`, `matl_area_enable`, `matl_area_z_range` | `group_id`, `column`, `row` (and `enable`) | `MATL_ROI_AREA_*` |

The vocabulary of a limits file is one entry per setting, `set_<name>`, so a microscope can bound each
of them (a range or a list of allowed values); `driver_setup/limits/defaults/limits.json` shows them all.

## Setting up the driver

Once per microscope, before the first experiment, the driver needs five things about this microscope:
where its pictures go, how far the stage may travel, the point that counts as (0, 0, 0), which way the
picture is turned relative to the stage, and how far the sample shifts between objectives. A sixth is
learned by itself: what one picture covers.

| What | Notebook or call |
|---|---|
| Data location | `driver_setup/data_location/notebooks/set_data_location.ipynb` |
| Limits | `driver_setup/limits/notebooks/set_limits.ipynb` |
| Origin | `scope.set_origin()` from Python, with the stage on the spot that should be (0, 0, 0) |
| Orientation | `driver_setup/orientation/notebooks/set_orientation.ipynb` |
| Objective calibration | `driver_setup/optical_calibration/notebooks/calibrate_objective_pair.ipynb` |
| Frame (image size and pixel size) | Take one picture under each objective and save it; the driver remembers them from then on. |

Do the limits first: the orientation is measured within them, and the objective calibration needs both.
This driver has no setup wizard yet; the notebooks do the same work step by step.

**Where the settings are kept.** On this computer, in
`C:\ProgramData\zmart-microscopy\evident\fv4000\rdk\` (or under the folder the environment variable
`ZMART_MICROSCOPY_ROOT` names), never in the repository. Each item has its own folder there, with one
dated folder per time it was saved; the newest is the one `connect()` loads. Nothing is overwritten, so
you can always see what the microscope was told and when. A set of settings can be kept apart in a
config folder (`config_YYYYMMDD_HHMM`) and chosen when connecting with `connect(config=...)`.

**What happens when something is missing.**

- **No limits saved yet, or a damaged limits file:** the session runs within the driver's cautious
  default limits, and says so loudly. The stage's physical backstop always holds. Note that the bundled
  backstop and defaults are placeholders around FLUOVIEW's zero until the travel has been measured on
  an FV4000 (see below).
- **No origin saved yet:** positions are plain FLUOVIEW stage coordinates, and `connect()` warns. A
  damaged origin file stops the connection with how to fix it, because a guessed origin would shift
  every position.
- **No data location saved yet:** nothing is saved until one is set. A session or a capture may still
  name its own folder.
- **No orientation or calibration measured yet:** the driver fills in neutral defaults (no turn, no
  offsets) the first time it connects. A move to another objective is refused until the calibration
  covers it.
- **No picture taken yet under this objective:** `state()` reports the image size and pixel size as not
  known, and the ZMART Controller's validation says so. One capture and save fills them in.

### Where the driver expects FLUOVIEW

| Purpose | Default |
|---|---|
| The RDK endpoint | `http://<host>:8080/xmlrpc`; the port is set in FLUOVIEW's `cellSens_FV.ini` with `-rdkport` |
| Time between two calls | at least 20 ms, so FLUOVIEW is never flooded (a flood closes the socket, error 10054) |
| One call's timeout | 30 s |
| Connections | one per call, opened and closed again; a call is sent once, never again by the network layer |
| The pictures FLUOVIEW writes | the folder set in FLUOVIEW; the driver reads them back through the RDK's IDA calls, so they need not be reachable from this computer |

These numbers live in `primitives/rdk/client.py`; the waiting rules in `primitives/rdk/profile.py`.

## What to check at the microscope

Everything below was taken from the manual and is marked in the code. The hardware check
(`testing/run_ci.py --hardware`) reads most of it; the rest needs a look at the microscope.

1. **The stage travel and the backstop.** `STAGE_BACKSTOP_UM` in `driver_setup/limits/limit_rules.py`
   is ±200 mm in X and Y, ±20 mm in Z and ±1 mm for the piezo around FLUOVIEW's zero: wide placeholders,
   not measured. Measure the travel with the limits notebook, then tighten the backstop to the real
   hardware.
2. **The focus motor and the piezo.** The driver moves the focus motor (`Z_STAGE_POSITION_SETTING`,
   absolute) and only reads the piezo. How the two add up to the focus, and whether a z-stack is swept
   with the motor or the piezo, is not known from the manual; a stack's plane heights are taken from the
   picture itself.
3. **The objective slot.** The manual's set of `LSM_MICROSCOPE_OBJECTIVE_LENS_CURRENT_SETTING` takes the
   slot as a string, while its get answers a number; the driver sends a string, as the manual says
   (`REVOLVER_INDEX_AS` in `primitives/rdk/writes.py`). If FLUOVIEW refuses it with
   INVALID_PARAMETERS, switch it to `int`. The manual does not name the struct the current objective
   comes back in, nor the list a channel's lasers come back in; the driver looks for them in the reply
   and in any struct or list inside it.
4. **`z_type`.** The manual names two keys for the stack method; the driver sends `method` only.
5. **The time-lapse unit.** The interval is sent in the finest unit the manual lists; check that the
   read-back agrees for an interval in minutes.
6. **The direction of a saved stack.** The lowest plane is stored first, as read from the picture's axis
   positions; confirm with a sample that has an obvious top and bottom.
7. **The orientation and the pixel size** of a saved picture, with the orientation notebook and a
   stage micrometer.
8. **The state of a live scan.** The manual allows `Repeat.stop` only in RESTING or WAITING, which
   suggests a live scan reports RESTING. The driver counts any state but IDLING as the live scan
   running; check which one FLUOVIEW shows.
9. **Long moves.** A move is read back for four windows of 3 s, and sent again between windows. An
   XY move or an objective change that takes longer than about 12 s is reported unconfirmed even if
   it arrives. Time the longest move across the measured travel; if it comes near 12 s, lengthen the
   windows in `primitives/rdk/profile.py`.

## More about the driver

- **How it is built:** [`docs/evident/architecture.md`](../../../../docs/evident/architecture.md): its
  parts, how a call travels to FLUOVIEW, what a change answers, pictures and coordinates, in short
  tables; and [`docs/evident/rdk-notes.md`](../../../../docs/evident/rdk-notes.md): what the manual
  says that the driver relies on.
- How every ZMART driver is laid out: [`docs/driver-anatomy.md`](../../../../docs/driver-anatomy.md);
  what another microscope must bring: [`docs/vendor-contract.md`](../../../../docs/vendor-contract.md).
- The tests: `python zmart_drivers/evident/fv4000/rdk/testing/run_ci.py` runs the offline suite from
  the repository root, in two layers: the driver on its own, and the driver inside the ZMART Controller
  when that is installed. The suite runs against a pretend FLUOVIEW
  (`testing/helpers/mock_fluoview.py`) that follows the manual: the same result codes, scan states, the
  stage that takes a moment to arrive, the picture read back in pieces, and a real XML-RPC server for
  the transport test. The checks at the microscope are in
  [`testing/hardware/README.md`](testing/hardware/README.md).
