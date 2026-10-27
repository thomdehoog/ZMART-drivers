"""The ``dll`` class: the requests the bridge carries out through ``g5_regprocs.dll``.

NIS-Elements ships a C library that exports most of its macro functions
(``StgGetPos``, ``StgMove``, ``Capture``, ``ImageSaveAs`` and so on). The
bridge calls those directly, in well under a millisecond each, so these are
the readings the driver asks often and the changes it sends. The connection to
the bridge itself (``client.py``) lives here too, and the ``macro`` class
shares it.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""
