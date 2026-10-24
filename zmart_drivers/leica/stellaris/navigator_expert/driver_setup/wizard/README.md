# Setup wizard for the Leica STELLARIS 5

A guided way to do the one-time setup (version 0.7.0b6) of this microscope. It looks like the ZMART
Interface (the steps are numbered on the left, the chosen step is on the right), but it
runs on its own and talks to the driver directly. You do not need the ZMART Interface or
the ZMART Controller.

```bash
python -m zmart_drivers.leica.stellaris.navigator_expert.driver_setup.wizard
```

The wizard opens in its own window (pywebview). If that is not installed, add `--browser`
to open the page in your web browser instead.

## The five steps

| Step | What it sets | The notebook it replaces |
|---|---|---|
| 1. Data location | The folder every run is saved under. | `set_data_location.ipynb` |
| 2. Travel limits | How far the stage may safely travel, from four recorded corners. | `set_limits.ipynb` |
| 3. Image orientation | How the camera image is turned compared with the stage. | `set_orientation.ipynb` |
| 4. Objective calibration | The shift between a reference and a target objective. | `calibrate_objective_pair.ipynb` |
| 5. Zero point | The stage spot that counts as (0, 0, 0). | `Microscope.set_origin` |

A step opens once the ones before it are done. A step that is already set up on this
computer shows as done, and can be run again to replace it. Where a notebook is saved as the
record of a measurement, the wizard saves a small `.json` record in the same place.

The steps use the same driver code as the notebooks, so they keep the same safety checks.
The notebooks still work.

## Changing the page

The page is written in TypeScript in `page-src/` and built into plain HTML, CSS and
JavaScript in `page/`. **The built `page/` is committed**, so running the wizard needs only
Python: no Node, Vite or any other build tool. Node is only for changing the page:

```bash
cd page-src
npm install
python -m zmart_drivers.leica.stellaris.navigator_expert.driver_setup.wizard --no-window --port 8765   # in another terminal
npm run dev      # the page with live reload, sending its calls to that wizard
npm run build    # rewrites ../page; commit the result
```

## Using it for another driver

`framework.py` knows nothing about Leica. A driver supplies a list of `Step`s (a title, a
sentence of purpose, and the actions the page can call) and reuses the server and launcher.
The page's step views are in `page-src/src/steps.ts`.
