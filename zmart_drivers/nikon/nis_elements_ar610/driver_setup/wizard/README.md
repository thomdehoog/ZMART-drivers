# Setup wizard for a Nikon microscope with NIS-Elements AR 6.10

A guided way to do the one-time setup of this microscope (driver version 0.7.0b9). It has the same
five steps, the same look and the same rules as the Leica driver's wizard: the rail of numbered
steps on the left, and beside it the chosen step's column of cells, a notebook read from top to
bottom. It talks to the driver directly, and needs neither the ZMART Interface, the ZMART
Controller, ZMART Analysis nor ZMART Viewer.

Start NIS-Elements on this computer and run `start_bridge.mac` in it (the driver's
[README](../../README.md) says how). Then:

```bash
python -m zmart_drivers.nikon.nis_elements_ar610.driver_setup.wizard
```

The wizard opens in its own window (pywebview). If that is not installed, add `--browser` to open
the page in your web browser instead.

## The five steps

| Step | What happens | What it replaces |
|---|---|---|
| 1. Connect | Connects to NIS-Elements through the bridge (this computer, port 54470 unless you changed it) and confirms the folder this microscope saves its pictures in. | `set_data_location.ipynb` |
| 2. Define limits | Every entry of the limits file, ticked or not, and the four corners of the safe area imported from the stage; then Save and adopt. The wizard connects again, so the new limits fence every move of the steps after it. | `set_limits.ipynb` |
| 3. Define coordinate system origin | Read the stage, the focus drive and the objective where the stage stands; Save and adopt makes it (0, 0, 0). | `Microscope.set_origin` |
| 4. Image-to-stage calibration | Start: the stage steps along X and along Y, a picture at each place, and comes back. The turn and the pixel size are shown; then a check picture, then Save and adopt. | `set_orientation.ipynb` |
| 5. Objective calibration | Choose the reference objective; each other objective is a preset. Under the reference, centre and focus a feature and Record; turn to the target (nothing else moves), centre and focus the same feature and Record. The offset is shown as three tiles; then Save and adopt. | `calibrate_objective_pair.ipynb` |

**How it differs from the Leica wizard.** The steps are the same; two are measured differently,
as this driver's notebooks measure them:

- **The orientation** is found from two small stage steps. Choose a step smaller than the field of
  view, so the pictures overlap: 50 µm suits a 10x objective; take less at higher magnification.
- **The objective calibration** is measured by your eye, not by matching pictures: you centre and
  focus the same feature under each objective, and the driver takes the difference between the two
  places the stage stood. Choose a small feature with a clear edge, and centre it as exactly as you
  can; the offset is only as good as the centring.

When a saved calibration counts from another reference objective than the one you choose, the wizard
counts its other offsets again from the new reference, so that every offset in the file shares one.
If the saved calibration does not hold the new reference at all, it says so and saves nothing.

**Two folders.** The settings (limits, origin, orientation, calibration) are always saved in this
computer's configuration folder, `C:\ProgramData\zmart-microscopy\nikon\ti2\nis_elements_ar610\`, in
dated folders; the newest is what the driver loads when it connects. That folder is not chosen;
Connect shows it among its checks. The pictures go to the data folder, which is the one field on the
Connect card to fill: a folder on a local disk that already exists, suggested as `D:\ZMART-data`
(or `C:\ZMART-data` when there is no `D:`), and saved when you press Connect.

The rail follows the interface's rules. A setup always starts with Connect, and a step opens once
every step before it is done. Its number badge is blue where you stand, green once done, and orange,
with a small "review" tag, when a step it was measured on was saved again after it: the objective
calibration turns orange when the image orientation is measured again. The driver fills in an
unmeasured orientation and an empty calibration the first time it connects; those do not count as
done, so a new microscope shows those steps as still to do.

Where a notebook is saved as the record of a measurement, the wizard saves a small `.json` record in
the same place, so a later reader can see how the numbers came about.

## Changing the page

The page is written in TypeScript in `page-src/` and built into plain HTML, CSS and JavaScript in
`page/`. **The built `page/` is committed**, so running the wizard needs only Python. Node is only for
changing the page:

```bash
cd page-src
npm install
python -m zmart_drivers.nikon.nis_elements_ar610.driver_setup.wizard --no-window --port 8765   # in another terminal
npm run dev      # the page with live reload, sending its calls to that wizard
npm run build    # rewrites ../page; commit the result
```

`framework.py`, `server.py`, `launcher.py` and the page's cells and style are the Leica wizard's,
unchanged; the Nikon steps are `steps.py` and `page-src/src/steps/`.

## How it is tested

Three test files in `testing/unit/` cover the wizard without a microscope, through the real bridge
server to the mock NIS-Elements:

- `test_setup_wizard_steps.py` runs the five steps through the wizard's actions as the page calls
  them, checks what is saved, that the next connection uses it, and that every mistake is answered
  with a sentence.
- `test_setup_wizard_page.py` opens the built page in Chromium (Playwright) and clicks through the
  whole setup as an operator would. Set `ZMART_WIZARD_SCREENSHOTS` to a folder to keep screenshots.
- `test_setup_wizard.py` covers the server, the window and the command line, and that the wizard
  imports no other ZMART package.

What these tests cannot show is how the steps behave at a real Ti2 with NIS-Elements; that still
needs a run at the microscope.
