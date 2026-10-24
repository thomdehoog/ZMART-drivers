"""A setup wizard for the Leica STELLARIS 5, run once per microscope.

The wizard walks through the same five steps as the setup notebooks (where the
data is saved, the safe travel limits, the image orientation, the objective
calibration and the zero point), with the steps listed on the left and the
current step on the right, as in the ZMART Interface. It talks to the driver
directly, so the ZMART Interface and the ZMART Controller are not needed.

Start it with::

    python -m zmart_drivers.leica.stellaris.navigator_expert.driver_setup.wizard

The parts, in the order a reader will want them:

- ``framework``: the small, microscope-independent core. A wizard is a list of
  steps, and each step offers named actions the page can call. Another driver
  can reuse it by supplying its own steps.
- ``steps``: the five Leica steps, written against the driver's own setup
  procedures.
- ``server``: the local web server that serves the page and carries the calls.
- ``launcher``: opens the page in a pywebview window, or in the browser.
- ``page/``: the page itself (plain HTML, CSS and JavaScript, nothing to build).
"""
