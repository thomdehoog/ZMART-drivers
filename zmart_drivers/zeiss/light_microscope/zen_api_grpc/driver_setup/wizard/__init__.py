"""A setup wizard for a ZEISS microscope driven by ZEN 3.13, run once per microscope.

The wizard walks through the driver's one-time setup in a window: Connect,
Define limits, Define coordinate system origin, Image-to-stage calibration
and Objective calibration. The steps are listed on the left and the chosen
step's cells on the right, as in the ZMART Interface without its top row. It
talks to the driver directly, so no other part of ZMART is needed.

Start it with::

    python -m zmart_drivers.zeiss.light_microscope.zen_api_grpc.driver_setup.wizard

The parts, in the order a reader will want them:

- ``framework``: the small, microscope-independent core, the same as in the
  Leica driver. A wizard is a list of steps, and each step offers named
  actions the page can call.
- ``steps``: the five ZEISS steps, written against the driver's own setup
  code, through one connection that Connect opens.
- ``picture``: draws the three orientation snaps side by side as a PNG file,
  so the operator can see what the measurement looked at.
- ``server``: the local web server that serves the page and carries the calls.
- ``launcher``: opens the page in a pywebview window, or in the browser.
- ``page/``: the page itself (plain HTML, CSS and JavaScript, nothing to build).
"""
