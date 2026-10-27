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
  does, run `testing/hardware/check_hardware.py` at the microscope: it asks NIS the questions only a
  switched-on microscope can answer (listed in [the plan](../../../docs/nikon/PLAN_NIS_ELEMENTS_AR7.md),
  section 11), such as whether a stage move waits until the stage has arrived, and whether the TIFF that
  NIS saves holds the camera's own values.
- **Known limits of this alpha:**
  - There is no objective calibration. Changing the objective does not move the stage to keep the same
    spot in view, so positions shift by the difference between the two objectives.
  - The focus is not moved while Perfect Focus holds it: switch Perfect Focus off in NIS first.
  - One picture at a time (`snap`): no z-stacks, time series or ND acquisitions yet.
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

scope.move_to(100.0, 250.0, 0.0)                # micrometres from the saved origin
scope.select_optical_configuration("GFP")       # read back
scope.change_objective(2)                        # nosepiece position 2, read back
scope.acquire("A1")                              # one picture, saved as OME-Zarr

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
mic.connect("nikon-nis-elements-ar7")                                          # every session
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
| `set_state(changeable)` | Both at once: `{"objective_position": 2, "optical_configuration": "GFP"}`, objective first. |
| `acquisition_settings()` | How a capture is triggered: `snap`, one picture with the settings as they are. |
| `acquire(position_label=None, acquisition_type="acquisition", output_folder=None)` | Take one picture and save it as one OME-Zarr store, with its log. |
| `save_staged(output_folder=None)` | Save the pictures whose saving failed earlier (they wait in `.staging`). |
| `state()` | The objective and optical configuration, and what can only be read: the objectives, Perfect Focus, the limits in force, the picture size, the changes that were never confirmed. |
| `stop_bridge()` | Ask the bridge's macro in NIS to end, and close this session. |
| `close()` | Close the session; every later call is refused. The bridge keeps running in NIS. |
| `client` | The bridge's client itself, for the setup notebooks. |

## What a capture leaves on disk

`acquire` takes the picture into a new NIS document, has NIS save it as a TIFF in the output folder's
`.staging` folder, and closes that document. The driver then converts the TIFF into one OME-Zarr store
that follows the ZMART metadata convention, and deletes the TIFF only once the store is written:

```
<output folder>/
    data/<type>_<label>.ome.zarr     the picture, with where it was taken and how
    log/<type>_<label>.log.json      what the driver was asked, and what it did
```

The pixel size comes from NIS's own calibration of the objective. A picture taken with an objective
that is not calibrated in NIS cannot be placed on the sample, so it is not saved: it stays in
`.staging`, and the error says to calibrate the objective in NIS and then run `save_staged()` (or the
controller's `save_data` procedure). From then on, the driver refuses to capture with that objective
before taking a picture.

## Setting up the driver

A few things are set up once per microscope, each with a notebook in `driver_setup/`. What is set up is
saved under `C:\ProgramData\zmart-microscopy\nikon\<microscope>\nis_elements_ar7\` and loaded every
time the driver connects.

| Item | Notebook | Until it is done |
|---|---|---|
| The limits | `driver_setup/limits/notebooks/set_limits.ipynb` | x and y stay within NIS's own travel, and z within 100 µm of where it stood at connect. |
| The orientation | `driver_setup/orientation/notebooks/set_orientation.ipynb` | Pictures are saved as the camera gives them. |
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

At the microscope, with NIS-Elements running and `start_bridge.mac` started in it:

```bash
python zmart_drivers/nikon/nis_elements_ar7/testing/hardware/check_hardware.py           # reads only
python zmart_drivers/nikon/nis_elements_ar7/testing/hardware/check_hardware.py --move    # also small moves
python zmart_drivers/nikon/nis_elements_ar7/testing/hardware/check_hardware.py --snap D:\check
```

## More about the driver

- [The plan](../../../docs/nikon/PLAN_NIS_ELEMENTS_AR7.md): what is known about NIS-Elements 7 and from
  where, how the driver is built, what the reviews of the plan changed, and what only the microscope
  can settle.
- [The anatomy of a ZMART driver](../../../docs/driver-anatomy.md), which this driver follows.
- Credits: the bridge and its macro grew out of steps 0 and 1 of the NIS 7 study in
  thomdehoog/nis-useq-aiagent; the layout, the dispatcher and much of the saving follow the Leica driver.
