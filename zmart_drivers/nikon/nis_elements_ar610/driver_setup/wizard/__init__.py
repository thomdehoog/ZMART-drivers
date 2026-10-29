"""A setup wizard for a Nikon microscope with NIS-Elements AR 6.10, run once per microscope.

The wizard walks through the five things the driver needs to know about this
microscope, one step after another: Connect, Define limits, Define
coordinate system origin, Image-to-stage calibration and Objective
calibration. The steps are listed on the left and the chosen step's cells on
the right, as in the ZMART Interface without its top row. It talks to the
driver directly, so no other part of ZMART is needed.

Start it on the NIS-Elements computer, with the nis-bridge running inside
NIS-Elements::

    python -m zmart_drivers.nikon.nis_elements_ar610.driver_setup.wizard

The parts, in the order a reader will want them:

- ``framework``: the small core that knows nothing about any microscope. A
  wizard is a list of steps, and each step offers named actions the page can
  call. It is the same code as in the Leica driver's wizard.
- ``steps``: the five Nikon steps, written against the driver's own setup
  code, through the one connection that Connect opens.
- ``server``: the local web server that serves the page and carries the calls.
- ``launcher``: opens the page in a pywebview window, or in the browser.
- ``page/``: the page itself (plain HTML, CSS and JavaScript, nothing to build).

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""
