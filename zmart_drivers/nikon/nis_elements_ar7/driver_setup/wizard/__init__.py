"""A setup wizard for a Nikon microscope on NIS-Elements AR 7, run once per microscope.

The wizard walks through the one-time setup in a window, step by step:
Connect, Define limits, Define coordinate system origin, Image-to-stage
orientation and Hardware settings. The steps are listed on the left and the
chosen step's cells on the right, as in the ZMART Interface without its top
row, and as in the Leica driver's wizard. It talks to the driver directly, so
no other part of ZMART is needed.

Start it with::

    python -m zmart_drivers.nikon.nis_elements_ar7.driver_setup.wizard

The parts, in the order a reader will want them:

- ``framework``: the small, microscope-independent core, the same as the
  Leica driver's. A wizard is a list of steps, and each step offers named
  actions the page can call.
- ``steps``: the five Nikon steps, written against the driver's own setup
  code, through one connection that Connect opens.
- ``server``: the local web server that serves the page and carries the calls
  (the same as the Leica driver's).
- ``launcher``: opens the page in a pywebview window, or in the browser.
- ``page/``: the page itself (plain HTML, CSS and JavaScript, nothing to build).

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
Adapted from the Leica driver's setup wizard.
"""
