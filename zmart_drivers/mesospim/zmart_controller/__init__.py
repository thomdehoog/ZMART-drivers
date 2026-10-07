"""The mesoSPIM driver's plug-in for the ZMART Controller.

This folder is what the controller looks for. ``zmart.json`` beside this file
names the instrument, and the functions below are found by name. Plug the
driver in once on the microscope computer, by its folder or by its module
name::

    import zmart_controller

    zmart_controller.register_driver("zmart_drivers.mesospim")

The entry in ``zmart.json`` is handed to ``connect`` as it is. Change it
there, or in the dictionary from ``get_instruments()`` before it is passed to
``set_instrument``: ``microscope`` names this instrument, and ``host`` and
``port`` are where mesoSPIM-control's Remote Scripting server listens. More
keys may be added: ``output_root``, the folder where images are saved (a
temporary folder when left out); ``timeout``, how many seconds to wait for an
answer; ``stage_limits``, the path of a stage-limits file to use instead of
this microscope's own; and ``machine_root``, a different folder for this
microscope's saved configuration.

The commands themselves live in the driver's adapter. Called from there
directly, each one returns its content alone. The versions below, which the
controller calls, answer ``{"success": ..., "content": ...}``.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from zmart_drivers.mesospim import mesospim_zmart_adapter as _adapter

_OPS = _adapter.ops_table()

connect = _OPS["connect"]
disconnect = _OPS["disconnect"]
get_info = _OPS["get_info"]
get_actuators = _OPS["get_actuators"]
get_xyz = _OPS["get_xyz"]
set_xyz = _OPS["set_xyz"]
get_state = _OPS["get_state"]
set_state = _OPS["set_state"]
get_acquisition_settings = _OPS["get_acquisition_settings"]
acquire = _OPS["acquire"]
get_procedures = _OPS["get_procedures"]
run_procedure = _OPS["run_procedure"]
