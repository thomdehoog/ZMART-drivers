"""A setup wizard for the Leica STELLARIS 5, run once per microscope.

The wizard is the ZMART driver configuration workflow of the ZMART Microscopy
operator page, now living with the driver: Connect, Define limits, Define
coordinate system origin, Image-to-stage calibration and Objective
calibration. The steps are listed on the left and the chosen step's cells on
the right, as in the ZMART Interface without its top row. It talks to the
driver directly, so no other part of ZMART is needed.

Start it with::

    python -m zmart_drivers.leica.stellaris.navigator_expert.driver_setup.wizard

The parts, in the order a reader will want them:

- ``framework``: the small, microscope-independent core. A wizard is a list of
  steps, and each step offers named actions the page can call. Another driver
  can reuse it by supplying its own steps.
- ``steps``: the five Leica steps, written against the driver's own setup
  code, through one connection that Connect opens.
- ``server``: the local web server that serves the page and carries the calls.
- ``launcher``: opens the page in a pywebview window, or in the browser.
- ``page/``: the page itself (plain HTML, CSS and JavaScript, nothing to build).
"""
