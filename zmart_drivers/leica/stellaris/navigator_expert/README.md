# navigator_expert — Leica LAS X (STELLARIS) microscope driver

`navigator_expert` drives a **Leica STELLARIS** confocal from Python, through LAS X. It works on its
own, from a notebook or a script, and it is the Leica driver behind the ZMART Controller. Every change
is checked against this microscope's limits before it is sent, and read back afterwards, so you know
what the microscope really did.

- **Author:** Thom de Hoog (ZMB, University of Zurich) · thom.dehoog@zmb.uzh.ch · thomdehoog@gmail.com
- **License:** see the repository root [`LICENSE`](../../../../LICENSE).
- **Status:** **Beta (`0.7.0b8`).** 0.7.0b7 added the [setup wizard](driver_setup/wizard/README.md);
  0.7.0b8 adds a check of it to the hardware CI and splits the CI into the driver on its own and the
  driver inside the ZMART Controller. Both are tested offline and not yet on the LAS X simulator or a
  STELLARIS. The driver itself is unchanged from 0.7.0b6, which passed the quick hardware acceptance on the LAS X simulator on 23 October 2026; 0.7.0b5
  passed it on the STELLARIS on 21 October 2026. Before the driver is used unattended, the findings in
  [`docs/leica/RELEASE_CANDIDATE_REVIEW.md`](../../../../docs/leica/RELEASE_CANDIDATE_REVIEW.md) need to be fixed.
- **Known limits of this beta:**
  - Scan mode is never changed by the validators, because changing it removes a job's z-stack.
  - Detector gain cannot be tested on this hardware: LAS X gives the HyD S 2 a single value.
  - A filter wheel's spectrum mode is applied by LAS X but the driver cannot confirm it.
  - Windows must allow long paths, or captures must be saved to a short folder: a path of more than
    259 characters stops a save, and the driver says so.

## How to call the driver

The driver runs on the computer that runs LAS X, with the Navigator Expert add-in installed, because it
loads Leica's interface into Python. Install it with Python 3.11 or newer, from the repository's root
folder:

```bash
pip install -e ".[leica]"
```

Set the microscope up once before the first experiment (see [Setting up the driver](#setting-up-the-driver)
below). Then every session starts with `connect()`, which opens LAS X, loads this microscope's settings
and hands back a `Microscope`. Each call in the table below is a method of that `Microscope`:

```python
import zmart_drivers.leica.stellaris.navigator_expert as navigator_expert

scope = navigator_expert.connect()

scope.move_to(100.0, 250.0, 0.0)          # micrometres from the saved origin
scope.select_job("HiRes")
scope.change_setting("zoom", value=2.0)   # the selected job, read back
scope.acquire("A1")                       # the picture stays in LAS X's cache
scope.save()                              # written as OME-Zarr
scope.clear_cache()                       # LAS X lets go of the saved pictures

scope.close()
```

What every call does for you:

- **The limits are asked first.** A move or a setting outside this microscope's limits is refused
  before anything is sent.
- **Every change is read back.** A change that was sent but never seen is recorded, and `state()` lists
  it; the work goes on.
- **A mistake is said in words.** A request that is wrong raises `ValueError`; a change that LAS X
  refused, or that failed, raises `RuntimeError` with the reason.

`connect()` takes a few keys, all optional: `output_root` (where captures go when no folder is given),
`load_limits`, `load_calibration` and `load_origin` (all `True`), `calibration_name`, `client` and
`api_delay_ms`.

**Through the ZMART Controller.** Register the driver's folder once on the LAS X computer, then connect
by name in every session. The controller calls the same `Microscope`: `get_xyz` is `position()`,
`set_xyz` is `move_to()`, `set_state` is `select_job()`, and `acquire` is `acquire()` then `save()`.

```python
from zmart_controller import mic

mic.register_driver(r"C:\ZMART-drivers\zmart_drivers\leica\stellaris\navigator_expert")  # once
mic.connect("stellaris")                                                                 # every session
```

## The calls

| Call | What it does |
|---|---|
| `position()` | Where the stage is: `x`, `y` and `z` in micrometres from the origin, each with its motors' own readings and the range a picture can show. `z` is the focus, the two z drives added together. |
| `move_to(x, y, z)` | Move there, and answer as `position()`, read back once the stage has arrived. The focus moves with z-wide. |
| `motors()` | The motors of each axis, and the one used to move it. |
| `set_origin()` | Make the current position the origin, and save it for every later connection. |
| `zero_z_galvo()` | Park the z-galvo at 0 without changing the focus. |
| `jobs()` | The LAS X jobs by name: `selected`, `jobs` and `autofocus_jobs`. |
| `select_job(name)` | Select a job. Navigator Expert's autofocus jobs are not selected. |
| `job_settings(job=None)` | The settings of a job, as LAS X reports them. Without a job, the selected one. |
| `change_setting(name, job=None, **arguments)` | Change one setting of a job and read it back; see the table below. |
| `change_objective(slot=None, name=None, magnification=None)` | Change the objective, and move the stage by the measured offset so the same spot stays in view. |
| `acquisition_settings()` | How a capture is triggered: `full`, the whole selected job. |
| `acquire(position_label=None, acquisition_type="acquisition", output_folder=None)` | Run the selected job. The picture stays in LAS X's cache, with a note of how it was taken. Nothing is written yet. |
| `save(label=None)` | Write the captures that are waiting as OME-Zarr, with their projection and log. |
| `clear_cache()` | Have LAS X let go of the pictures that were saved. Acquiring and saving never do this. |
| `state()` | The selected job, and what can only be read: the instrument, the objectives, the limits in force, the changes that were never confirmed. |
| `close()` | Close the session. Every later call is refused. |
| `client` | The LAS X client itself, for the setup notebooks. |

### The settings `change_setting` can change

The name is the first column. The arguments are given by name, for example
`change_setting("laser_intensity", setting_index=0, beam_route="...", line_index=0, value=0.05)`.
A per-setting entry targets one sequential setting of the job, by its `setting_index`.

| Name | Arguments | Read back within |
|---|---|---|
| `zoom` | `value` | 0.1 |
| `scan_speed` | `value` | exact |
| `scan_resonant` | `enable` | exact |
| `scan_mode` | `mode`, such as `"xyz"` | exact |
| `sequential_mode` | `mode`: `"Line"`, `"Frame"` or `"Stack"` | exact |
| `scan_field_rotation` | `angle` | 0.5° |
| `image_format` | `format_str`, such as `"512 x 512"` | exact |
| `z_stack_definition` | `begin_um`, `end_um`; `old_begin_um` or `old_end_um` set to anything resets that end | 1.0 µm |
| `z_stack_step_size` | `step_size_um` | 0.5 µm |
| `z_stack_size` | `size_um` | 1.5 µm |
| `frame_accumulation` | `setting_index`, `value` | exact |
| `frame_average` | `setting_index`, `value` | exact |
| `line_accumulation` | `setting_index`, `value` | exact |
| `line_average` | `setting_index`, `value` | exact |
| `pinhole_airy` | `setting_index`, `value` | 0.05 AU |
| `detector_gain` | `setting_index`, `beam_route`, `value` | 1.0 |
| `laser_intensity` | `setting_index`, `beam_route`, `line_index`, `value` | 0.005 (0 to 1) |
| `laser_shutter` | `setting_index`, `beam_route`, `activate` | exact |
| `filter_wheel_slot` | `setting_index`, `beam_route`, `filter_wheel_type`, `slot_index` | exact |
| `filter_wheel_spectrum` | `setting_index`, `beam_route`, `filter_wheel_type`, `position` | 1 nm |

## Setting up the driver

Once per microscope, before the first experiment, the driver needs five things about this microscope:
where its pictures go, how far the stage may travel, the point that counts as (0, 0, 0), which way the
picture is turned relative to the stage, and how far the sample shifts between objectives.

**With the setup wizard (recommended).** One window walks you through all of them, step by step, and
saves each where the driver loads it:

```bash
python -m zmart_drivers.leica.stellaris.navigator_expert.driver_setup.wizard
```

Its steps are Connect (with the data folder), Define limits, Define coordinate system origin,
Image-to-stage calibration and Objective calibration. Its [README](driver_setup/wizard/README.md) says
more about each.

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
`C:\ProgramData\zmart-microscopy\leica\stellaris_y42h93\navigator_expert\`, never in the repository.
Each item has its own folder there, with one dated folder per time it was saved; the newest is the one
`connect()` loads. Nothing is overwritten, so you can always see what the microscope was told and when.
On a computer set up with a version before 0.7.0b1 that folder is still called `stellaris5_y42h93`:
rename it once to `stellaris_y42h93`, or the driver finds no limits.

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
| CAM API command log | `C:\ProgramData\Leica Microsystems\LAS X\lcsCommand.log` |
| LAS X dialog / MessageBox log | `C:\ProgramData\Leica Microsystems\LAS X\MatrixScreener.log` |
| CAM API assemblies (runtime) | `C:\Program Files\Leica Microsystems CMS GmbH\LAS X\AddIns\NavigatorExpert` |

Defaults live beside the code that uses them: `LasxApiProfile` in `primitives/cam_api/client.py`
and `LogReaderProfile` in `primitives/log/reads.py`. Override them there, not at call sites.

## More about the driver

- **How it is built:** [`docs/leica/architecture.md`](../../../../docs/leica/architecture.md): its parts,
  how a call travels to LAS X, the two routes, what a change answers, pictures and coordinates, in short
  tables.
- How every ZMART driver is laid out: [`docs/driver-anatomy.md`](../../../../docs/driver-anatomy.md);
  what another microscope must bring: [`docs/vendor-contract.md`](../../../../docs/vendor-contract.md).
- The notes, plans and reviews written while building the driver, and the runs on the microscope:
  [`docs/leica/`](../../../../docs/leica/).
- The tests: `python zmart_drivers/leica/stellaris/navigator_expert/testing/run_ci.py` runs the offline
  suite from the repository root, in two layers: the driver on its own, and the driver inside the
  ZMART Controller when that is installed. The checks at the microscope are in
  [`testing/hardware/README.md`](testing/hardware/README.md).
