"""The limits item: how far the stage may travel, and which objectives and optical configurations may be chosen.

``config.py`` reads and saves ``limits.json``, ``limit_rules.py`` answers the
limits gate, ``handshake.py`` installs the limits at connect, and
``measure_limits.py`` records the corners the operator drives the stage to,
for the ``set_limits`` notebook.

The bundled default (``defaults/limits.json``) is deliberately wide: it is
always cut to the travel NIS itself reports, and while it is in use z may
move only a little from where it stood at connect.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""
