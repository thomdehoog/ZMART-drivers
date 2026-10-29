# nis_elements_ar7 — Nikon NIS-Elements AR 7 microscope driver

`nis_elements_ar7` drives a **Nikon microscope** from Python, through **NIS-Elements AR 7**. It works
on its own, from a notebook or a script, and it is the Nikon driver behind the ZMART Controller. Every
change is checked against this microscope's limits before it is sent, and read back afterwards, so you
know what the microscope really did.

- **Author:** Thom de Hoog (ZMB, University of Zurich) · thom.dehoog@zmb.uzh.ch · thomdehoog@gmail.com
- **License:** see the repository root [`LICENSE`](../../../LICENSE).
- **Status:** **Alpha (`0.1.0a1`).** Tested offline only, against a pretend NIS-Elements that behaves as
  the NIS 7 study showed (steps 0, 1 and 2 of thomdehoog/nis-useq-aiagent, run on NIS-Elements AR
  7.01.03 with the microscope switched off). It has **not yet driven a real microscope**. Before it
  does, run the hardware check inside NIS (see [Testing](#testing)): it asks NIS the questions only a
  switched-on microscope can answer (listed in [the plan](../../../docs/nikon/PLAN_NIS_ELEMENTS_AR7.md),
  sections 11 and 13), such as whether a stage move waits until the stage has arrived, whether the TIFF
  that NIS saves holds the camera's own values, and whether each hardware setting's function takes the
  arguments the driver sends.
- **Known limits of this alpha:**
  - There is no objective calibration. Changing the objective does not move the stage to keep the same
    spot in view, so positions shift by the difference between the two objectives.
  - The focus is not moved while Perfect Focus holds it: switch Perfect Focus off in NIS first.
  - One picture at a time (`snap`): no z-stacks, time series or ND acquisitions yet; ZMART workflows
    build those from single moves and pictures.
  - The camera's own settings (exposure, gain, binning) are set through an optical configuration; NIS
    offers its camera functions only with the camera on, so they are not in the table yet.
  - The hardware settings' function arguments follow Nikon's macro reference and are marked unconfirmed
    until the hardware check has tried them.
  - The picture size is known only once one picture has been taken with the current objective and
    optical configuration. Until then `state()` says so, and the ZMART Controller's check of the
    driver reports that one gap.

## How it reaches NIS-Elements

NIS-Elements cannot be driven from another program directly. It runs macros, though, and a macro can
start Python inside NIS. So the driver brings a small program, **the bridge**, that runs inside NIS and
answers the driver's requests over a connection on this computer only. A macro, `start_bridge.mac`,
starts it and keeps it running. While that macro runs, every request the driver sends is carried out on
NIS's own thread, one at a time, as if you had run it from NIS's macro window.

The bridge is careful in a few ways that matter at the microscope:

- It answers only the driver: each start writes a fresh key that the driver reads, and anything else
  that knocks (a web page, say) is cut off.
- It refuses a stage move beyond NIS's own travel, whatever the driver asked.
- It only takes a picture into a **new** NIS document, saves that one, and closes that one. Your own
  images in NIS are never saved over or closed. If NIS captures into an image you have open (its
  "capture creates a new document" option is off), nothing is saved and nothing is closed.
- When the macro is stopped with NIS's Stop button, the driver says so instead of waiting.

## What runs where

On a microscope PC where NIS-Elements brings the only Python, three parts run in NIS's own Python and
need nothing installed:

- **the bridge**, started inside NIS by `start_bridge.mac`;
- **writing that macro**, with NIS's own `python.exe`, from the repository's root folder:
  `"C:\Program Files\NIS-Elements\Python\python.exe" -m zmart_drivers.nikon.nis_elements_ar7.driver_setup.nis_bridge`;
- **the hardware check**, started inside NIS by `testing/hardware/check_hardware.mac`.

The driver itself (`connect()` and the ZMART Controller plugin) also needs numpy, tifffile and the OME-Zarr
packages, to save each picture as OME-Zarr. They are installed into the Python that runs the driver: NIS's
own, when that is allowed (`pip install -e ".[nikon]"` with NIS's `python.exe`), or another one on the same
computer.

## How to call the driver

The driver runs on the computer that runs NIS-Elements. Install it with Python 3.11 or newer, from the
repository's root folder:

```bash
pip install -e ".[nikon]"
```

Once per computer, and again after every update of the driver, write the macro that starts the bridge:

```bash
python -m zmart_drivers.nikon.nis_elements_ar7.driver_setup.nis_bridge
```

It writes `start_bridge.mac` into `C:\ProgramData\zmart-microscopy\nikon\bridge\` and prints the path.
In NIS-Elements, choose **Macro > Run Macro From File** and pick it. Leave it running while you use the
driver: its loop is what carries out the driver's requests. The driver refuses a bridge of another
version, and says to write the macro again.

Set the microscope up once before the first experiment (see [Setting up the driver](#setting-up-the-driver)
below). Then every session starts with `connect()`, which reaches the bridge, loads this microscope's
settings and hands back a `Microscope`. Each call in the table below is a method of that `Microscope`:

```python
import zmart_drivers.nikon.nis_elements_ar7 as nis_elements_ar7

scope = nis_elements_ar7.connect(microscope="nikon-ti2-zmb")

scope.move_to(100.0, 250.0, 0.0)  # micrometres from the saved origin
scope.select_optical_configuration("GFP")  # read back
scope.change_objective(2)  # nosepiece position 2, read back
scope.acquire("A1")  # one picture, saved as OME-Zarr

scope.close()
```

What every call does for you:

- **The limits are asked first.** A move or a setting outside this microscope's limits is refused
  before anything is sent. A move whose focus is out of bounds does not move the stage either.
- **Every change is read back.** A change that was sent but never seen is recorded, and `state()` lists
  it; the work goes on.
- **A mistake is said in words.** A request that is wrong raises `ValueError`; a change that NIS
  refused, or that failed, raises `RuntimeError` with the reason.

`connect()` takes a few keys, all optional: `microscope` (this instrument's name; its settings are kept
under it), `config`, `output_root` (where pictures go when `acquire` is not given a folder), `port`,
`timeout_s`, `load_limits` and `load_origin` (both `True`). The full list is `DEFAULT_CONNECTION` in
`methods.py`.

**Through the ZMART Controller.** Register the driver's folder once on the NIS computer, then connect
by name in every session. The controller calls the same `Microscope`: `get_xyz` is `position()`,
`set_xyz` is `move_to()`, `set_state` is `set_state()` (the objective position and the optical
configuration), and `acquire` is `acquire()`.

```python
from zmart_controller import mic

mic.register_driver(r"C:\ZMART-drivers\zmart_drivers\nikon\nis_elements_ar7")  # once
mic.connect("nikon-nis-elements-ar7")  # every session
```

## The calls

| Call | What it does |
|---|---|
| `position()` | Where the stage is: `x`, `y` and `z` in micrometres from the origin, each with NIS's own reading and the range a picture can show. `z` is the focus drive. |
| `move_to(x, y, z)` | Move the stage, then the focus, and answer as `position()`, read back after the move. |
| `motors()` | The motors of each axis (one each), and the one used to move it. |
| `set_origin()` | Make the current position the origin, and save it, with the objective in place, for every later connection. |
| `objectives()` | The objectives by nosepiece position, and the position in place. |
| `change_objective(position)` | Turn the nosepiece to `position` (counted from 1) and read it back. |
| `optical_configurations()` | The optical configurations NIS has, and the selected one. |
| `select_optical_configuration(name)` | Select an optical configuration and read it back. It may bring its own objective. |
| `set_state(changeable)` | Several at once, as `state()` names them: `{"objective_position": 2, "optical_configuration": "GFP", "shutter/EPI": 1}`. The objective first, then the configuration, then the hardware settings, the light last. |
| `acquisition_settings()` | How a capture is triggered: `snap`, one picture with the settings as they are. |
| `acquire(position_label=None, acquisition_type="acquisition", output_folder=None)` | Take one picture and save it as one OME-Zarr store, with its log. |
| `save_staged(output_folder=None)` | Save the pictures whose saving failed earlier (they wait in `.staging`). |
| `devices()` | Every hardware setting the driver knows, which of them this microscope has, and their values now. |
| `device(name)` | One hardware setting's value now: `device("shutter/EPI")`, `device("light_path")`. |
| `set_device(name, value)` | Set one hardware setting and read it back: `set_device("filter/Emission Wheel", 3)`. |
| `state()` | The objective, the optical configuration and every hardware setting, and what can only be read: the objectives, Perfect Focus, the limits in force, the picture size, the changes that were never confirmed. |
| `stop_bridge()` | Ask the bridge's macro in NIS to end, and close this session. |
| `close()` | Close the session; every later call is refused. The bridge keeps running in NIS. |
| `client` | The bridge's client itself, for the setup notebooks. |

## The hardware settings

Besides the stage, the objective and the optical configuration, the driver can set each piece of hardware
NIS controls on its own. Every one is a row of one table, `primitives/nis_python/devices.py`, which names
the NIS function that reads it and the one that changes it, side by side. The driver, the bridge and the
hardware check all read that one table, so what the check confirms at the microscope is what the driver
sends.

| Group | Settings (their names in `state()` and `set_state`) |
|---|---|
| Light | `shutter/<name>` (1 open, 0 closed), `lamp/<name>` (1 on, 0 off), `lamp_voltage/<name>`, `lamp_iris/<name>`, `lamp_nd_filter/<name>`, `nd_filter` |
| Filters | `filter/<name>`: a filter wheel or turret's position, from 1 |
| Optics | `light_path`, `condenser`, `aperture`, `analyzer`, `zoom`, `relay_lens`, `correction_collar` |
| Focus | `pfs` (1 holds the focus), `pfs_offset` |
| Spinning disk | `disk_shutter`, `disk_speed` |
| TIRF *(guarded)* | `tirf_position` |
| Lasers *(guarded)* | `laser_line/<laser>.<line>` (1 used), `laser_power/<laser>.<line>` (percent) |
| Environment *(guarded)* | `stage_temperature/<name>`, `incubator_gas`, `incubator_humidity` |

`<name>` is the device's name as NIS lists it, such as `shutter/EPI`. When connected, the driver asks NIS
which of these devices are there, and offers only those; `devices()` lists them, with what each takes.

**Guarded settings** are refused until this microscope's limits allow them, because a wrong value could
harm the sample, the microscope or a person. Allow one by adding it under `devices` in this microscope's
`limits.json`, with the values or the range it may take. An entry for one device wins over the setting's:

```json
"devices": {
    "laser_power": {"range": [0, 30]},
    "incubator_gas": {"range": [4.5, 5.5]},
    "shutter/DIA": {"allowed": [0, 1]}
}
```

A setting that is not guarded needs no entry, but may have one to narrow it. `set_state` sends nothing for
a setting that already holds the asked value, so `set_state(state()["changeable"])` is always safe.

## What a capture leaves on disk

`acquire` takes the picture into a new NIS document, has NIS save it as a TIFF in the output folder's
`.staging` folder, and closes that document. The driver then converts the TIFF into one OME-Zarr store
that follows the ZMART metadata convention, and deletes the TIFF only once the store is written:

```
<output folder>/
    data/<type>_<label>.ome.zarr     the picture, with where it was taken and how
    log/<type>_<label>.log.json      what the driver was asked, and what it did
```

What the store says about the picture comes in four layers, as in every ZMART driver (the ZMART
metadata convention, `zmart_drivers/metadata_convention`):

1. **OME-Zarr itself** (`zarr.json`): the axes, the pyramid, the pixel size as scale, and where the
   picture lies on the stage as translation.
2. **OME-XML** (`OME/METADATA.ome.xml`, the OME schema OME-TIFF uses): the image, its channels, each
   plane's stage position, the objective and the date.
3. **The driver's state at the capture**: everything `state()` said (the objective, the optical
   configuration, every hardware setting), the capture record, and each motor's own reading.
4. **NIS's own words**: NIS's whole description of the picture (camera, exposure, binning, channels...),
   which the snap has NIS write with `SaveImageFullDescription` before it closes the document, and what
   NIS answered to the capture.

The pixel size comes from NIS's own calibration of the objective. A picture taken with an objective
that is not calibrated in NIS cannot be placed on the sample, so it is not saved: it stays in
`.staging`, and the error says to calibrate the objective in NIS and then run `save_staged()` (or the
controller's `save_data` procedure). From then on, the driver refuses to capture with that objective
before taking a picture.

## Setting up the driver

A few things are set up once per microscope. The easiest way is the [setup wizard](driver_setup/wizard/README.md),
which walks through them in a window of its own, as the Leica driver's does:

```bash
python -m zmart_drivers.nikon.nis_elements_ar7.driver_setup.wizard
```

Each item also has a notebook in `driver_setup/`. What is set up is
saved under `C:\ProgramData\zmart-microscopy\nikon\<microscope>\nis_elements_ar7\` and loaded every
time the driver connects.

| Item | Notebook | Until it is done |
|---|---|---|
| The limits | `driver_setup/limits/notebooks/set_limits.ipynb` | x and y stay within NIS's own travel, and z within 100 µm of where it stood at connect. |
| The orientation | the wizard (measured from three pictures), or `driver_setup/orientation/notebooks/set_orientation.ipynb` | Pictures are saved as the camera gives them. |
| The data location | `driver_setup/data_location/notebooks/set_data_location.ipynb` | `acquire` needs an `output_folder`. |
| The origin | `scope.set_origin()` | Positions are NIS's own stage coordinates. |
| The pixel calibration | in NIS-Elements itself (Calibration) | Pictures with that objective cannot be saved. |

## Testing

The driver carries its own offline test suite. It runs the real bridge over a pretend NIS-Elements
(`testing/helpers/mock_nis.py`), so it needs neither a microscope nor NIS:

```bash
pip install -e ".[nikon,test]"                # the driver on its own
pip install -e ".[nikon,controller,test]"     # also with the ZMART Controller
python zmart_drivers/nikon/nis_elements_ar7/testing/run_ci.py
```

**The hardware check, at the microscope.** It runs inside NIS-Elements, in NIS's own Python, and needs
nothing installed. The same checks run with the microscope switched on or off: with it off, NIS answers
"not available" where the hardware is missing, which is the baseline of how it reacts; with it on, each
change really happens and is seen.

1. Unzip the ZMART-drivers folder onto the Desktop, into Documents or Downloads, or at `C:\ZMART-drivers`.
   The macro finds it there, also with the extra folder Windows' "Extract all" adds.
2. In NIS-Elements: **Macro > Run Macro From File**, and pick `testing\hardware\check_hardware.mac`.
3. When NIS says it has finished, send the `results` folder beside `check_hardware_nis.py`.

What it does, one line each in `check_summary.txt`:

1. **What NIS has**: NIS's version; whether NIS offers each of the driver's functions; what NIS's macro
   reference on the PC says about them (`reference.txt`, and the pages in `help_pages`); every hardware
   setting, which devices are there, and their values.
2. **Every reading**, called once with the driver's own arguments. NIS answers "not available" (−4) to right
   arguments with the hardware off, and "Bad number of parameters" to wrong ones.
3. **Every change, the smallest that can be seen, each put back**: a switch turned over and back, a position
   one step and back, a number nudged by about three times its read-back tolerance and back, the stage 5 µm
   and back, the focus 1 µm and back. Each is read after the change and again after putting it back. Set
   only to the value they already have: Perfect Focus on or off, the nosepiece, the disk's speed, lasers,
   TIRF, heating and gas. Where a value cannot be read (the hardware off), the change is sent once with a
   neutral value, and "not available" is the expected answer.
4. **One picture**, into a new document of its own, saved as a TIFF, and only that document closed.
5. **The driver itself**: the check starts the driver's own bridge on NIS's thread and runs the real driver
   against it, as in use: `connect()`, `state()`, `position()`, `move_to()` 5 µm and back, `devices()`,
   `set_device()` for each device with the smallest change and back, `objectives()`,
   `optical_configurations()`, `set_state()` with what `state()` answered, and `acquire()`, saved as
   OME-Zarr (reported as skipped when NIS's Python lacks tifffile or zarr). The driver keeps its settings in a
   scratch folder in the results, so the microscope's real setup is never touched.

At the top of `check_hardware_nis.py`: `CHANGES = False` makes a run that only reads; `TURN_NOSEPIECE` and
`SWITCH_PFS` also turn the nosepiece and switch Perfect Focus; `SNAP` and `DRIVER` leave out the picture or
the driver part.

## More about the driver

- [The plan](../../../docs/nikon/PLAN_NIS_ELEMENTS_AR7.md): what is known about NIS-Elements 7 and from
  where, how the driver is built, what the reviews of the plan changed, and what only the microscope
  can settle.
- [The anatomy of a ZMART driver](../../../docs/driver-anatomy.md), which this driver follows.
- Credits: the bridge and its macro grew out of steps 0 and 1 of the NIS 7 study in
  thomdehoog/nis-useq-aiagent; the layout, the dispatcher and much of the saving follow the Leica driver.
