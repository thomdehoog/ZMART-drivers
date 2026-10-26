r"""The Leica STELLARIS driver, as the ZMART Controller plugs it in.

This is the class the controller makes when it connects, with one method per
command (see the controller's "How do I plug in a ZMART-driver"). Together
with ``zmart_controller_plugin.json`` beside it in this folder, which names this
file, it is all the controller needs. Install
the driver once on the LAS X computer, then connect by name in every session::

    from zmart_controller import mic

    mic.register_driver(r"C:\ZMART-drivers\zmart_drivers\leica\stellaris\navigator_expert")
    mic.connect("stellaris")
    mic.get_xyz()

The class translates. The driver itself is the ``Microscope`` in
``methods.py``, which can also be used without the
controller; each method here calls it and gives the answer the shape the
controller takes. What only the controller asks for lives beside this file:
the description of ``get_info`` (``info.py``) and the menu of named
procedures (``procedure_menu.py``). ``acquire`` takes the picture and saves it, and never
clears LAS X's cache: that is the ``clear_cache`` procedure.

Each method hands back ``True`` and the values when the microscope did what
was asked. When it did not, the ``Microscope`` raises: ``ValueError`` for a
request that is wrong (an unknown motor, setting or job) and
``RuntimeError`` for a change that was refused or failed. The controller
reports a raised error with its text, so a workflow always sees
``{"success": False, "output": "..."}`` and never has to catch anything
itself.

Positions are micrometres from the saved origin, in the sample's coordinate
system. The ``connection`` dictionary comes from ``zmart_controller_plugin.json``; every
key may be left out and then takes the driver's default
(``methods.DEFAULT_CONNECTION``).

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from typing import Any

from ..methods import Microscope, named_configurations
from . import info
from . import procedure_menu

#: The three axes, in the order the controller's canvas lists them.
AXES = ("x", "y", "z")

#: What set_state can change: the selected LAS X job, and nothing else.
CHANGEABLE = frozenset({"job"})


class ZmartControllerPlugin:
    """One connected STELLARIS, with the controller's commands."""

    def __init__(self, connection: dict[str, Any] | None = None) -> None:
        """Connect: open LAS X and load this microscope's configuration (see ``Microscope``)."""
        self.microscope = Microscope(connection)

    @staticmethod
    def configurations() -> list[str]:
        """The configs kept on this computer, the most recent first: what ``config`` may name.

        Asked before connecting, so a client can offer them to choose from.
        New configs are made in the setup wizard.
        """
        return named_configurations()

    def disconnect(self) -> None:
        """Close the session; every later command is refused. A second call does nothing."""
        self.microscope.close()

    # --- describing the setup ---------------------------------------------------------

    def get_info(self):
        """``True, (description, setup, chooses_its_own_folder, connection_status)``.

        The description says what the other commands cannot: what each
        setting means, in which unit and within which bounds, which
        objective sits in which slot, and which way +z points. ``setup`` is
        each thing set up once on this microscope, done or not, in words.
        ``chooses_its_own_folder`` is False: the driver writes every file
        itself, from LAS X's cache, so it saves where ``acquire`` is told.
        ``connection_status`` is what the session stands on, each check in
        words, for the operator's Connect step.
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
        """``True, (changeable, observed)``: the selected job, and what can only be read."""
        state = self.microscope.state()
        return True, (state["changeable"], state["observed"])

    def set_state(self, changeable):
        """Apply the changeable settings and answer ``True, applied``.

        The one changeable setting is ``job``, the selected LAS X job. Any other
        is a setting this microscope does not have, and is refused with
        ``ValueError``; the job's own settings, such as the zoom, are changed
        with the ``Microscope``'s ``change_setting``.
        """
        unknown = sorted(set(changeable) - CHANGEABLE)
        if unknown:
            raise ValueError(f"no changeable setting {unknown}; the changeable settings are {sorted(CHANGEABLE)}")
        job = dict(changeable).get("job")
        if job is None:
            return True, {}
        return True, self.microscope.select_job(job)["applied"]

    # --- acquiring ----------------------------------------------------------------------

    def get_acquisition_settings(self):
        """``True, settings``: how a capture is triggered, ``full`` and nothing else."""
        return True, self.microscope.acquisition_settings()

    def acquire(self, position_label, acquisition_settings, acquisition_type, output_folder):
        """Capture here with the selected job, save, and answer ``True, (files, planes)``.

        The files are saved in ``output_folder``, or else in this session's
        folder under the data location, named after the ``acquisition_type``
        and the position label, never over an earlier picture.
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
        """``True, procedures``: parking the z-galvo, saving captures, clearing LAS X's cache."""
        return True, procedure_menu.get_procedures(self.microscope.handle)

    def run_procedure(self, procedure):
        """Run the routine named by ``procedure["name"]`` and answer ``True, name``; the other keys are its arguments.

        A routine that could not do all of its work, such as a picture LAS X
        would not let go of, raises ``RuntimeError`` naming what was left, so
        the workflow sees a failure.
        """
        done = procedure_menu.run_procedure(self.microscope.handle, dict(procedure))
        if done.get("failed"):
            raise RuntimeError(f"{procedure['name']} left {done['failed']}")
        return True, procedure["name"]


def as_controller_xyz(answer: dict) -> tuple:
    """The per-axis answer as the controller takes it: ``(x, y, z, actuator_readings, canvas)``.

    ``actuator_readings`` is keyed by axis, and ``canvas`` is ``(x_min, x_max, y_min,
    y_max, z_min, z_max)``; the controller builds the per-axis answer again for
    the workflow.
    """
    position = tuple(answer[axis]["position"] for axis in AXES)
    readings = {axis: dict(answer[axis]["actuator_readings"]) for axis in AXES}
    canvas = tuple(bound for axis in AXES for bound in answer[axis]["canvas"])
    return (*position, readings, canvas)
