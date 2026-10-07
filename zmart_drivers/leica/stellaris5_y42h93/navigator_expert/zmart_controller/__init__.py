r"""The Leica STELLARIS 5 driver's plug-in for the ZMART Controller.

This folder is what the controller looks for. ``zmart.json`` beside this file
names the instrument, and the functions below are found by name. Plug the
driver in once on the LAS X computer, by its folder or by its module name::

    import zmart_controller

    zmart_controller.register_driver("zmart_drivers.leica.stellaris5_y42h93.navigator_expert")
    instrument = next(
        i for i in zmart_controller.get_instruments() if i["vendor"] == "leica"
    )
    zmart_controller.set_instrument(instrument)

The entry in ``zmart.json`` is handed to :func:`connect` as it is, so its
settings can be changed there, or in the dictionary from ``get_instruments()``
before it is passed to ``set_instrument``:

- ``client`` and ``api_delay_ms`` are passed to the LAS X CAM connection.
- ``output_root`` is the folder where images are saved. Left empty, the
  driver saves to a folder named ``ZMART-microscopy`` beside the folder that
  LAS X native AutoSave writes to.
- ``load_limits`` and ``load_calibration`` load this microscope's limits and
  objective calibration at connect. Leave both on for normal work.
- ``load_origin`` loads the saved zero point of the coordinate frame. Set it to
  false only to start in plain stage coordinates, for example to capture a new
  origin when the saved one is damaged.
- ``calibration_name`` (not set by default) picks a named calibration set.

The commands themselves live in the driver's adapter,
``zmart_adapter/zmart_adapter.py``. Called from there directly, each one
returns its content alone. The versions below, which the controller calls,
answer ``{"success": True, "content": ...}``. Every failure is raised.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.zmart_adapter import (
    zmart_adapter as _adapter,
)

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
