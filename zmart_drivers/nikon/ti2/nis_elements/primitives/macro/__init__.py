"""The ``macro`` class: the requests the bridge can only answer inside a NIS macro.

``g5_regprocs.dll`` does not export most of NIS-Elements' camera functions,
and on NIS 6.10 a function that hands a value back cannot be called by name
from NIS's Python without breaking it. So the bridge writes a few lines of NIS
macro, runs them with ``RunMacro``, and reads the answer back from macro
variables and a file. That takes about 24 ms per function, against a few
microseconds through the DLL, and the camera report asks five of them.

This class holds what goes that way: the camera and its exposure
(``reads.py``), and setting the exposure (``writes.py``). Its profile says
how slowly it is asked again. The requests travel over the same bridge
connection as the ``dll`` class's (``dll/client.py``).

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""
