"""The absolute coordinate system: one place for the stage's position and the lens's offsets.

The vendor part of the driver only reports plain readings from the microscope:
the stage position, each actuator (z-wide and z-galvo), and the selected
objective. This folder turns those readings into the driver's one absolute
coordinate system, and turns a requested position in that system back into
moves. It takes into account the origin the operator set, the actuators that
share the focus, and the measured offsets between objectives (how far the
centre of the view shifts, and how far the focus shifts, when the lens is
changed). When the lens or job changes, it moves the stage so the sample stays
in view.

The images are placed in the same system when they are saved: the orientation
measured by the setup (``driver_setup/orientation``) is applied in
``save_data`` so that a picture lines up with the stage moves.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""
