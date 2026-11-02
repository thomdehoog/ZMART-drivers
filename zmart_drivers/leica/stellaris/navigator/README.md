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
Windows' COM (ActiveX), as the running LAS X offers it, and LAS X's Data Container through Leica's own
.NET interface from the LAS X installation. Install it with Python 3.11 or newer, from the
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
- **LAS X's harmless notices are answered.** An Information or Warning box with only an OK button
  is pressed from here when it is one of the notices known to change nothing, such as "Can not start
  scan!", and the running log says so. Every other box is left for you, and the change that met it is
  refused with its text.

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
| `optimize_format()` | Set the format to the one LAS X computes as optimal for the zoom and the objective, as its "Optimize XY Format" button does, and read it back. |
| `set_detector(name, on=None, gain=None, band=None, operating_mode=None, tau_mode=None, tau_contrast=None, dye=None)` | Switch a detector on or off, set its gain, its band `(begin_nm, end_nm)`, its operating mode (Fast Mode, Counting, Analog, Reflection), its tau mode (Intensity, TauGating, TauScan, TauSeparation, TauInteraction; with a pulsed WLL line), TauContrast or its dye (LAS X's name, such as `"Leica/ALEXA 488"`), and read it back. LAS X takes these only through a whole setting, so the driver saves the current setting, changes Setting 1 in a copy, checks it against the limits and loads it; the answer's `changed` lists everything else LAS X changed with it. In photon counting LAS X keeps the gain fixed. Refused with several sequential settings. |
| `set_channel_colour(detector, colour)` | Give a detector's channel a colour, LAS X's name of a colour table such as `"Green"`, and read it back. It changes how the channel is shown, not the light. Only the detectors of the spectral detection. |
| `dyes(namespace="Leica")` | The names of the dyes in LAS X's dye library, read from LAS X's dye-database service on this computer, as LAS X reads it. |
| `dye(name)` | One dye of the library: its full name, its excitation, emission and multiphoton peaks, and its spectra as `(nm, intensity)` pairs. Its `setting_name` is what `set_detector(..., dye=...)` takes. |
| `set_excitation_line(line)` | Choose the excitation line, the wavelength of a line that is on, or `"auto"`; read back. With one detector on, because LAS X keeps one line per setting. |
| `show_spectra(excitation=None, emission=None)` | Show or hide the dyes' excitation and emission spectra in LAS X's setting panel; read back. |
| `add_setting(copy=False)` | Add a setting to the current job, new or a copy of the current one, as the window's "Add new Setting" does; LAS X makes it the current setting. With more than one setting, the changes through a saved setting (`set_detector` and the like) are refused, because which setting is meant cannot be told. |
| `activate_setting(name)` | Make a setting the current one, as clicking it in the window does, and read it back. Every change after goes into the current setting: the driver names it by its place in the job, as the window does. |
| `delete_setting(name)` | Delete a setting by its name; the job's last setting is never deleted. |
| `enable_setting(on)` | Switch the current setting on or off for scanning, as the window's checkbox does, and read it back. Off, LAS X takes no picture with it. |
| `lock_detector(name, locked=True)` | Keep a detector in use and its setting's excitation line from LAS X's Dye Assistant, or free them, as the window's detector lock does; read back. |
| `lock_channel_colour(locked=True)` | Lock or free the colour of the channel LAS X's detector panel shows, as the window's colour lock does. The lock sends LAS X's hardware server nothing, so the driver presses it in LAS X's window and reads it back from there; it holds the window, not `set_channel_colour`. |
| `autofocus(project=None)` | Run LAS X's autofocus once, with the settings in its Best Focus Scan Options, as the window's Autofocus button does. The answer's `found` says whether LAS X found a focus, with z before and after on the autofocus's drive; the search's two ends are checked against the limits first. `settings()["autofocus_settings"]` shows the drive, the capture range, the method and the precision or steps. |
| `set_autofocus_optics(laser_nm, percent, detector, band, dye)` | Give the Best Focus Setting a laser line with its power, and a detector with its band and dye, as the window's drag-in does, in one command; read back. The power is checked against the laser limits. A line already there keeps its power: see the next. |
| `set_autofocus_laser_power(laser_nm, percent)` | Change the power of a line in the Best Focus Setting, as the window does (written into LAS X's state tree), and read it back; checked against the laser limits. |
| `smartcorr(project=None)` | Run SmartCORR, as the window's Start SmartCORR: a scan through the collar positions of its range, after which LAS X leaves the collar where the picture was best. The answer's `collar` says where. |
| `set_collar_reference()` | Store where the collar stands as this objective's reference, as the window's Set does, and read it back. Written into LAS X's state tree, for the objective in the light path only. |
| `reset_collar_reference()` | Clear this objective's collar reference, as the window's Reset does, and read it back. |
| `collar_to_reference()` | Turn the collar to the position stored for this objective, as the window's GoTo does, and read it back. Refused when none is stored. |
| `pan()` | Where the galvo has moved the scan field: `x` and `y` as LAS X holds them, and `x_um` and `y_um`, the shift in micrometres. Read from LAS X's setting, saved for the purpose: the pan has no value in LAS X's hardware server. |
| `move_pan(x_um, y_um)` | Move the pan by that many micrometres, to the right and up (negative: left and down), and read it back. The driver presses the arrows of LAS X's pan pad, as the operator does, at a zoom raised just enough that whole presses land on the distance, and then sets the zoom back. Refused before anything is pressed when the pan would go beyond LAS X's largest. LAS X's XY panel must be open. |
| `center_pan()` | Set the pan back to the centre. LAS X's window moves the pan without a command the driver can send, and a loaded setting does not set it, but at the smallest zoom LAS X pulls it to the centre; so the driver sets the smallest zoom and then the zoom it had, both read back, and reads the pan. |
| `save_setting(path)` | Save LAS X's current setting to a file, as its Setting panel does. |
| `load_setting(path)` | Load a setting file, one saved in LAS X, say. Its laser powers and detector gains are checked against the limits first; the answer's `changed` lists what changed. |
| `set_filter_wheel(wheel, filter_name)` | Turn the filter wheel of that type (LAS X's number) to the filter of that name, and read it back. LAS X's own galvo and scanner wheels are never turned. |
| `set_laser_power(wavelength_nm, percent)` | Set the power of the laser line at that wavelength, in percent as LAS X's slider shows it, and read it back. Above 0 the line is switched on in the current setting, 0 switches it off; its light source's shutter stays as it was (see `set_laser_shutter`). At most 10 % unless this microscope's limits say otherwise. A white-light line is found when it is on, at its wavelength. |
| `add_laser_line(wavelength_nm, percent=2)` | Switch on a white-light line at that wavelength with that power, as the window's Add Laser does, and read it back: the lines in use are sent with the new one, then its power is set, through the limits (at most 10 % unless they say otherwise). Refused before anything is sent outside the laser's range, within 8 nm of its lines that are on, without a free line, or while a line is locked for the Dye Assistant. |
| `remove_laser_line(wavelength_nm)` | Remove the laser line that is on at that wavelength from the setting, as the window does, and read it back. `set_laser_power(wavelength_nm, 0)` switches a line off but leaves it listed in the window; this takes it away. Refused while a line is locked for the Dye Assistant. |
| `tune_laser_line(from_nm, to_nm)` | Tune the white-light laser's line that is on at `from_nm` to `to_nm`, as the window does, and read it back; LAS X's AOBS follows by itself. Refused before anything is sent outside the laser's range (485 to 685 nm on the simulator), or within 8 nm of another of its lines that is on. A white-light line that is on gets its power with `set_laser_power(wavelength_nm, percent)`, like any other. |
| `set_laser_shutter(wavelength_nm, open_)` | Open or close the shutter of the light source that line comes from, and read it back. |
| `set_aobs_line(wavelength_nm, reflection=, enhanced_dynamics=)` | Set a white-light line's AOBS, as its Configuration window: Fluorescence or Reflection, and Enhanced Dynamics. LAS X has no Enhanced Dynamics in Reflection. Written into LAS X's state tree, as the window does, and read back. |
| `set_tau_channel_colour(detector, channel, colour)` | The colour of TauSeparation's channel 1 or 2 of a detector, as the window's colour pick; only while the detector is in TauSeparation. |
| `set_tau_channel_lifetime(detector, channel, ns)` | The lifetime of TauSeparation's channel 1 or 2, in nanoseconds, as the window's slider; within its scale, 0.07 to 9.37 ns. |
| `find_tau_lifetimes(detector)` | TauSeparation's Find: LAS X looks for the two lifetimes in the data it has; answers whether it found them and the lifetimes now. |
| `start_live()`, `stop_live()` | Start and stop Live, as LAS X's Live button. While Live runs, the lasers are on; stop it before taking pictures. |
| `start_fast_live()` | Start Fast Live, as LAS X's Fast Live button; `stop_live()` stops it. |
| `set_fast_live(scanner, format=, scan_frequency=, overview_zoom=, bidirectional=, no_average=, no_dynamic_signal_enhancement=, selected_setting_only=, no_tau_sted_xtend=)` | Fast Live's Scan Settings for the FOV scanner (`"fov"`) or the resonant one, as its window. Each is checked against LAS X's range first, since LAS X does not hold the format to it. |
| `set_z_stack(drive, begin_um, end_um, step_um)` | Set LAS X's z-stack: its drive (`z-galvo` or `z-wide`), its begin and end in that drive's own micrometres, and its step. Answers the stack as LAS X holds it; LAS X may stretch the end to fit whole steps. Each end is checked like a z move from where the drive stands. From then on `acquire()` takes the whole stack. |
| `set_z_stack_size(size_um)` | Set the z-stack's size around its middle, as the window's Z Size; its new ends are checked against the z limits first. Answers the stack. |
| `set_z_stack_begin_here()`, `set_z_stack_end_here()` | Set the z-stack's begin or end where its drive stands, as the window's Begin and End buttons. |
| `recenter_z_stack()` | Move the z-stack, its size kept, around where its drive stands, as the window's Re-Center; its new ends are checked first. |
| `set_z_stack_sections(sections)` | Set the z-stack's number of sections over its size, by the step that gives it. |
| `move_to_z_stack(place)` | Move the focus to the z-stack's `"begin"`, `"end"` or `"center"`, or to its `"focus"` plane (set with Set Focus in LAS X's window), as the window's Move to buttons. Checked against the z limits like any z move. |
| `clear_z_stack()` | Leave no z-stack, as the window's bin: its begin and end both go where the focus is. |
| `change_objective(slot=None, name=None, magnification=None)` | Turn to another objective, and move the stage by the measured offset so the same spot stays in view. Refused before the turret turns when the objective has another immersion, or the calibration does not cover the change. |
| `acquisition_settings()` | How a capture is triggered: `full`, one picture with the current settings. |
| `acquire(position_label=None, acquisition_type="acquisition", output_folder=None)` | Take one picture with the current settings: one plane with the window's Capture Image, or, when LAS X has a z-stack set, the whole stack with its Start. It stays in LAS X's cache, with a note of how it was taken. Nothing is written yet. |
| `tile_scan(positions, position_label=None, acquisition_type="tile_scan", output_folder=None)` | Take one picture of several fields, as the window's Preview does, at `(x, y)` positions in micrometres from the origin, at the current focus. Afterwards the stage is back at the first field. |
| `row_of_fields(count)` | `count` positions in a row from where the stage stands, a field and a tenth apart, for `tile_scan`. |
| `save(label=None)` | Write the pictures that are waiting as OME-Zarr, one store per field, with their projection and log. |
| `clear_cache()` | Have LAS X let go of the pictures that were saved. Acquiring and saving never do this. |
| `state()` | The five settings and the objective, and what can only be read: the instrument, the objectives, the limits in force, the changes that were never confirmed. |
| `close()` | Close the session. Every later call is refused. |
| `client` | The connection to LAS X's hardware server itself, for the setup notebooks. |

### The settings `change_setting` can change

Each is sent to the current scan with the command LAS X's own software sends (learned in LCS's log),
and read back from LAS X's state tree. A value outside the range or the list in this microscope's
limits file is refused before it is sent; a value LAS X adjusts (a pinhole above its maximum, say) is reported as not
confirmed, with what LAS X set instead.

| Name | Value | Read back within |
|---|---|---|
| `zoom` | the zoom factor, within the zoom range `settings()` reports | 0.1 |
| `scan_speed` | lines per second, one of the speeds LAS X offers | exact |
| `scan_field_rotation` | degrees | 0.5° |
| `line_average` | 1, 2, 4, ... | exact |
| `pinhole_airy` | Airy units | 0.05 AU |
| `scan_mode` | LAS X's name of the scan mode, such as `"xyz"` or `"xyt"` | exact (LAS X shows xyz as xyzN) |
| `autofocus` | `True` or `False` | exact |
| `frame_average` | 1, 2, 4, ... | exact |
| `line_accumulation` | 1, 2, 3, ... | exact |
| `frame_accumulation` | 1, 2, 3, ... | exact |
| `format` | `(x, y)` pixels, such as `(512, 512)` | exact |
| `resonant_scanner` | `True` or `False` | exact |
| `collar` | the objective's correction collar (motCORR), 0 to 100 %, as the window's box sends it | 0.1 % |
| `autofocus_format` | the Best Focus Setting's format, `(x, y)` pixels, with the scan's command for the autofocus | exact |
| `autofocus_speed` | its scan speed, within LAS X's range for it; written into LAS X's state tree, as the window does | exact |
| `autofocus_bidirectional` | `True` or `False`: its scan both ways; written into the state tree | exact |
| `autofocus_zoom` | its zoom, within LAS X's range for it; written into the state tree | 0.01 |
| `autofocus_method` | `"contrast 1"`, `"intensity"`, `"noise"`, `"contrast 2"` or `"reflection"`, as the autofocus's "Optimized for" | exact |
| `autofocus_precision` | 1 (speed) to 5 (accuracy), as its slider; LAS X works out the steps | exact |
| `autofocus_steps` | a fixed number of steps instead, odd, 3 to 1001, as its "Number of Steps" | exact |
| `autofocus_range_um` | its capture range, whole micrometres from 1 to 5000; the search's two ends are checked against the z limits when it runs | 0.01 µm |
| `autofocus_drive` | `"z-galvo"` or `"z-wide"`, its "Focusdrive" | exact |
| `sequential_mode` | `"line"`, `"frame"` or `"stack"`: how several settings are taken in turn, as the window's three buttons; written into LAS X's state tree, as the window does; LAS X rearranges the detectors' channels itself | exact |
| `setting_name` | the current setting's name, 1 to 64 letters, digits, spaces or `_ - . , ( ) / + #`; written into LAS X's state tree, as the window does when the name is edited | exact |
| `smartcorr_range` | `(begin, end, steps)`: the collar positions SmartCORR scans through, 0 to 100 %, and how many (2 to 100); written into LAS X's state tree, as its window does | exact |
| `bidirectional` | `True` or `False`; written into LAS X's state tree, as its window does | exact |
| `phase_x` | Phase X, which lines up the two directions of a bidirectional scan, within LAS X's range (-100 to 100); written into the state tree | 0.1 |
| `pinhole_um` | the pinhole in micrometres, with LAS X's command `SetPinhole`; LAS X's range (100 to 1000 on the simulator) is checked first, since LAS X itself does not keep to it | 0.5 µm |
| `pinhole_unit` | `"um"` or `"airy"`: the unit LAS X shows the pinhole in; the pinhole itself stays | exact |
| `emission_wavelength` | whole nanometres, 380 to 800: the wavelength LAS X works the Airy unit out for | exact |
| `bit_depth` | the bits per pixel, one LAS X offers (8, 12 or 16) | exact |
| `system_optimized_step` | `True` or `False`: the z-stack's step System Optimized, as the window's button; LAS X may stretch the end to fit whole steps | exact |

These and the autofocus's settings have no command LAS X's window sends; the window writes them straight into
LAS X's state tree, and so does the driver, for these seven values only. LAS X does not keep to its
own range there: a pinhole written beyond it opens to the maximum. So the driver reads LAS X's own
minimum and maximum at each write and refuses a value outside them; nothing is written then.
`pinhole_airy` sets the pinhole in Airy units through LAS X's command, which keeps to the range itself.

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
