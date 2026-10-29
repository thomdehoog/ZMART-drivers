"""Start the setup wizard: ``python -m zmart_drivers.nikon.nis_elements_ar610.driver_setup.wizard``.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

import sys

from .launcher import main

sys.exit(main())
