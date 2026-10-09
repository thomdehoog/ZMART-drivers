r"""The Leica STELLARIS 5 driver in the older module shape, kept until the class has run on hardware.

The controller now plugs a driver in through ``zmart_driver.json`` and the
``ZmartDriver`` class in ``zmart_driver.py``, both at the top of this folder;
that is the shape to install and use. This module is the shape the
controller accepted before: one function per command under the names the
controller looks for, handed over directly::

    import zmart_controller
    import zmart_drivers.leica.stellaris5_y42h93.navigator_expert.zmart_controller_plugin as stellaris

    mic = zmart_controller.ZmartController(stellaris, {"output_root": r"D:\images"})

It stays until ``zmart_driver.py`` has driven the real STELLARIS, so that the
two can be compared on the microscope; then it goes.

The optional connection dictionary is handed to :func:`connect` as it is.
Every key may be left out, and then takes its value from ``CONNECTION`` in
the adapter:

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

from .zmart_adapter import zmart_adapter as _adapter

# The driver's name in the controller's list of drivers, and its configuration
# on this computer: the adapter's defaults, which you may edit here.
NAME = "stellaris"
CONNECTION = dict(_adapter.CONNECTION)

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
