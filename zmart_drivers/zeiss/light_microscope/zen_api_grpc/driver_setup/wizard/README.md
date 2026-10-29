# Setup wizard for a ZEISS microscope with ZEN

A guided way to do the one-time setup of this microscope, in a window instead of notebooks. It
has the same five steps, the same cells and the same look as the Leica driver's wizard: the rail
of numbered steps on the left, and beside it the chosen step's column of cells, a notebook read
from top to bottom.

It stands on its own: it talks to the driver directly, and needs neither the ZMART Interface, the
ZMART Controller, ZMART Analysis nor ZMART Viewer. Its look is a copy of the interface's, kept in
`page-src/src/style.css`.

```bash
python -m zmart_drivers.zeiss.light_microscope.zen_api_grpc.driver_setup.wizard
```

The wizard opens in its own window (pywebview, part of the `zeiss` extra). If that cannot start,
add `--browser` to open the page in your web browser instead.

Before you start, ZEN must be running with the ZEN API Gateway, and *Enable Unsupervised API
Mode* must be ticked in ZEN's options: the driver's README says how. Without it, ZEN answers
questions but refuses every move, and the wizard says so.

## The five steps

| Step | What happens | What it replaces |
|---|---|---|
| 1. Connect | Connects to ZEN through the ZEN API Gateway (host, port and control token) and confirms the folder this microscope saves its pictures in. | `set_data_location.ipynb` |
| 2. Define limits | Every entry of the limits file, ticked or not; the four corners of the safe stage area and the two ends of the safe focus travel, read from ZEN; then Save and adopt. | `set_limits.ipynb` |
| 3. Define coordinate system origin | Read the stage, the focus and the objective where they stand; Save and adopt makes that point (0, 0, 0). | `Microscope.set_origin` |
| 4. Image-to-stage calibration | Choose a ZEN experiment and Start: three snaps with two small stage moves give the turn of the picture and the pixel size. The three snaps are shown side by side; then Save and adopt. | `set_orientation.ipynb` |
| 5. Objective calibration | Choose the reference objective; each other objective is a preset. Record a feature under the reference, turn to the target, find the same feature again and Record. The offset is shown as three tiles; then Save and adopt. | `calibrate_objective_pair.ipynb` |

**The control token.** The ZEN API Gateway lets a call through only with its token, a password it
makes. Right-click the gateway's icon in the Windows tray to copy it. On the ZEN computer itself
the field may be left empty: the driver reads the token from the gateway's own file, and the card
says so when that file is there. The token is kept only while the wizard runs, never saved.

**ZEN's own numbers.** The corners, the focus ends and the objective offsets are read in ZEN's
coordinates, before any origin is applied, which is why the limits can be measured before the
origin is set and the objective calibration does not depend on the orientation.

**A different reference objective.** The calibration stores every objective's offset against
one reference objective. Choosing another reference than the saved one starts a new calibration:
the page says so in red, and Save and adopt keeps only the objectives measured in this window.

**Two folders.** The settings (limits, origin, orientation, calibration) are always saved in this
computer's configuration folder, `C:\ProgramData\zmart-microscopy\zeiss\light_microscope\zen_api_grpc\`,
in dated folders; the newest is what the driver loads when it connects. That folder is not chosen;
Connect shows it among its checks. The pictures go to the data folder, the output folder on the
Connect card: a folder on a local disk, suggested as `D:\ZMART-data` (or `C:\ZMART-data` when there
is no `D:`), and saved when you press Connect.

The rail follows the interface's rules. A setup always starts with Connect, and a step opens once
every step before it is done. Its number badge is blue where you stand and green once done. The
driver fills in an unmeasured orientation and an empty calibration the first time it connects;
those do not count as done, so a new microscope shows those steps as still to do. After each Save
and adopt the wizard connects again, so what was just saved governs the next steps: the new limits
fence every move, and the new orientation and calibration are in use.

While a button works it says so ("measuring…") and the other buttons wait. What it came to is
written underneath it, in green, or in red with the reason. Walking away from a step and back
shows what it read and measured in this window.

Where a notebook is saved as the record of a measurement, the wizard saves a small `.json` record
in the same place, so a later reader can see how the numbers came about. The orientation step
also keeps its sheet of three snaps there (`orientation_sheet.png`). The steps use the same driver
code as the notebooks, so they keep the same safety checks; the notebooks still work.

## Changing the page

The page is written in TypeScript in `page-src/` and built into plain HTML, CSS and JavaScript in
`page/`. **The built `page/` is committed**, so running the wizard needs only Python: no Node, Vite
or any other build tool. Node is only for changing the page:

```bash
cd page-src
npm install
python -m zmart_drivers.zeiss.light_microscope.zen_api_grpc.driver_setup.wizard --no-window --port 8765   # in another terminal
npm run dev      # the page with live reload, sending its calls to that wizard
npm run build    # rewrites ../page; commit the result
```

`framework.py`, `server.py` and `launcher.py` are the Leica wizard's, with only the title and the
steps changed; `picture.py` draws the orientation sheet as a PNG file with `numpy` alone.

## How it is tested

Three test files in `testing/unit/` cover the wizard without a microscope:

- `test_setup_wizard_steps.py` runs the five real steps through the wizard, on the pretend ZEN of
  `helpers/mock_zen_api.py`, whose snaps are real CZI files of a sample that shifts as the stage
  moves. It checks what is saved, that the next connection uses it, and that every mistake is
  answered with a sentence.
- `test_setup_wizard_page.py` opens the built page in Chromium (Playwright) and clicks through the
  whole setup as an operator would, then checks the page's own rules: the rail's colours and
  locks, each step's cells, a busy button, a narrow window and a dark screen. Set
  `ZMART_WIZARD_SCREENSHOTS` to a folder to keep screenshots of the run. Without Playwright it is
  skipped.
- `test_setup_wizard.py` covers the server (it hands out only the page and the pictures the driver
  named), the window (pywebview is replaced by a stand-in; the server always stops when the window
  closes), and that the wizard imports no other ZMART package.

What these tests cannot show is how the steps behave on a real microscope with ZEN; that still
needs a run at the microscope.
