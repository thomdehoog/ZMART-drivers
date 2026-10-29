# zen_api_grpc — ZEISS ZEN driver

`zen_api_grpc` drives a **ZEISS light microscope** from Python, through **ZEN 3.13** and its
**ZEN API**. It works on its own, from a notebook or a script, and it is the ZEISS driver behind
the ZMART Controller. Every change is checked against this microscope's limits before it is sent,
and read back afterwards, so you know what the microscope really did.

- **Author:** Thom de Hoog (ZMB, University of Zurich) · thom.dehoog@zmb.uzh.ch · thomdehoog@gmail.com
- **License:** see the repository root [`LICENSE`](../../../../LICENSE).
- **Status:** **Beta (`0.1.0b1`), not yet run against ZEN.** It speaks ZEISS's published `zen_api`
  2025.10.1 package, the one made for ZEN 3.13, and is tested offline in two ways: against a
  stand-in for ZEN, and over the real wire (ZEISS's own classes, gRPC and TLS) against a small
  gateway inside the tests. The first run on ZEN, or on ZEISS's simulator, is what
  [the check at the microscope](#checking-the-driver-at-the-microscope) is for.
- **Laid out as** [the anatomy of a ZMART driver](../../../../docs/driver-anatomy.md), with the
  Leica driver as the example. Its dispatcher is the Leica driver's, copied byte for byte.

## How the driver reaches ZEN

```
 your Python (this driver)                       the ZEN computer
 ┌──────────────────────────┐  gRPC over TLS   ┌───────────────────────────────┐
 │ procedures, dispatcher   │  + control token │ ZEN API Gateway               │
 │ primitives/gateway/      │ ───────────────▶ │  checks the token, forwards   │
 │   ZEISS's zen_api classes│ ◀─────────────── │          │                    │
 └──────────────────────────┘                  │          ▼                    │
                                               │ ZEN 3.13: stage, focus,       │
                                               │ objectives, experiments → CZI │
                                               └───────────────────────────────┘
```

ZEN offers its stage, focus drive, objective changer and experiments as services over the
network, using gRPC (a way of calling functions on another program). A small separate program
from ZEISS, the **ZEN API Gateway**, stands in front of ZEN: it encrypts the connection and lets a
call through only when it carries the gateway's **control token**, a password the gateway makes.

Three things are good to know before you start:

- **ZEN keeps the imaging settings.** What to image (channels, exposure, light sources, z-stack,
  time series, tiles) is a ZEN *experiment*, a `.czexp` you set up and save in ZEN as usual. The
  driver loads an experiment by name and runs it. It can change the experiment's z-stack, but it
  does not build experiments.
- **ZEN writes the images, the driver saves them as OME-Zarr.** Every capture becomes one CZI file
  in ZEN's image folder. When it is saved, the driver turns it into one OME-Zarr store in the run's
  `data` folder, the format every ZMART driver saves in, so the ZMART Viewer and ZMART Analysis
  open a ZEISS capture as they open any other. ZEN's own CZI is kept beside it.
- **ZEN speaks metres and waits until done.** ZEN's moves answer when the stage has arrived, and
  its acquisitions when they are finished. The driver converts to micrometres in one place, and
  its read-backs are a second opinion, not the signal that a move is over.

## Setting it up on the microscope PC

1. **Install the ZEN API Gateway** with the ZEISS Microscopy Installer, and activate the *ZEN API*
   toolkit in ZEN. The gateway then starts with ZEN and sits in the system tray.
2. **Allow control from outside.** In ZEN, open *Tools ▸ Options ▸ ZEN API* (ZEN core:
   *Maintenance ▸ General Options ▸ ZEN API*) and tick *Enable Unsupervised API Mode*. Without it
   ZEN answers only questions and refuses every move and every capture; the driver then says so,
   with this instruction. In this mode you and the driver can both use the microscope, so do not
   move the stage in ZEN while a workflow runs.
3. **Install the driver** with Python 3.11 or newer, from the repository's root folder. The
   `zeiss` extra brings ZEISS's `zen_api` 2025.10.1 package straight from ZEISS's OAD repository
   (it is not on PyPI), with `grpclib` and ZEISS's CZI reader `pylibCZIrw`:

   ```bash
   pip install -e ".[zeiss]"                # the driver on its own
   pip install -e ".[zeiss,controller]"     # with the ZMART Controller
   ```

4. **Save the experiments** you want to capture with in ZEN, for example `ZMART_Snap` with your
   channels and `ZMART_ZStack` with a z-stack.
5. **Set the microscope up once** (see [below](#setting-up-the-driver)).

## How to call the driver

```python
import zmart_drivers.zeiss.light_microscope.zen_api_grpc as zen

scope = zen.connect(experiment="ZMART_Snap")   # on the ZEN computer the token is read for you

scope.move_to(100.0, 250.0, 0.0)               # micrometres from the saved origin
scope.change_objective(position=2)             # the sample stays in view
scope.acquire("A1")                            # ZEN writes the CZI
scope.save()                                   # moved into the run's folder, with its record

scope.close()
```

What every call does for you:

- **The limits are asked first.** A move outside this microscope's limits is refused before
  anything is sent.
- **Every change is read back.** A change that was sent but never seen is recorded, and `state()`
  lists it; the work goes on.
- **A mistake is said in words.** A request that is wrong raises `ValueError`; a change ZEN refused
  or that failed raises `RuntimeError` with the reason and, where there is one, what to do.

### The connection

Every key is optional.

| Key | What it is |
|---|---|
| `host`, `port` | Where the ZEN API Gateway listens: `127.0.0.1` and `5002` unless its `appsettings.json` says otherwise. |
| `password` | The gateway's control token. Left empty on the ZEN computer, it is read from `C:\ProgramData\Carl Zeiss\ZEN APIGateway\GlobalControlToken.txt`. Copy it from the gateway's tray icon otherwise. |
| `cert_file` | The gateway's root certificate, so the driver checks it talks to the right gateway. Left empty, the gateway's own `Certificates` folder is looked in; without one the connection is encrypted but not checked, and `get_info` says so. |
| `zen_config_ini` | A ZEISS-style `config.ini` (`[api]`: host, port, cert_file, control-token), as ZEISS's own examples use. Its values fill what is left empty. |
| `zen_images` | Where this computer sees ZEN's image folder, when the driver runs on another computer than ZEN (a mapped share). |
| `experiment` | The ZEN experiment loaded at connect. |
| `output_root` | Where captures go when no folder is given; else the data location set up once. |
| `config` | Which saved configuration to use (`config_YYYYMMDD_HHMM`); empty, the most recent. |
| `load_limits`, `load_calibration`, `load_origin` | Load what was set up; leave them on for normal work. |
| `calibration_name` | A named objective calibration set. |

**Through the ZMART Controller.** Register the driver's folder once, then connect by name in every
session. The controller calls the same `Microscope`: `get_xyz` is `position()`, `set_xyz` is
`move_to()`, `set_state` loads an experiment or changes the objective, and `acquire` is `acquire()`
then `save()`.

```python
from zmart_controller import mic

mic.register_driver(r"C:\ZMART-drivers\zmart_drivers\zeiss\light_microscope\zen_api_grpc")  # once
mic.connect("zeiss-zen", {"experiment": "ZMART_Snap"})                                     # every session
```

## The calls

| Call | What it does |
|---|---|
| `position()` | Where the stage is: `x`, `y` and `z` in micrometres from the origin, each with ZEN's own reading and the range the stage may travel. `z` is the focus drive. |
| `move_to(x, y, z)` | Move there, XY first, then the focus, and answer as `position()`, read back. |
| `motors()` | The motor of each axis: `motoric` for x and y, `focus` for z. |
| `set_origin()` | Make the current position the origin, and save it for every later connection. |
| `objectives()` | The objectives on the changer: position (from 1), name, magnification, NA. |
| `change_objective(position=None, name=None, magnification=None)` | Change the objective and move the stage and focus by the measured offset, so the same spot stays in view. A pair not calibrated is refused. |
| `experiments()` | The experiments ZEN can load, and the one loaded. |
| `load_experiment(name)` | Load a ZEN experiment: the imaging settings every capture uses. |
| `z_stack()`, `set_z_stack(first_um, last_um, interval_um)` | Read or change the loaded experiment's z-stack; the ends are micrometres from the origin's focus, checked against the focus limits. |
| `acquisition_settings()` | How a capture is triggered: `full` (the whole experiment) or `snap` (one picture with its channels). |
| `acquire(position_label=None, acquisition_type="acquisition", output_folder=None, acquisition_settings=None)` | Run the loaded experiment once. ZEN writes the CZI; it waits in ZEN's folder until `save()`. A picture is sent once and never again. |
| `save(label=None)` | Save the waiting captures, each as one OME-Zarr store in its run's `data` folder, keep ZEN's CZI beside it, and describe their planes. |
| `autofocus(timeout_s=None)` | ZEN's software autofocus, with the focus search of the loaded experiment. |
| `find_surface()`, `store_focus()`, `recall_focus()` | The Definite Focus, on stands that have one. |
| `stop()` | Stop whatever ZEN is acquiring. |
| `state()` | The experiment and the objective, and what can only be read: the objectives, the experiments, the image and pixel size, the limits in force, what is set up, the changes never confirmed. |
| `close()` | Close the connection. Every later call is refused. |

### What a capture leaves on disk

```
<output folder>/
  data/
    overview_A1.ome.zarr              the capture: one OME-Zarr 0.5 store
    metadata/
      vendor/overview_A1.czi          ZEN's own file, exactly as ZEN wrote it
      ZMART_state/overview_A1.json    ZMART's account of the capture, readable as text
  projections/
    overview_A1.ome.zarr              for a z-stack: the brightest pixel over z
```

This is the layout the Leica driver writes, so a run's `data` folder opens in the ZMART Viewer as
one picture of all its positions. A name already used gets `_001`, `_002` and so on, so no capture
overwrites another.

**What the store holds.** It follows the
[ZMART metadata convention](../../../../zmart_drivers/metadata_convention/README.md), like every
ZMART driver's store:

- every plane of the capture, axes t, c, z, y, x, with the lowest slice first;
- the pixels turned onto the stage's axes, with the orientation the setup measured, so the
  picture's columns run along the stage's x and its rows along its y. The turn only rearranges
  pixels, so nothing is lost;
- the pixel size, and where on the sample the image lies, in micrometres from the origin;
- each channel's name and colour as ZEN described them, and coarser levels for zooming out;
- in `OME/METADATA.ome.xml`: the objective, where each plane was taken and what the drives read,
  the controller's state and the capture record, and ZEN's own description of the image, the
  CZI's metadata, byte for byte.

A colour camera's picture becomes three channels, red, green and blue. A tile scan is saved as
the stitched picture ZEN's reader gives, one store for the whole mosaic, placed around where the
stage stood when the capture began; for a tile region that is not centred on that point, the
placement is not yet right, which the first runs on ZEN will have to settle.

The planes the answer lists give each image's channel, depth and time point, and where it was
taken in the sample's frame; `z = 0` is the lowest slice. Each names the store as its `path`.

**ZEN's CZI.** The CZI is moved into `data/metadata/vendor/` only after the store is written and
its pixels read back, so a save that fails loses nothing: the file stays in ZEN's folder and the
capture waits for another try. To save disk space, set
`procedures.save_data.save.KEEP_CZI = False`; ZEN's file is then deleted once the store is
written. `PROJECTION = "none"` in the same file leaves the projections out.

## Setting up the driver

Once per microscope, before the first experiment, the driver needs five things: where its pictures
go, how far the stage may travel, the point that counts as (0, 0, 0), how the picture is turned
against the stage (with the pixel size), and how far the sample shifts between objectives.

The easiest way is the **setup wizard**, which walks through all five in a window:

```bash
python -m zmart_drivers.zeiss.light_microscope.zen_api_grpc.driver_setup.wizard
```

[Its README](driver_setup/wizard/README.md) describes each step. The notebooks below do the same
work with the same safety checks, one item at a time:

| What | How |
|---|---|
| Data location | `driver_setup/data_location/notebooks/set_data_location.ipynb` |
| Limits | `driver_setup/limits/notebooks/set_limits.ipynb`: drive the stage to four safe corners, and the focus to its two safe ends |
| Origin | `scope.set_origin()` from Python, with the stage on the spot that should be (0, 0, 0) |
| Orientation and pixel size | `driver_setup/orientation/notebooks/set_orientation.ipynb`: three snaps and two 15 µm moves |
| Objective calibration | `driver_setup/optical_calibration/notebooks/calibrate_objective_pair.ipynb`: centre a feature by eye under each objective |

Do the limits first: the orientation moves the stage within them.

**Where the settings are kept.** On this computer, in
`C:\ProgramData\zmart-microscopy\zeiss\light_microscope\zen_api_grpc\`, never in the repository.
Each item has its own folder there, with one dated folder per time it was saved; the newest is the
one `connect()` loads.

**What happens when something is missing.**

- **No limits saved yet, or a damaged limits file:** the session runs within the bundled
  placeholder limits, and says so loudly. They are not your stage's travel, so measure the real
  ones before the first experiment. A backstop of ±200 mm (±50 mm for the focus) always holds.
- **No origin saved yet:** positions are ZEN's own coordinates. A damaged origin file stops the
  connection with how to fix it.
- **No data location:** nothing is saved until one is set, unless a capture names its own folder.
- **No orientation measured yet:** the image size and pixel size are unknown until the first capture
  of the session, and `state()` reports them as missing. The controller's driver check needs them,
  so measure the orientation before using the controller.
- **No calibration:** an objective change across a pair that is not calibrated is refused.

## Checking the driver at the microscope

The check connects and reads by default; it changes nothing. Add `--allow-move` for three small
moves (50 µm in x and y, 5 µm of focus) that are read back and returned, and `--allow-acquire` for
one snap with the experiment you name. Each step says PASS or FAIL with what it saw.

```bash
python zmart_drivers/zeiss/light_microscope/zen_api_grpc/testing/run_ci.py --hardware
python zmart_drivers/zeiss/light_microscope/zen_api_grpc/testing/run_ci.py --hardware --allow-move --allow-acquire --experiment ZMART_Snap
```

What the first run on ZEN has to confirm, which the offline tests cannot:

1. That ZEN 3.13 offers the services the driver uses under the names of the 2025.10.1 package
   (`lm.hardware.v2` for the stage, the focus and the objective changer).
2. That ZEN's `MoveTo` answers only once the stage has arrived, as ZEISS documents.
3. Where ZEN writes its CZI files, and that this computer can see the folder.
4. The real travel of the stage, which goes into the limits.

## Testing

The driver carries its own offline suite, which needs neither a microscope nor ZEN. From the
repository's root folder:

```bash
pip install -e ".[zeiss,test]"              # the driver on its own
pip install -e ".[zeiss,controller,test]"   # also with the ZMART Controller
python zmart_drivers/zeiss/light_microscope/zen_api_grpc/testing/run_ci.py
```

It has two layers, as the Leica driver's: the driver by itself, then the driver inside the ZMART
Controller, which shows as `SKIP` when the controller is not installed (`--controller` makes that a
failure). The stand-in for ZEN, `testing/helpers/mock_zen_api.py`, pretends a stage, a focus, three
objectives and a few experiments, and writes real CZI files of a pretend sample seen through a
turnable camera, so even the orientation measurement runs offline. `testing/unit/test_real_wire.py`
runs the driver over ZEISS's own classes, gRPC and TLS.

## The parts

```
zen_api_grpc/
  __init__.py                connect() and Microscope
  methods.py                 connect(), the Microscope, its Session, the running log
  procedures/                changes.py, readings.py, absolute_coordinate_system.py,
                             keep_in_view.py, acquire.py, state.py, save_data/
  dispatcher/                the Leica driver's, unchanged
  primitives/gateway/        client.py (the connection and the loop thread), transfer.py,
                             reads.py, writes.py, profile.py
  driver_setup/              limits/, origin/, orientation/, optical_calibration/,
                             data_location/, store.py, and wizard/, the window that
                             walks through them
  zmart_controller_plugin/   the ZmartControllerPlugin class, info.py, procedure_menu.py
  testing/                   helpers/ (the stand-in), unit/, hardware/, run_ci.py
```

There is one class of primitive, `gateway`: ZEN is reached one way, so its profile is also the one
handed to the dispatcher. How it differs from the Leica driver:

- **No route chain, no dialogs, no log.** ZEN answers every call itself, so the transfer keeps the
  answer as the reply and the profile sorts ZEN's gRPC status: only `UNAVAILABLE` and its kin (busy,
  timed out) are sent again.
- **The objective calibration is done by eye.** The operator centres and focuses one feature under
  each objective, and the driver reads the positions; the Leica driver matches z-stacks and
  pictures instead. The setup wizard follows the same way.
- **The store is made from ZEN's CZI.** The Leica driver writes its store from LAS X's cache; this
  driver reads the CZI ZEN wrote, and keeps that file beside the store.

## References

- ZEISS OAD, ZEN API: <https://github.com/zeiss-microscopy/OAD/tree/v2025.10.1/ZEN-API>
- The `zen_api` 2025.10.1 documentation: <https://zeiss-microscopy.github.io/OAD/zenapi/2025.10.1/>
