r"""The Leica STELLARIS 5 driver, as the ZMART Controller plugs it in.

This is the class the controller makes when it connects, with one method per
command (see the controller's "How do I plug in a ZMART-driver"). Together
with ``zmart_driver.json`` beside it in this folder, which names this
file, it is all the controller needs. Install
the driver once on the LAS X computer, then connect by name in every session::

    from zmart_controller import mic

    mic.register_driver(r"C:\ZMART-drivers\zmart_drivers\leica\stellaris5_y42h93\navigator_expert\for_zmart_controller")
    mic.connect("stellaris")
    mic.get_xyz()

The class only translates. The driver itself is the ``Microscope`` in
``for_direct_use/microscope.py``, which can also be used without the
controller; each method here calls it and gives the answer the shape the
controller takes. ``acquire`` takes the picture and saves it, and never
clears LAS X's cache: that is the ``clear_cache`` procedure.

Each method hands back ``True`` and the values when the microscope did what
was asked. When it did not, the ``Microscope`` raises: ``ValueError`` for a
request that is wrong (an unknown motor, setting or job) and
``RuntimeError`` for a change that was refused or failed. The controller
reports a raised error with its text, so a workflow always sees
``{"success": False, "content": "..."}`` and never has to catch anything
itself.

Positions are micrometres from the saved origin, in the sample's coordinate
system. The ``connection`` dictionary comes from ``zmart_driver.json``; every
key may be left out and then takes the driver's default
(``for_direct_use/microscope.DEFAULT_CONNECTION``).

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from typing import Any

from ..for_direct_use.microscope import Microscope

#: The three axes, in the order the controller's canvas lists them.
AXES = ("x", "y", "z")


class ZmartDriver:
    """One connected STELLARIS, with the controller's commands."""

    def __init__(self, connection: dict[str, Any] | None = None) -> None:
        """Connect: open LAS X and load this microscope's configuration (see ``Microscope``)."""
        self.microscope = Microscope(connection)

    def disconnect(self) -> None:
        """Close the session; every later command is refused. A second call does nothing."""
        self.microscope.close()

    # --- describing the setup ---------------------------------------------------------

    def get_info(self):
        """``True, (description, setup, chooses_its_own_folder)``.

        The description says what the other commands cannot: what each
        setting means, in which unit and within which bounds, which
        objective sits in which slot, and which way +z points. ``setup`` is
        each thing set up once on this microscope, done or not, in words.
        ``chooses_its_own_folder`` is False: the driver writes every file
        itself, from LAS X's cache, so it saves where ``acquire`` is told.
        """
        info = self.microscope.info()
        return True, (info["description"], info["setup"], info["chooses_its_own_folder"])

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
        """Apply the changeable settings (today the job) and answer ``True, applied``."""
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
        """``True, procedures``: parking the z-galvo, saving captures, clearing LAS X's cache, autofocus."""
        return True, self.microscope.procedures()

    def run_procedure(self, procedure):
        """Run the routine named by ``procedure["name"]``; the other keys are its arguments."""
        arguments = dict(procedure)
        return True, self.microscope.run_procedure(arguments.pop("name", None), **arguments)


def as_controller_xyz(answer: dict) -> tuple:
    """The per-axis answer as the controller takes it: ``(x, y, z, actuators, canvas)``.

    ``actuators`` is keyed by axis, and ``canvas`` is ``(x_min, x_max, y_min,
    y_max, z_min, z_max)``; the controller builds the per-axis answer again for
    the workflow.
    """
    position = tuple(answer[axis]["position"] for axis in AXES)
    actuators = {axis: dict(answer[axis]["actuators"]) for axis in AXES}
    canvas = tuple(bound for axis in AXES for bound in answer[axis]["canvas"])
    return (*position, actuators, canvas)
