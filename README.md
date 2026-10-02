# ZMART Drivers

[![python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)
[![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![status](https://img.shields.io/badge/status-release%20candidate-orange)](#status)

**ZMART drivers** connect the [ZMART Controller](https://github.com/thomdehoog/ZMART-controller)
to real microscopes. The controller gives every workflow the same short list of commands:
move in x, y and z, read and apply the microscope's settings, and acquire an image. A driver
translates those commands into what one particular microscope understands, through that
vendor's own programming interface.

[ZMART](https://github.com/thomdehoog/ZMART-microscopy) (ZMB's Microscopy-Agnostic Research Toolkit) is the set of tools we use for smart
microscopy at the Center for Microscopy and Image Analysis (ZMB), University of Zurich.

## What a driver does for you

A workflow should not have to know which microscope it runs on. The driver takes care of
everything that is specific to the instrument, so the workflow does not have to:

1. **It speaks the vendor's language.** Each microscope has its own interface, its own names
   for settings, and its own habits. The driver hides these behind the controller's commands.
2. **It keeps the microscope safe.** Before any command moves hardware, the driver checks it
   against the limits measured for this microscope, such as how far the stage may travel.
3. **It gives you one coordinate system.** Positions are reported in micrometres, relative to
   a zero point the operator chooses. When you change objectives, the driver uses the
   calibration to keep that zero point on the same spot of your sample.
4. **It confirms what happened.** After a command, the driver reads the microscope back to
   check that it really did what was asked, and says so in its answer.

## Drivers in this repository

| Microscope | Vendor interface | Driver | Status |
|---|---|---|---|
| Leica STELLARIS 5 | LAS X Python (CAM) API, Navigator Expert | [`zmart_drivers/leica/stellaris5_y42h93/navigator_expert/`](zmart_drivers/leica/stellaris5_y42h93/navigator_expert/README.md) | **Release candidate `6.0.0rc1`**, not yet released. Tested on the LAS X simulator and a real STELLARIS. See its [release candidate review](zmart_drivers/leica/stellaris5_y42h93/navigator_expert/RELEASE_CANDIDATE_REVIEW.md). |
| Nikon Ti2 | NIS-Elements AR 6.10, a bridge inside NIS | [`zmart_drivers/nikon/nis_elements_6_10/`](zmart_drivers/nikon/nis_elements_6_10/README.md) | Working on the NIS-Elements Ti2 simulator; not yet run on a real microscope. |
| ZEISS (ZEN blue or ZEN core) | ZEN API (`zen_api`, gRPC) | [`zmart_drivers/zeiss/zenapi/`](zmart_drivers/zeiss/zenapi/README.md) | Speaks the published ZEN API, tested against its own fake gateway; not yet run against ZEN itself. |
| mesoSPIM light-sheet | mesoSPIM-control, Remote Scripting | [`zmart_drivers/mesospim/`](zmart_drivers/mesospim/README.md) | Working through Remote Scripting. mesoSPIM-control's own Remote Control is the newer way in; this driver has not moved to it yet. |
| Evident FLUOVIEW FV4000 | Remote Development Kit (RDK) | [`zmart_drivers/evident/`](zmart_drivers/evident/README.md) | Investigation only: a spike that proves the connection against a pretend RDK server. No driver yet. |

The Leica driver is the furthest along. The others work in their own test setups but have
not been reviewed for release, and none of the five is plugged into the controller by its
folder yet: that needs a `zmart.json` beside each driver's functions (see the controller's
[driver guide](https://github.com/thomdehoog/ZMART-controller/blob/main/docs/driver.md)).

Drivers are organised by vendor, then by microscope, then by the vendor interface they use:
`zmart_drivers/<vendor>/<microscope>/<interface>/`. A single microscope can therefore have
more than one driver if its vendor offers more than one way in.

## Getting started with the Leica driver

The Leica driver runs on the computer that runs LAS X, because it loads Leica's interface
directly into Python. Its [README](zmart_drivers/leica/stellaris5_y42h93/navigator_expert/README.md)
explains the installation, the setup notebooks you run once per microscope (limits,
orientation and calibration), and how to drive the microscope from Python.

To use the driver through the ZMART Controller, install the controller, start Python in the
root folder of this repository, and register the driver by its module name:

```python
import zmart_controller

zmart_controller.register_driver("zmart_drivers.leica.stellaris5_y42h93.navigator_expert.zmart_adapter")
zmart_controller.get_instruments()
```

Registering it by folder path does not work yet for this driver; the release candidate review
explains why (finding L8).

## Status

This repository is a **release candidate**. It collects the drivers that are ready to be
reviewed for release, separately from the rest of ZMART-microscopy. Nothing in it is released
yet. The Leica driver has been tested on the LAS X simulator and on a real STELLARIS, and its
[release candidate review](zmart_drivers/leica/stellaris5_y42h93/navigator_expert/RELEASE_CANDIDATE_REVIEW.md)
lists what must be fixed before release. Please do not leave the Leica driver running
unattended until the high-severity findings in that review are resolved.

The ZMART Controller needs Python 3.11 or newer, so a driver used through the controller needs
it too. The Leica driver on its own also runs on Python 3.10.

## Testing

Every driver carries its own offline test suite, which runs without a microscope; each
driver's README says how to run it. For the Leica driver:

```bash
cd zmart_drivers/leica/stellaris5_y42h93/navigator_expert
python -m pip install -r requirements-dev.txt
python run_ci.py
```

The adapter tests also need the ZMART Controller to be importable. The continuous-integration
workflow in `.github/workflows/` runs the Leica suite; the other drivers are not in it yet.

## Author

Thom de Hoog, Center for Microscopy and Image Analysis (ZMB), University of Zurich
(thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).

## License

MIT License. See the [LICENSE](LICENSE) file for details.

## Links

- [ZMART Microscopy](https://github.com/thomdehoog/ZMART-microscopy): the main repository, with the workflows
- [ZMART Controller](https://github.com/thomdehoog/ZMART-controller): the microscope-independent layer these drivers plug into
- [ZMART analysis](https://github.com/thomdehoog/ZMART-analysis): the analysis engine that runs between acquisitions
- [ZMART AI agent](https://github.com/thomdehoog/ZMART-ai-agent): drive any microscope by chatting, through the controller
- [ZMART viewer](https://github.com/thomdehoog/ZMART-viewer): the viewer
- [Center for Microscopy and Image Analysis (ZMB)](https://www.zmb.uzh.ch), University of Zurich
