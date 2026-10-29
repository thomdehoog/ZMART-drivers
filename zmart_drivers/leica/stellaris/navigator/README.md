# navigator — Leica LAS X (STELLARIS) through the plain Navigator

`navigator` drives a **Leica STELLARIS** confocal from Python through LAS X's own **Navigator**
window, the one every LAS X installation has. It is the companion of
[`navigator_expert`](../navigator_expert/README.md), which needs the Navigator Expert add-in and its
CAM API; this driver needs neither. It talks to LAS X's hardware server only, the same connection the
Navigator window itself uses, so the plain window and the driver always see the same values.

It works on its own, from a notebook or a script, and it plugs into the ZMART Controller. Every change
is checked against this microscope's limits before it is sent, and read back afterwards, so you know
what the microscope really did.

- **Author:** Thom de Hoog (ZMB, University of Zurich) · thom.dehoog@zmb.uzh.ch · thomdehoog@gmail.com
- **License:** see the repository root [`LICENSE`](../../../../LICENSE).
- **Status:** **Beta (`0.1.0b1`).** The route this driver is built on (the settings, the stage, both z
  drives, a capture and a tile scan through the hardware server) passed its 50 checks on the STELLARIS
  on 15 October 2026. This driver puts that route into the anatomy every ZMART driver shares; it is
  tested offline against a stand-in for LAS X and not yet on the STELLARIS itself. The check at the
  microscope is `testing/run_ci.py --hardware` (see [`testing/hardware/README.md`](testing/hardware/README.md)).
- **Known limits of this beta:**
  - LAS X's plain Navigator has no jobs. The driver changes five settings of the current scan (the
    table below); everything else, such as the lasers, the detectors and a z-stack, is set in LAS X's
    window and only read back.
  - A z move is sent as a step from where the drive stands, because the plain Navigator only takes
    relative z moves. The driver reads the drive before every send, so a step is never added twice.
  - The setup wizard and the orientation and objective-calibration measurements are the Navigator
    Expert driver's, with their pictures taken by the Navigator window's Capture Image. They are tested
    offline against stand-ins and not yet on the STELLARIS: whether Capture Image takes the whole
    z-stack the objective step needs is still to be confirmed at the microscope.
  - There is no canvas to clear. `clear_cache()` lets go of a saved picture in LAS X's Data Container
    only, which is all the plain Navigator keeps.
  - Windows must allow long paths, or captures must be saved to a short folder: a path of more than
    259 characters stops a save, and the driver says so.

## How to call the driver

The driver runs on the computer that runs LAS X, because it reaches LAS X's hardware server through
Leica's own .NET interface from the LAS X installation. Install it with Python 3.11 or newer, from the
repository's root folder:

```bash
pip install -e ".[leica]"
```

Open LAS X and show its Navigator (not Navigator Expert; the driver refuses to connect when Navigator
Expert's jobs are shown, because that window's canvas and jobs belong to the other driver). Set the
microscope up once before the first experiment (see [Setting up the driver](#setting-up-the-driver)
below). Then every session starts with `connect()`, which reaches LAS X, loads this microscope's
settings and hands back a `Microscope`. Each call in the table below is a method of that `Microscope`:

```python
import zmart_drivers.leica.stellaris.navigator as navigator

scope = navigator.connect()

scope.move_to(100.0, 250.0, 0.0)              # micrometres from the saved origin
scope.change_setting("zoom", 2.0)             # read back from LAS X
scope.acquire("A1")                           # the window's Capture Image; stays in LAS X's cache
scope.tile_scan(scope.row_of_fields(3), "row")  # the window's Preview over three fields
scope.save()                                  # written as OME-Zarr, one store per field
scope.clear_cache()                           # LAS X lets go of the saved pictures

scope.close()
```

What every call does for you:

- **The limits are asked first.** A move, a setting or a tile scan position outside this microscope's
  limits is refused before anything is sent.
- **Every change is read back.** A change that was sent but never seen is recorded, and `state()` lists
  it; the work goes on.
- **A mistake is said in words.** A request that is wrong raises `ValueError`; a change that LAS X
  refused, or that failed, raises `RuntimeError` with the reason.
- **LAS X's own message boxes are answered.** An Information or Warning box with only an OK button,
  the kind LAS X shows when a scan is stopped or a setting is adjusted, is pressed from here, and the
  running log says so. A box with a choice is left for you.

`connect()` takes a few keys, all optional: `output_root` (where captures go when no folder is given),
`config` (the name of a saved configuration), `client` (the name LAS X sees, `ZMART`), and
`load_limits`, `load_calibration` and `load_origin` (all `True`). The host is always this computer.

**Through the ZMART Controller.** Register the driver's folder once on the LAS X computer, then connect
by name in every session. The controller calls the same `Microscope`: `get_xyz` is `position()`,
`set_xyz` is `move_to()`, `set_state` is `change_setting()` and `change_objective()`, and `acquire` is
`acquire()` then `save()`. The procedures it offers are `zero_z_galvo`, `tile_scan`, `save_data` and
`clear_cache`.

```python
from zmart_controller import mic

mic.register_driver(r"C:\ZMART-drivers\zmart_drivers\leica\stellaris\navigator")  # once
mic.connect("stellaris-navigator")                                                 # every session
```

## The calls

| Call | What it does |
|---|---|
| `position()` | Where the stage is: `x`, `y` and `z` in micrometres from the origin, each with its motors' own readings and the range a picture can show. `z` is the focus, the two z drives added together. |
| `move_to(x, y, z)` | Move there, and answer as `position()`, read back once the stage has arrived. The focus moves with z-wide. |
| `motors()` | The motors of each axis, and the one used to move it. |
| `set_origin()` | Make the current position the origin, and save it for every later connection. |
| `zero_z_galvo()` | Park the z-galvo at 0 without changing the focus: its offset moves onto z-wide. |
| `settings()` | What LAS X is set to now: the zoom, the scan speed, the rotation, the averaging, the pinhole, the format, the scan mode, the objective and the z-stack, as LAS X reports them. |
| `change_setting(name, value)` | Change one of the five settings below and read it back. |
| `change_objective(slot=None, name=None, magnification=None)` | Turn to another objective, and move the stage by the measured offset so the same spot stays in view. |
| `acquisition_settings()` | How a capture is triggered: `full`, one picture with the current settings. |
| `acquire(position_label=None, acquisition_type="acquisition", output_folder=None)` | Take one picture, as the window's Capture Image does. It stays in LAS X's cache, with a note of how it was taken. Nothing is written yet. |
| `tile_scan(positions, position_label=None, acquisition_type="tile_scan", output_folder=None)` | Take one picture of several fields, as the window's Preview does, at `(x, y)` positions in micrometres from the origin, at the current focus. Afterwards the stage is back at the first field. |
| `row_of_fields(count)` | `count` positions in a row from where the stage stands, a field and a tenth apart, for `tile_scan`. |
| `save(label=None)` | Write the pictures that are waiting as OME-Zarr, one store per field, with their projection and log. |
| `clear_cache()` | Have LAS X let go of the pictures that were saved. Acquiring and saving never do this. |
| `state()` | The five settings and the objective, and what can only be read: the instrument, the objectives, the limits in force, the changes that were never confirmed. |
| `close()` | Close the session. Every later call is refused. |
| `client` | The connection to LAS X's hardware server itself, for the setup notebooks. |

### The settings `change_setting` can change

These are the settings LAS X's Navigator window takes from outside; each is sent to the current scan
and read back from LAS X's state tree. A value outside the range in this microscope's limits file is
refused before it is sent; a value LAS X adjusts (a pinhole above its maximum, say) is reported as not
confirmed, with what LAS X set instead.

| Name | Value | Read back within |
|---|---|---|
| `zoom` | the zoom factor, within the zoom range `settings()` reports | 0.1 |
| `scan_speed` | lines per second, one of the speeds LAS X offers | exact |
| `scan_field_rotation` | degrees | 0.5° |
| `line_average` | 1, 2, 4, ... | exact |
| `pinhole_airy` | Airy units | 0.05 AU |

## Setting up the driver

Once per microscope, before the first experiment, the driver needs five things about this microscope:
where its pictures go, how far the stage may travel, the point that counts as (0, 0, 0), which way the
picture is turned relative to the stage, and how far the sample shifts between objectives.

**With the setup wizard (recommended).** One window walks you through all of them, step by step, and
saves each where the driver loads it:

```bash
python -m zmart_drivers.leica.stellaris.navigator.driver_setup.wizard
```

Its steps are Connect (with the data folder), Define limits, Define coordinate system origin,
Image-to-stage calibration and Objective calibration. Its [README](driver_setup/wizard/README.md) says
more about each. The two measuring steps take their pictures with the Navigator window's Capture
Image, as LAS X is set up at the time: drive to a field with clear structure for the orientation, and
set a z-stack up on z-wide in LAS X for the objective step's focus measurements.

**Or with the notebooks**, one per item, in its own folder:

| What | Notebook |
|---|---|
| Data location | `driver_setup/data_location/notebooks/set_data_location.ipynb` |
| Limits | `driver_setup/limits/notebooks/set_limits.ipynb` |
| Origin | `scope.set_origin()` from Python, with the stage on the spot that should be (0, 0, 0) |
| Orientation | `driver_setup/orientation/notebooks/set_orientation.ipynb` |
| Objective calibration | `driver_setup/optical_calibration/notebooks/calibrate_objective_pair.ipynb` |

Do the limits first: the orientation is measured within them, and the objective calibration needs both.

**Where the settings are kept.** On this computer, in
`C:\ProgramData\zmart-microscopy\leica\stellaris_y42h93\navigator\`, never in the repository. Each
item has its own folder there, with one dated folder per time it was saved; the newest is the one
`connect()` loads. Nothing is overwritten, so you can always see what the microscope was told and when.
The `navigator_expert` driver keeps its own folder beside it: the two drivers are set up separately,
each with its own wizard.

**What happens when something is missing.**

- **No limits saved yet, or a damaged limits file:** the session runs within the driver's cautious
  default limits, and says so loudly. The stage's physical backstop always holds.
- **No origin saved yet:** positions are plain stage coordinates, and `connect()` warns. A damaged origin
  file stops the connection with how to fix it, because a guessed origin would shift every position.
- **No data location saved yet:** nothing is saved until one is set. A capture may still name its own
  folder.
- **No orientation or calibration measured yet:** the driver fills in neutral defaults (no turn, no
  offsets) the first time it connects. A move to another objective is refused until the calibration
  covers it.

### Where the driver expects LAS X

| Purpose | Path (default) |
|---|---|
| LAS X's installation, with its hardware server client | `C:\Program Files\Leica Microsystems CMS GmbH\LAS X` (or `ZMART_LASX_FOLDER`) |
| LAS X's own log, read for its message boxes | `C:\ProgramData\Leica Microsystems\LAS X\LAS X.log` |
| LAS X's Data Container cache, where its pictures are | the folder the registry names (`D:\Temp2` on the STELLARIS) |

Defaults live beside the code that uses them, in `primitives/direct/client.py`. Override them there,
not at call sites.

## More about the driver

- How every ZMART driver is laid out: [`docs/driver-anatomy.md`](../../../../docs/driver-anatomy.md);
  what another microscope must bring: [`docs/vendor-contract.md`](../../../../docs/vendor-contract.md).
  This driver has one primitive class, `primitives/direct/`, LAS X's hardware server: it reads the state
  tree and sends the Navigator window's own commands.
- The tests: `python zmart_drivers/leica/stellaris/navigator/testing/run_ci.py` runs the offline suite
  from the repository root, in two layers: the driver on its own, and the driver inside the ZMART
  Controller when that is installed. The check at the microscope is in
  [`testing/hardware/README.md`](testing/hardware/README.md).
