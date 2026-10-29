# Setup wizard for the Evident FLUOVIEW FV4000

A guided way to do the one-time setup of this microscope (driver version 0.7.0b9). It is the
Leica driver's wizard, ported: the **ZMART driver configuration** workflow, with the same five
steps, the same cells and the same wording, so both microscopes are set up the same way. It
looks like the ZMART Interface without its top row: the rail of numbered steps on the left,
and beside it the chosen step's column of cells, a notebook read from top to bottom. There
is no workflow chooser, no canvas and no side column.

It stands on its own: it talks to the driver directly, and needs neither the ZMART
Interface, the ZMART Controller, ZMART Analysis nor ZMART Viewer. Its look is a copy, kept
in `page-src/src/style.css`; when the interface changes its look, change it there too.

```bash
python -m zmart_drivers.evident.fv4000.rdk.driver_setup.wizard
```

The wizard opens in its own window (pywebview). If that is not installed, add `--browser`
to open the page in your web browser instead.

## The five steps

| Step | What happens | What it replaces |
|---|---|---|
| 1. Connect | Connects to FLUOVIEW (its host and port) and confirms the folder this microscope saves its pictures in. | `set_data_location.ipynb` |
| 2. Define limits | Every entry of the limits file, ticked or not, and the four corners of the safe area imported from the stage; then Save and adopt. | `set_limits.ipynb` |
| 3. Define coordinate system origin | Read every drive where the stage stands; Save and adopt makes it (0, 0, 0). | `Microscope.set_origin` |
| 4. Image-to-stage calibration | Start: three pictures and eight candidates. A check picture, then Save and adopt. | `set_orientation.ipynb` |
| 5. Objective calibration | Choose the reference objective; each other objective is a preset. Measure a preset's focus (a z-stack set up in FLUOVIEW) and X/Y (the zoom set so both objectives give the same pixel size), see its offset as three tiles, then Save and adopt. | `calibrate_objective_pair.ipynb` |

**Two folders.** The settings (limits, origin, orientation, calibration) are always saved in
this computer's configuration folder, `C:\ProgramData\zmart-microscopy\evident\fv4000\rdk\`,
in dated folders; the newest is what the driver loads when it connects. That folder is not
chosen; Connect shows it among its checks. The pictures go to the data folder, which is the
one field on the Connect card: a folder on a local disk, suggested as `D:\ZMART-data` (or
`C:\ZMART-data` when there is no `D:`), and saved when you press Connect.

The rail follows the interface's rules. A setup always starts with Connect, and a step opens
once every step before it is done. Its number badge is blue where you stand, green once done,
and orange, with a small "review" tag, when a step it was measured on was saved again after
it: the objective calibration turns orange when the image orientation is measured again,
because its X/Y offsets were measured in the old orientation. Do that step again to make it
green. The driver fills in an unmeasured orientation and an empty calibration the first time
it connects; those do not count as done, so a new microscope shows those steps as still to do.

While a button works it says so ("measuring…") and the other buttons wait. What it came to
is written underneath it, in green, or in red with the reason. Walking away from a step and
back shows what it read and measured in this window.

Where a notebook is saved as the record of a measurement, the wizard saves a small `.json`
record in the same place, so a later reader can see how the numbers came about. The steps
use the same driver code as the notebooks, so they keep the same safety checks; the
notebooks still work.

## Changing the page

The page is written in TypeScript in `page-src/` and built into plain HTML, CSS and
JavaScript in `page/`. **The built `page/` is committed**, so running the wizard needs only
Python: no Node, Vite or any other build tool. Node is only for changing the page:

```bash
cd page-src
npm install
python -m zmart_drivers.evident.fv4000.rdk.driver_setup.wizard --no-window --port 8765   # in another terminal
npm run dev      # the page with live reload, sending its calls to that wizard
npm run build    # rewrites ../page; commit the result
```

## Using it for another driver

`framework.py` knows nothing about either microscope: this driver supplies its list of `Step`s
(`steps.py`) and reuses the Leica driver's server and launcher as they are. The page's step
views are in `page-src/src/steps/`, built from the cells in `page-src/src/cells.ts`; they
differ from the Leica driver's only in the words that name the microscope's software and drives
(FLUOVIEW, the focus motor and the piezo, the host and port of the RDK).

## How it is tested

Three test files in `testing/unit/` cover the wizard without a microscope:

- `test_setup_wizard_steps.py` runs the five real FV4000 steps through the wizard on the
  pretend FLUOVIEW (`testing/helpers/mock_fluoview.py`), whose stage moves the field of view,
  whose scanner can be mounted turned or mirrored, and whose objectives look and focus at
  offsets the test sets. The wizard has to recover the mounting and those offsets. It checks
  what is saved, that the next connection uses it, and that every mistake is answered with a
  sentence. It follows the Leica driver's wizard tests case for case.
- `test_setup_wizard_page.py` opens the built page in Chromium (Playwright) and clicks
  through the whole setup as an operator would, then checks the page's own rules: the
  rail's colours and locks, each step's cells, a busy button, a narrow window and a dark screen.
  Set `ZMART_WIZARD_SCREENSHOTS` to a folder to keep screenshots of the run.
- `test_setup_wizard.py` covers the server (it hands out only the page and the pictures the
  driver named), the window (pywebview is replaced by a stand-in; the server always stops
  when the window closes), and that the wizard imports no other ZMART package.

What these tests cannot show is how the steps behave on a real FV4000 with FLUOVIEW; that
still needs a run on the microscope.
