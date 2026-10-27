r"""The Nikon NIS-Elements driver, as the ZMART Controller plugs it in.

This is the class the controller makes when it connects, with one method per
command (see the controller's "How do I plug in a ZMART-driver"). Together
with ``zmart_controller_plugin.json`` beside it in this folder, it is all the
controller needs. Install the driver once on the NIS-Elements computer, start
the bridge, then connect by name in every session::

    from zmart_controller import mic

    mic.register_driver(r"C:\ZMART-drivers\zmart_drivers\nikon\nis_elements_ar610")
    mic.connect("nikon-nis-elements-ar610")
    mic.get_xyz()

The class translates. The driver itself is the ``Microscope`` in
``methods.py``, which can also be used without the controller; each method
here calls it and gives the answer the shape the controller takes. What only
the controller asks for lives beside this file: the description of
``get_info`` (``info.py``) and the menu of named procedures
(``procedure_menu.py``). ``acquire`` takes the picture and saves it.

Each method hands back ``True`` and the values when the microscope did what
was asked. When it did not, the ``Microscope`` raises: ``ValueError`` for a
request that is wrong (an unknown setting or configuration) and
``RuntimeError`` for a change that was refused or failed. The controller
reports a raised error with its text, so a workflow always sees
``{"success": False, "output": "..."}`` and never has to catch anything.

Positions are micrometres from the saved origin, in the sample's coordinate
system. The ``connection`` dictionary comes from ``zmart_controller_plugin.json``;
every key may be left out and then takes the driver's default
(``methods.DEFAULT_CONNECTION``).

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from typing import Any

from ..methods import Microscope, named_configurations
from ..procedures.state import CHANGEABLE
from . import info, procedure_menu

#: The three axes, in the order the controller's canvas lists them.
AXES = ("x", "y", "z")


class ZmartControllerPlugin:
    """One connected Nikon, with the controller's commands."""

    def __init__(self, connection: dict[str, Any] | None = None) -> None:
        """Connect: reach NIS-Elements through the bridge and load this microscope's settings (see ``Microscope``)."""
        self.microscope = Microscope(connection)

    @staticmethod
    def configurations() -> list[str]:
        """The configs kept on this computer, the most recent first: what ``config`` may name."""
        return named_configurations()

    def disconnect(self) -> None:
        """Close the session; every later command is refused. A second call does nothing."""
        self.microscope.close()

    # --- describing the setup ---------------------------------------------------------

    def get_info(self):
        """``True, (description, setup, chooses_its_own_folder, connection_status)``.

        The description says what the other commands cannot: what each
        setting means, in which unit, and what the microscope has. ``setup``
        is each thing set up once on this microscope, done or not, in words.
        ``chooses_its_own_folder`` is False: the driver writes every store
        itself, so it saves where ``acquire`` is told. ``connection_status``
        is what the session stands on, each check in words.
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
        """Move the stage, then answer exactly as ``get_xyz`` does, read back once it has arrived."""
        return True, as_controller_xyz(self.microscope.move_to(x, y, z, with_actuators))

    # --- the settings -------------------------------------------------------------------

    def get_state(self):
        """``True, (changeable, observed)``: the objective, channel, exposure and PFS, and what can only be read."""
        state = self.microscope.state()
        return True, (state["changeable"], state["observed"])

    def set_state(self, changeable):
        """Apply the changeable settings and answer ``True, applied``.

        The changeable settings are ``objective`` (a nosepiece slot),
        ``channel`` (an optical configuration's name), ``exposure_ms`` and
        ``pfs`` (True or False). Any other is a setting this microscope does
        not have, and is refused with ``ValueError``.
        """
        unknown = sorted(set(changeable) - set(CHANGEABLE))
        if unknown:
            raise ValueError(
                f"no changeable setting {unknown}; the changeable settings are {list(CHANGEABLE)}"
            )
        return True, self.microscope.set_state(dict(changeable))["applied"]

    # --- acquiring ----------------------------------------------------------------------

    def get_acquisition_settings(self):
        """``True, settings``: how a capture is triggered, ``snap`` and nothing else."""
        return True, self.microscope.acquisition_settings()

    def acquire(
        self, position_label, acquisition_settings, acquisition_type, output_folder
    ):
        """Capture here with the settings as they are, save, and answer ``True, (files, planes)``.

        The store is saved in ``output_folder``, or else under this
        microscope's data location, named after the ``acquisition_type`` and
        the position label, never over an earlier picture.
        """
        taken = self.microscope.acquire(
            position_label,
            acquisition_type=acquisition_type,
            output_folder=output_folder,
            acquisition_settings=acquisition_settings,
        )
        (saved,) = self.microscope.save(taken["position_label"])
        return True, (saved["files"], saved["planes"])

    # --- procedures ---------------------------------------------------------------------

    def get_procedures(self):
        """``True, procedures``: the focus sweep, the PFS, saving captures, saving the origin."""
        return True, procedure_menu.get_procedures(self.microscope.handle)

    def run_procedure(self, procedure):
        """Run the routine named by ``procedure["name"]`` and answer ``True, name``; the other keys are its arguments."""
        procedure_menu.run_procedure(self.microscope.handle, dict(procedure))
        return True, procedure["name"]


def as_controller_xyz(answer: dict) -> tuple:
    """The per-axis answer as the controller takes it: ``(x, y, z, actuator_readings, canvas)``."""
    position = tuple(answer[axis]["position"] for axis in AXES)
    readings = {axis: dict(answer[axis]["actuator_readings"]) for axis in AXES}
    canvas = tuple(bound for axis in AXES for bound in answer[axis]["canvas"])
    return (*position, readings, canvas)
