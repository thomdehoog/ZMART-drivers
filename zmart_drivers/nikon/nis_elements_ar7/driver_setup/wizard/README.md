# Setup wizard for a Nikon microscope on NIS-Elements AR 7

A guided way to do the one-time setup of this microscope, in a window of its own. It is the same
wizard as the Leica driver's: the rail of numbered steps on the left, and beside it the chosen step's
column of cells, a notebook read from top to bottom. It talks to the driver directly, and needs neither
the ZMART Interface, the ZMART Controller, ZMART Analysis nor ZMART Viewer.

```bash
python -m zmart_drivers.nikon.nis_elements_ar7.driver_setup.wizard
```

The wizard opens in its own window (pywebview). If that is not installed, add `--browser` to open the
page in your web browser instead. It runs in the Python that runs the driver, which needs the driver's
packages (`pip install -e ".[nikon]"`).

## The five steps

| Step | What happens | What it replaces |
|---|---|---|
| 1. Connect | Writes `start_bridge.mac` for this computer (run it in NIS: Macro > Run Macro From File), connects to NIS-Elements through it, and confirms the folder this microscope saves its pictures in. | `python -m ...driver_setup.nis_bridge`, `set_data_location.ipynb` |
| 2. Define limits | The stage and focus ranges, the objectives and optical configurations automation may choose, and the guarded hardware it may set; the four corners and the lowest and highest focus imported from the stage; then Save and adopt. | `set_limits.ipynb` |
| 3. Define coordinate system origin | Read the stage, the focus and the objective where they stand; Save and adopt makes it (0, 0, 0). | `Microscope.set_origin` |
| 4. Image-to-stage orientation | Start: three pictures (here, after +x, after +y) and eight candidates; the first picture is shown turned the measured way; then Save and adopt. It can also be chosen by hand. | `set_orientation.ipynb` |
| 5. Hardware settings | Every shutter, lamp, filter wheel and other setting NIS reports, with its value now; try a value (through the limits); then Looks right. | `Microscope.devices()` and `set_device()` |

Where the Leica wizard calibrates its objectives, this one looks at the hardware: the Nikon driver has no
objective calibration (its pixel sizes come from NIS's own calibration), and the hardware settings are
what a new microscope most needs checked.

**Two folders.** The settings (limits, origin, orientation) are always saved in this computer's
configuration folder, `C:\ProgramData\zmart-microscopy\nikon\<microscope>\nis_elements_ar7\`, in dated
folders; the newest is what the driver loads when it connects. That folder is not chosen; Connect shows
it among its checks. The pictures go to the data folder, the one field on the Connect card that is a
folder: a folder on a local disk, suggested as `D:\ZMART-data` (or `C:\ZMART-data` when there is no
`D:`), and saved when you press Connect.

The rail follows the Leica wizard's rules. A setup always starts with Connect, and a step opens once every
step before it is done. Its number badge is blue where you stand and green once done. While a button
works it says so ("measuring…") and the other buttons wait. What it came to is written underneath it, in
green, or in red with the reason.

Where a notebook is saved as the record of a measurement, the wizard saves a small `.json` record beside
the saved setting (`set_limits_wizard_record.json`, `set_orientation_wizard_record.json`), so a later
reader can see how the numbers came about. The steps use the same driver code as the notebooks, so they
keep the same safety checks; the notebooks still work.

## Changing the page

The page is written in TypeScript in `page-src/` and built into plain HTML, CSS and JavaScript in
`page/`. **The built `page/` is committed**, so running the wizard needs only Python: no Node, Vite or
any other build tool. Node is only for changing the page:

```bash
cd page-src
npm install
python -m zmart_drivers.nikon.nis_elements_ar7.driver_setup.wizard --no-window --port 8765   # in another terminal
npm run dev      # the page with live reload, sending its calls to that wizard
npm run build    # rewrites ../page; commit the result
```

`framework.py` and `server.py` are the Leica wizard's, copied unchanged (a test keeps them the same);
`steps.py` and the views in `page-src/src/steps/` are this driver's.

## How it is tested

Two test files in `testing/unit/` cover the wizard without a microscope:

- `test_setup_wizard_steps.py` presses each step's buttons through the wizard, on the bridge stand-in
  over the pretend NIS, and checks what was saved.
- `test_setup_wizard_page.py` opens the built page in a real browser (Chromium, through Playwright) and
  walks the whole setup the way an operator would, from Connect to Hardware settings. It is skipped where
  no browser can start. Set `ZMART_WIZARD_SCREENSHOTS` to a folder to keep a screenshot of each step.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB), University of Zurich
(thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com). Adapted from the Leica driver's setup wizard.
