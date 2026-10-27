# nis_elements_ar610 — Nikon NIS-Elements AR 6.10 microscope driver

`nis_elements_ar610` drives a **Nikon Ti2** widefield microscope from Python, through NIS-Elements AR 6.10
and the [nis-bridge](https://github.com/thomdehoog/nis-useq-aiagent/tree/bridge-hybrid-610/1_nis_bridge).
It works on its own, from a notebook or a script, and it is the Nikon driver behind the ZMART
Controller. Every change is checked against this microscope's limits before it is sent, and read back
afterwards wherever NIS-Elements can report it, so you know what the microscope really did.

- **Author:** Thom de Hoog (ZMB, University of Zurich) · thom.dehoog@zmb.uzh.ch · thomdehoog@gmail.com
- **License:** see the repository root [`LICENSE`](../../../../LICENSE).
- **Status:** **Beta, tested offline only.** The driver is laid out as
  [the anatomy of a ZMART driver](../../../../docs/driver-anatomy.md) describes and passes its offline
  suite against a mock NIS-Elements behind the real bridge server (27 October 2026). It has not yet been
  run at NIS-Elements itself. The bridge it builds on was validated on the Ti2 simulator of NIS-Elements
  AR 6.10.02; the driver's first run there is the next step
  ([`testing/hardware/README.md`](testing/hardware/README.md) says what to run).
- **Known limits of this beta:**
  - NIS-Elements cannot report which optical configuration is selected, so the driver reports the
    one it selected last in this session, and None before that.
  - A capture is one picture with the settings as they are (`snap`). A z-stack, a time series or a
    multi-channel capture is a series of moves, settings and snaps by the workflow.
  - The pixel size on the sample comes from NIS-Elements' calibration of the objective, read at each
    capture; before the first capture it is estimated from the camera pixel and the magnification in
    the objective's name. A store saved without a calibration says so and uses a scale of 1.

## How NIS-Elements is reached

NIS-Elements has a Python interpreter inside it, but no way for another program to call into it. The
nis-bridge is a small server that a NIS macro runs inside NIS-Elements; it takes requests over a socket
on the same computer and carries each one out on NIS's main thread. Inside NIS a request goes one of
two ways, and the driver keeps the two apart as its two classes of primitive:

| Class | How NIS answers inside | What it carries | Cost |
|---|---|---|---|
| `dll` | `g5_regprocs.dll`, the C library NIS ships, exports most of its macro functions | the stage, the limits, the nosepiece, the Perfect Focus System, the optical configurations, the focus sweep, capturing | well under a millisecond |
| `macro` | a few lines of NIS macro, run with `RunMacro`, because NIS 6.10 cannot hand values back from its Python safely | the camera report and the exposure | about 24 ms per function, a tenth of a second for the camera report |

Both travel over the same bridge connection. The `macro` class is asked again at a slower pace, so it
never keeps NIS's main thread busy.

## How to call the driver

The driver runs on the computer that runs NIS-Elements. Three things to install there, with Python
3.11 or newer:

1. **The bridge, inside NIS-Elements.** Clone
   [thomdehoog/nis-useq-aiagent](https://github.com/thomdehoog/nis-useq-aiagent), branch
   `bridge-hybrid-610`, and follow its part 1 README: `pip install -e "./1_nis_bridge"`, then
   `python -m nis_bridge.install_macros`, then start NIS-Elements and run `start_bridge.mac` in it
   (or `python -m nis_bridge.start`, which does both). NIS then shows "nis-bridge: running on port
   54470". The macro keeps running while the bridge is up; that is intended.
2. **This driver**, from the repository's root folder: `pip install -e ".[nikon-ar610]"`. The extra brings
   the bridge's client package from the same branch.
3. **The setup**, once per microscope (see below).

Then every session starts with `connect()`, which connects to the bridge, reads what NIS-Elements
allows, loads this microscope's settings and hands back a `Microscope`. Each call in the table below
is a method of that `Microscope`:

```python
import zmart_drivers.nikon.nis_elements_ar610 as nis_elements

scope = nis_elements.connect()

scope.move_to(100.0, 250.0, 0.0)  # micrometres from the saved origin
scope.select_channel("DAPI")  # a NIS-Elements optical configuration
scope.set_exposure(50.0)  # milliseconds, read back as the camera applied it
scope.acquire("A1")  # one picture, staged with a note of how it was taken
scope.save()  # written as OME-Zarr

scope.close()
```

What every call does for you:

- **The limits are asked first.** A move or a setting outside this microscope's limits is refused
  before anything is sent. The limits are always cut to what NIS-Elements itself allows, read live.
- **Every change is read back** where NIS-Elements can report it: the stage, the objective, the
  exposure, the Perfect Focus System. A change that was sent but never seen is recorded, and
  `state()` lists it; the work goes on.
- **A mistake is said in words.** A request that is wrong raises `ValueError`; a change that
  NIS-Elements refused, or that failed, raises `RuntimeError` with the reason.

`connect()` takes a few keys, all optional: `output_root` (where captures go when no folder is given),
`port` (the bridge's, 54470), `request_timeout_s` (30), `config` (a named setup kept on this
computer), and `load_limits`, `load_calibration` and `load_origin` (all `True`).

**Through the ZMART Controller.** Register the driver's folder once on the NIS-Elements computer, then
connect by name in every session. The controller calls the same `Microscope`: `get_xyz` is
`position()`, `set_xyz` is `move_to()`, `set_state` is `set_state()`, and `acquire` is `acquire()`
then `save()`.

```python
from zmart_controller import mic

mic.register_driver(r"C:\ZMART-drivers\zmart_drivers\nikon\nis_elements_ar610")  # once
mic.connect("nikon-nis-elements-ar610")  # every session
```

## The calls

| Call | What it does |
|---|---|
| `position()` | Where the stage is: `x`, `y` and `z` in micrometres from the origin, each with its motor's own reading and the range the stage may travel. `z` is the focus drive. |
| `move_to(x, y, z)` | Move there, in one absolute move, and answer as `position()`, read back once the stage has arrived. |
| `motors()` | The motor of each axis: `stage` for x and y, `focus-drive` for z. |
| `set_origin()` | Make the current position the origin, and save it for every later connection. |
| `channels()` | The optical configurations NIS-Elements has, and the one selected last in this session. |
| `select_channel(name)` | Select an optical configuration. NIS cannot report the selection, so there is no read-back. |
| `exposure()` / `set_exposure(ms)` | The camera exposure, and setting it; read back as the camera applied it (it may round). |
| `objectives()` | The nosepiece: the current slot and the objective in each slot, slots counted from 1. |
| `change_objective(slot)` | Turn the nosepiece, and move the stage and the focus by the measured offset so the same spot stays in view. A pair the calibration does not cover is refused before the nosepiece turns. |
| `autofocus(range_um, speed)` | NIS-Elements' image-based focus sweep around the current focus. |
| `lock_focus(on)` | Switch the Perfect Focus System on (and wait for it to lock) or off. |
| `acquisition_settings()` | How a capture is triggered: `snap`, one picture with the settings as they are. |
| `acquire(position_label=None, acquisition_type="acquisition", output_folder=None)` | Take one picture. It is staged as a TIFF with a note of how it was taken; nothing is written to its final place yet. |
| `save(label=None)` | Write the captures that are waiting as OME-Zarr stores, by the ZMART metadata convention, with a log beside each. |
| `state()` / `set_state(changeable)` | The changeable settings (`objective`, `channel`, `exposure_ms`, `pfs`) and what can only be read: the stage, the camera, the limits in force, what is set up, the changes never confirmed. |
| `reconnect()` | Open the bridge connection again, after the macro was restarted or a request timed out. |
| `close()` | Close the session. Every later call is refused. The bridge keeps running in NIS-Elements. |
| `client` | The bridge connection itself, for the setup notebooks. |

## Setting up the driver

Once per microscope, before the first experiment, the driver needs five things about this microscope:
where its pictures go, how far the stage may travel, the point that counts as (0, 0, 0), which way the
camera is turned relative to the stage, and how far the sample shifts between objectives. Each has a
notebook that only calls the driver's own code:

| What | Notebook |
|---|---|
| Data location | `driver_setup/data_location/notebooks/set_data_location.ipynb` |
| Limits | `driver_setup/limits/notebooks/set_limits.ipynb` |
| Origin | `scope.set_origin()` from Python, with the stage on the spot that should be (0, 0, 0) |
| Orientation | `driver_setup/orientation/notebooks/set_orientation.ipynb` |
| Objective calibration | `driver_setup/optical_calibration/notebooks/calibrate_objective_pair.ipynb` |

Do the limits first: the orientation is measured within them, and the objective calibration needs both.
This driver has no setup wizard yet; the notebooks are the way.

**Where the settings are kept.** On this computer, in
`C:\ProgramData\zmart-microscopy\nikon\ti2\nis_elements_ar610\`, never in the repository. Each item has its
own folder there, with one dated folder per time it was saved; the newest is the one `connect()` loads.
Nothing is overwritten, so you can always see what the microscope was told and when.

**What happens when something is missing.**

- **No limits saved yet, or a damaged limits file:** the session runs within the driver's cautious
  default limits, cut to what NIS-Elements allows, and says so loudly.
- **No origin saved yet:** positions are plain NIS-Elements stage coordinates, and `connect()` warns.
  A damaged origin file stops the connection with how to fix it, because a guessed origin would shift
  every position.
- **No data location saved yet:** nothing is saved until one is set. A capture may still name its own
  folder, and the controller always does.
- **No orientation measured yet:** pictures are saved as the camera sees them, and `get_info` says so.
- **No objective pair calibrated yet:** a change of objective is refused, because the driver cannot
  keep the sample in view. Connect with `load_calibration=False` to change objectives without the
  compensation, for the calibration notebook itself.

## More about the driver

- How every ZMART driver is laid out: [`docs/driver-anatomy.md`](../../../../docs/driver-anatomy.md),
  with a section on this driver; what another microscope must bring:
  [`docs/vendor-contract.md`](../../../../docs/vendor-contract.md).
- The bridge and what it can ask of NIS-Elements: its
  [README](https://github.com/thomdehoog/nis-useq-aiagent/blob/bridge-hybrid-610/1_nis_bridge/README.md).
- The tests: `python zmart_drivers/nikon/nis_elements_ar610/testing/run_ci.py` runs the offline suite
  from the repository root, in two layers: the driver on its own, and the driver inside the ZMART
  Controller when that is installed. The suite stands a mock NIS-Elements behind the real bridge
  server, so the driver is tested over the socket with the real protocol. The check at the microscope
  is in [`testing/hardware/README.md`](testing/hardware/README.md).
