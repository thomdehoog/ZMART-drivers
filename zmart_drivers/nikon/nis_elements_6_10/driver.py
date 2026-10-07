"""The Nikon NIS-Elements driver, as the ZMART Controller plugs it in.

This module is the driver: it holds one function per command, under the
names the controller looks for. Hand it to the controller to drive the
microscope::

    import zmart_controller
    import zmart_drivers.nikon.nis_elements_6_10.driver as nikon

    zmart_controller.set_instrument(nikon)

The optional connection dictionary is handed to ``connect`` as it is. Every
key may be left out, and then takes its value from ``CONNECTION`` in the
adapter: ``microscope`` names this instrument, and ``host`` and ``port`` are
where the bridge inside NIS-Elements listens. Three more keys may be given:
``output_root``, the folder where images are saved (a temporary folder when
left out); ``timeout``, how many seconds to wait for an answer from the
bridge; and ``machine_root``, a different folder for this microscope's saved
configuration. For example::

    zmart_controller.set_instrument(nikon, {"output_root": r"D:\\images"})

The commands themselves live in the driver's adapter,
``nis_zmart_adapter.py``. Called from there directly, each one returns its
content alone. The versions below, which the controller calls, answer
``{"success": ..., "content": ...}``.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from . import nis_zmart_adapter as _adapter

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
