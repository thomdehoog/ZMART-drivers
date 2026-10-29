r"""The ZEISS driver, as the ZMART Controller plugs it in.

This is the class the controller makes when it connects, with one method per
command (see the controller's "How do I plug in a ZMART-driver"). Together
with ``zmart_controller_plugin.json`` beside it, which gives the driver's
name and connection, it is all the controller needs. Install the driver
once, then connect by name in every session; the control token is typed as
the connection's password::

    from zmart_controller import mic

    mic.register_driver(r"C:\ZMART-drivers\zmart_drivers\zeiss\light_microscope\zen_api_grpc")
    mic.connect("zeiss-zen", {"password": "<the gateway's control token>", "experiment": "ZMART_Snap"})
    mic.get_xyz()

The class translates. The driver itself is the ``Microscope`` in
``methods.py``, which can also be used without the controller; each method
here calls it and gives the answer the shape the controller takes. What only
the controller asks for lives beside this file: the description of
``get_info`` (``info.py``) and the menu of named procedures
(``procedure_menu.py``). ``acquire`` takes the picture and saves it.

Each method hands back ``True`` and the values when the microscope did what
was asked. When it did not, the ``Microscope`` raises: ``ValueError`` for a
request that is wrong (an unknown motor, setting or experiment) and
``RuntimeError`` for a change ZEN refused or that failed. The controller
reports a raised error with its text, so a workflow always sees
``{"success": False, "output": "..."}`` and never has to catch anything.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from typing import Any

from ..methods import Microscope, named_configurations
from ..procedures.state import set_state
from . import info, procedure_menu

#: The three axes, in the order the controller's canvas lists them.
AXES = ("x", "y", "z")


class ZmartControllerPlugin:
    """One connected ZEISS microscope, with the controller's commands."""

    def __init__(self, connection: dict[str, Any] | None = None) -> None:
        """Connect: open ZEN and load this microscope's configuration (see ``Microscope``)."""
        self.microscope = Microscope(connection)

    @staticmethod
    def configurations() -> list[str]:
        """The configs kept on this computer, the most recent first: what ``config`` may name."""
        return named_configurations()

    def disconnect(self) -> None:
        """Close the session; every later command is refused. A second call does nothing."""
        self.microscope.close()

    def get_info(self):
        """``True, (description, setup, chooses_its_own_folder, connection_status)``.

        ``chooses_its_own_folder`` is False: ZEN writes the CZI in its own
        folder, but the driver saves the capture as an OME-Zarr store in the
        ``output_folder`` ``acquire`` is given, and moves the CZI there too,
        so every file the answer lists is there.
        """
        answer = info.get_info(self.microscope.handle)
        return True, (
            answer["description"],
            answer["setup"],
            answer["chooses_its_own_folder"],
            answer["connection_status"],
        )

    def get_actuators(self):
        """``True, (x, y, z)``: per axis the motor and the default, ``{"options": [...], "default": ...}``."""
        motors = self.microscope.motors()
        return True, tuple(dict(motors[axis]) for axis in AXES)

    def get_xyz(self, with_actuators=None):
        """``True, (x, y, z, actuators, canvas)``, everything in micrometres from the saved origin."""
        return True, as_controller_xyz(self.microscope.position(with_actuators))

    def set_xyz(self, x, y, z, with_actuators=None):
        """Move the stage, then answer exactly as ``get_xyz`` does, read back once it has arrived."""
        return True, as_controller_xyz(self.microscope.move_to(x, y, z, with_actuators))

    def get_state(self):
        """``True, (changeable, observed)``: the experiment and the objective, and what can only be read."""
        state = self.microscope.state()
        return True, (state["changeable"], state["observed"])

    def set_state(self, changeable):
        """Apply the changeable settings, ``experiment`` and ``objective``, and answer ``True, applied``.

        Any other setting is one this microscope does not have, and is
        refused with ``ValueError``. The objective is changed so the sample
        stays in view.
        """
        return True, set_state(
            self.microscope.handle, {"changeable": dict(changeable or {})}
        )["applied"]

    def get_acquisition_settings(self):
        """``True, settings``: how a capture is triggered, ``full`` or ``snap``."""
        return True, self.microscope.acquisition_settings()

    def acquire(
        self, position_label, acquisition_settings, acquisition_type, output_folder
    ):
        """Capture with the loaded experiment, save, and answer ``True, (files, planes)``.

        The files are saved in ``output_folder``, or else in this session's
        folder under the data location, named after the ``acquisition_type``
        and the position label, never over an earlier capture.
        """
        taken = self.microscope.acquire(
            position_label,
            acquisition_type=acquisition_type,
            output_folder=output_folder,
            acquisition_settings=acquisition_settings,
        )
        (saved,) = self.microscope.save(taken["position_label"])
        return True, (saved["files"], saved["planes"])

    def get_procedures(self):
        """``True, procedures``: autofocus, the Definite Focus, stopping, saving captures."""
        return True, procedure_menu.get_procedures(self.microscope)

    def run_procedure(self, procedure):
        """Run the routine named by ``procedure["name"]`` and answer ``True, name``; the other keys are its arguments."""
        procedure_menu.run_procedure(self.microscope, dict(procedure))
        return True, procedure["name"]


def as_controller_xyz(answer: dict) -> tuple:
    """The per-axis answer as the controller takes it: ``(x, y, z, actuator_readings, canvas)``."""
    position = tuple(answer[axis]["position"] for axis in AXES)
    readings = {axis: dict(answer[axis]["actuator_readings"]) for axis in AXES}
    canvas = tuple(bound for axis in AXES for bound in answer[axis]["canvas"])
    return (*position, readings, canvas)
