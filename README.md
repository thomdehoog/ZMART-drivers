# ZMART Drivers

[![python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)
[![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![status](https://img.shields.io/badge/status-beta-orange)](#status)

<img src="docs/zmart-drivers-icon.png" align="left" width="150" alt="ZMART Drivers">

The **ZMART Drivers** connect the [ZMART Controller](https://github.com/ZMB-UZH/ZMART-controller) to real microscopes.
A driver translates the controller's short list of commands into what one particular microscope understands, through that vendor's own programming interface.
It is part of [**ZMART**](https://github.com/ZMB-UZH/ZMART-microscopy) (ZMB's Microscopy-Agnostic Research Toolkit), the tools we use for smart microscopy
at the Center for Microscopy and Image Analysis (ZMB), University of Zurich.
<br clear="left"/>

## The Problem

Every microscope speaks its own language: its own programming interface, its own names for
settings, its own habits. A workflow written against one vendor's interface does not run on
another, and it has to take care of that microscope's safety limits and coordinates itself.

## The Solution

A driver takes care of everything that is specific to the instrument, so the workflow does
not have to. A workflow talks only to the [ZMART Controller](https://github.com/ZMB-UZH/ZMART-controller),
and the driver plugged into it does the rest:

1. **It speaks the vendor's language.** Each microscope has its own interface, its own names
   for settings, and its own habits. The driver hides these behind the controller's commands.
2. **It keeps the microscope safe.** Before any command moves hardware, the driver checks it
   against the limits measured for this microscope, such as how far the stage may travel.
3. **It gives you one coordinate system.** Positions are reported in micrometres, relative to
   a zero point the operator chooses. When you change objectives, the driver uses the
   calibration to keep that zero point on the same spot of your sample.
4. **It confirms what happened.** After a command, the driver reads the microscope back to
   check that it really did what was asked, and says so in its answer.

How a driver is organised inside, so that the safety behaviour is the same on every
microscope, is described in [the anatomy of a ZMART driver](docs/driver-anatomy.md).

## The drivers in this beta

| Microscope | Vendor interface | Driver | Status |
|---|---|---|---|
| Leica STELLARIS | LAS X: its hardware server first, the Python (CAM) API behind it | [`zmart_drivers/leica/stellaris/navigator_expert/`](zmart_drivers/leica/stellaris/navigator_expert/README.md) | **Beta `0.7.0b9`.** 0.7.0b9 keeps the settings in configs chosen when connecting, writes a mean pyramid into every store, and fixes what the first end-to-end run on the STELLARIS found. 0.7.0b6 tested on the LAS X simulator, 0.7.0b5 also on a STELLARIS; 0.7.0b7 adds the setup wizard and 0.7.0b8 its hardware check and a CI that also runs without the controller, tested offline. Laid out as [the anatomy of a ZMART driver](docs/driver-anatomy.md) describes. |
| ZEISS light microscopes (ZEN 3.13) | The ZEN API over gRPC, through the ZEN API Gateway (ZEISS's `zen_api` 2025.10.1) | [`zmart_drivers/zeiss/light_microscope/zen_api_grpc/`](zmart_drivers/zeiss/light_microscope/zen_api_grpc/README.md) | **Beta `0.1.0b1`, not yet run against ZEN.** Tested offline against a stand-in for ZEN, and over ZEISS's own classes, gRPC and TLS against a small gateway inside the tests. Laid out as the anatomy describes, with the Leica driver's dispatcher. |

The Nikon, mesoSPIM and Evident drivers, and an earlier ZEISS driver, are in the repository's
history.

The driver works by itself, from a notebook or a script, and it plugs into the ZMART
Controller. Its README says how to do both. Before the first experiment, a few things are set
up once on the microscope: the travel limits, the image orientation, the objective calibration
and the zero point of the coordinates. The notebooks for them are in the driver's
`driver_setup/` folder, and a [setup wizard](zmart_drivers/leica/stellaris/navigator_expert/driver_setup/wizard/README.md) walks through the same steps in a window: `python -m zmart_drivers.leica.stellaris.navigator_expert.driver_setup.wizard`. What is set up is saved in the computer's configuration folder,
`C:\ProgramData\zmart-microscopy` on Windows, and loaded every time the driver connects.

## Try it yourself

The driver needs Python 3.11 or newer. Download this repository, open a terminal in its root
folder, and install it with the Leica extra. The driver works on its own; add the `controller`
extra to drive it through the ZMART Controller as well.

```bash
pip install -e ".[leica]"              # the driver on its own
pip install -e ".[leica,controller]"   # with the ZMART Controller
```

The ZEISS driver installs the same way with the `zeiss` extra, which brings ZEISS's `zen_api`
package from ZEISS's own repository; its [README](zmart_drivers/zeiss/light_microscope/zen_api_grpc/README.md)
says how to set ZEN up for it.

The driver runs on the computer that runs LAS X, because it loads Leica's interface into
Python. Use it by itself:

```python
import zmart_drivers.leica.stellaris.navigator_expert as navigator_expert

scope = navigator_expert.connect()
scope.move_to(100.0, 250.0, 0.0)
scope.acquire("A1")
scope.save()
```

Or, with the controller installed, register it once with the ZMART Controller, and connect by
name in every session:

```python
from zmart_controller import mic

mic.register_driver("C:/ZMART-drivers/zmart_drivers/leica/stellaris/navigator_expert")
mic.connect("stellaris")
print(mic.get_info()["output"]["description"])
```

From 0.7.0b2 the driver's folder is `leica/stellaris`, one folder for every STELLARIS; which
microscope it is stays in its configuration. A computer that registered the driver from
`leica/stellaris_y42h93` registers it again from the new folder.

### Status

This repository is a **beta**. Nothing in it is released yet. The driver has been tested on the
LAS X simulator and on a STELLARIS. Its
[release candidate review](docs/leica/RELEASE_CANDIDATE_REVIEW.md) lists what must be fixed
before release. Do not leave the driver running unattended until the high-severity findings in
that review are resolved.

## Testing

The driver carries its own offline test suite, which runs without a microscope or vendor
software. Install the test extra, then run it from the root folder of this repository:

```bash
pip install -e ".[leica,test]"                # the driver on its own
pip install -e ".[leica,controller,test]"     # also with the ZMART Controller
python zmart_drivers/leica/stellaris/navigator_expert/testing/run_ci.py
```

The ZEISS driver's suite runs the same way:
`pip install -e ".[zeiss,test]"`, then
`python zmart_drivers/zeiss/light_microscope/zen_api_grpc/testing/run_ci.py`.

The suite has two layers. The first checks the driver on its own, which is how many people use
it. The second checks the driver inside the ZMART Controller; it runs when the controller is
installed and is shown as `SKIP` when it is not. Add `--controller` to make a missing
controller a failure instead, on a computer that is meant to have it.

The continuous-integration workflow in `.github/workflows/` runs both layers of each driver on
Linux and Windows, with Python 3.11 and 3.12, and each driver alone on both systems as well.

## Author

Thom de Hoog, Center for Microscopy and Image Analysis (ZMB), University of Zurich
(thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).

## License

MIT License. See the [LICENSE](LICENSE) file for details.

## Links

- [ZMART Microscopy](https://github.com/ZMB-UZH/ZMART-microscopy): the main repository, with the workflows
- [ZMART Controller](https://github.com/ZMB-UZH/ZMART-controller): the microscope-independent layer these drivers plug into
- [ZMART analysis](https://github.com/ZMB-UZH/ZMART-analysis): the analysis engine that runs between acquisitions
- [ZMART AI agent](https://github.com/thomdehoog/ZMART-ai-agent): drive any microscope by chatting, through the controller
- [ZMART viewer](https://github.com/ZMB-UZH/ZMART-viewer): the viewer
- [Center for Microscopy and Image Analysis (ZMB)](https://www.zmb.uzh.ch), University of Zurich
