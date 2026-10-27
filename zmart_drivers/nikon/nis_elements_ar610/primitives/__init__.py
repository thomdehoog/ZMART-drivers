"""The primitives: one folder for each way NIS-Elements is reached.

Both classes travel over the same nis-bridge connection (``dll/client.py``),
a socket to the small server a macro runs inside NIS-Elements. Inside NIS the
bridge answers a request one of two ways, and that is what tells the classes
apart: ``dll/`` holds the requests that ``g5_regprocs.dll`` exports (the
stage, the nosepiece, the Perfect Focus System, the optical configurations,
capturing); ``macro/`` holds the requests NIS 6.10 can only answer inside a
few lines of NIS macro (the camera and its exposure), which take a lot longer
and are asked at a slower pace.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""
