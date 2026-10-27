r"""The Nikon NIS-Elements AR 7 driver, as the ZMART Controller plugs it in.

This is the class the controller makes when it connects, with one method per
command (see the controller's "How do I plug in a ZMART-driver"). Together
with ``zmart_controller_plugin.json`` beside it in this folder, it is all the
controller needs. Install the driver once on the NIS-Elements computer, start
``start_bridge.mac`` in NIS, then connect by name in every session::

    from zmart_controller import mic

    mic.register_driver(r"C:\ZMART-drivers\zmart_drivers\nikon\nis_elements_ar7")
    mic.connect("nikon-nis-elements-ar7")
    mic.get_xyz()

The class translates. The driver itself is the ``Microscope`` in
``methods.py``, which can also be used without the controller; each method
here calls it and gives the answer the shape the controller takes. What only
the controller asks for lives beside this file: the description of
``get_info`` (``info.py``) and the menu of named procedures
(``procedure_menu.py``).

Each method hands back ``True`` and the values when the microscope did what
was asked. When it did not, the ``Microscope`` raises: ``ValueError`` for a
request that is wrong (an unknown objective position, optical configuration
or setting) and ``RuntimeError`` for a change that was refused or failed. The
controller reports a raised error with its text, so a workflow always sees
``{"success": False, "output": "..."}`` and never has to catch anything
itself.

Positions are micrometres from the saved origin. The ``connection``
dictionary comes from ``zmart_controller_plugin.json``; every key may be left
out, and then takes the driver's default (``methods.DEFAULT_CONNECTION``).

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
Adapted from the Leica driver's ``zmart_controller_plugin.py``.
"""

from __future__ import annotations

from typing import Any

from ..methods import Microscope, named_configurations
from ..procedures.state import CHANGEABLE
from . import info, procedure_menu

#: The three axes, in the order the controller's canvas lists them.
AXES = ("x", "y", "z")


class ZmartControllerPlugin:
    """One connected Nikon microscope, with the controller's commands."""

    def __init__(self, connection: dict[str, Any] | None = None) -> None:
        """Connect: reach NIS through the bridge and load this microscope's settings (see ``Microscope``)."""
        self.microscope = Microscope(connection)

    @staticmethod
    def configurations() -> list[str]:
        """The configs kept on this computer, the most recent first: what ``config`` may name.

        This is asked before connecting, so that a client can offer them to
        choose from.
        """
        return named_configurations()

    def disconnect(self) -> None:
        """Close the session; every later command is refused. The bridge keeps running in NIS."""
        self.microscope.close()

    # --- describing the setup ---------------------------------------------------------

    def get_info(self):
        """``True, (description, setup, chooses_its_own_folder, connection_status)``.

        The description says what the other commands cannot: what each
        setting means, which objective sits at which nosepiece position, and
        what the limits allow. ``setup`` is each thing set up once on this
        microscope, done or not, in words. ``chooses_its_own_folder`` is
        False: the driver saves every picture itself, where ``acquire`` is
        told. ``connection_status`` is what the session stands on, each check
        in words, for the operator's Connect step.
        """
        answer = info.get_info(self.microscope.handle)
        return True, (
            answer["description"],
            answer["setup"],
            answer["chooses_its_own_folder"],
            answer["connection_status"],
        )

    def get_actuators(self):
        """``True, (x, y, z)``: per axis the motors and the default, ``{"options": [...], "default": ...}``."""
        motors = self.microscope.motors()
        return True, tuple(dict(motors[axis]) for axis in AXES)

    # --- the stage ----------------------------------------------------------------------

    def get_xyz(self, with_actuators=None):
        """``True, (x, y, z, actuators, canvas)``, everything in micrometres from the saved origin."""
        return True, as_controller_xyz(self.microscope.position(with_actuators))

    def set_xyz(self, x, y, z, with_actuators=None):
        """Move the stage and the focus, then answer exactly as ``get_xyz`` does, read back after the move."""
        return True, as_controller_xyz(self.microscope.move_to(x, y, z, with_actuators))

    # --- the settings -------------------------------------------------------------------

    def get_state(self):
        """``True, (changeable, observed)``: the objective and optical configuration, and what can only be read.

        ``observed`` holds ``image_size_px`` and ``pixel_size_um`` once a
        picture has been taken with the current objective and optical
        configuration (or an image is open in NIS); before that they are None,
        because NIS can only say the size of a picture it has.
        """
        state = self.microscope.state()
        return True, (state["changeable"], state["observed"])

    def set_state(self, changeable):
        """Apply the changeable settings and answer ``True, applied``.

        The changeable settings are ``objective_position`` (counted from 1)
        and ``optical_configuration`` (a name NIS knows). Any other is a
        setting this microscope does not have, and is refused with
        ``ValueError`` before anything changes.
        """
        changeable = dict(changeable)
        unknown = sorted(set(changeable) - set(CHANGEABLE))
        if unknown:
            raise ValueError(
                f"no changeable setting {unknown}; the changeable settings are {list(CHANGEABLE)}"
            )
        asked = {name: value for name, value in changeable.items() if value is not None}
        if not asked:
            return True, {}
        return True, self.microscope.set_state(asked)["applied"]

    # --- acquiring ----------------------------------------------------------------------

    def get_acquisition_settings(self):
        """``True, settings``: how a capture is triggered, ``snap`` and nothing else."""
        return True, self.microscope.acquisition_settings()

    def acquire(
        self, position_label, acquisition_settings, acquisition_type, output_folder
    ):
        """Take one picture here, save it, and answer ``True, (files, planes)``.

        The files are saved in ``output_folder``, or else in this microscope's
        data location, named after the ``acquisition_type`` and the position
        label, never over an earlier picture.
        """
        saved = self.microscope.acquire(
            position_label,
            acquisition_type=acquisition_type,
            output_folder=output_folder,
            acquisition_settings=acquisition_settings,
        )
        return True, (saved["files"], saved["planes"])

    # --- procedures ---------------------------------------------------------------------

    def get_procedures(self):
        """``True, procedures``: saving pictures whose saving failed earlier."""
        return True, procedure_menu.get_procedures(self.microscope.handle)

    def run_procedure(self, procedure):
        """Run the procedure named by ``procedure["name"]`` and answer ``True, name``; the other keys are its arguments.

        A procedure that could not do all of its work, such as a picture that
        still could not be saved, raises ``RuntimeError`` naming what was
        left, so that the workflow sees a failure.
        """
        done = procedure_menu.run_procedure(self.microscope.handle, dict(procedure))
        if done.get("failed"):
            raise RuntimeError(f"{procedure['name']} left {done['failed']}")
        return True, procedure["name"]


def as_controller_xyz(answer: dict) -> tuple:
    """The per-axis answer as the controller takes it: ``(x, y, z, actuator_readings, canvas)``.

    ``actuator_readings`` is keyed by axis, and ``canvas`` is ``(x_min, x_max,
    y_min, y_max, z_min, z_max)``; the controller builds the per-axis answer
    again for the workflow.
    """
    position = tuple(answer[axis]["position"] for axis in AXES)
    readings = {axis: dict(answer[axis]["actuator_readings"]) for axis in AXES}
    canvas = tuple(bound for axis in AXES for bound in answer[axis]["canvas"])
    return (*position, readings, canvas)
