"""How each command is sent and confirmed, for this microscope.

The profile of a command says which checks run before and after it, how often
it may be sent again, how long to wait, and how the request travels
(``transport.py``). The dispatcher runs the loops; these files say how.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""
