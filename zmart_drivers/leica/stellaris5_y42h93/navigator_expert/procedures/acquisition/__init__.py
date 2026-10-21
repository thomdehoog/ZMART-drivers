"""Taking a picture and keeping it, together with the story of how it was taken.

Acquiring and saving belong together, so the metadata of a picture is not a
separate step. It is gathered in three places, each doing one job:

- **What LAS X says about the picture** is read and translated into the
  standard fields in ``vendor_specific/primitives/capture/metadata_map.py``. Only LAS X knows how
  it describes a picture, so that part stays with the vendor code and moves no
  hardware.
- **The state of the microscope at the moment of the capture** (the job, the
  objective, where the stage was, the limits, and any change that could not be
  confirmed) is recorded by ``acquisition.py``. It has to be taken during the
  capture, because a later reading could describe a different state.
- **Writing it down** is saving: ``ome_canonical.py`` and ``ome_zarr.py`` put the
  standard OME metadata beside the pixels, and ``command_log.py`` writes the
  log of what the driver was asked and what it did. Saving also applies the
  measured image orientation, so the picture lines up with the stage moves.

To read the current state without taking a picture, use ``get_state`` and
``get_info``. They return plain readings and keep nothing.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""
