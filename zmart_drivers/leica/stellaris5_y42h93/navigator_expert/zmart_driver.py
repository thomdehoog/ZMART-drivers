r"""The Leica STELLARIS 5 driver, as the ZMART Controller plugs it in.

This is the class the controller makes when it connects, with one method per
command (see the controller's "How do I plug in a ZMART-driver"). Together
with ``zmart_driver.json`` beside it, it is all the controller needs. Install
the driver once on the LAS X computer, then connect by name in every session::

    from zmart_controller import mic

    mic.register_driver(r"C:\ZMART-drivers\zmart_drivers\leica\stellaris5_y42h93\navigator_expert")
    mic.connect("stellaris")
    mic.get_xyz()

The methods only translate. The work of every command lives in the parts
below: the readings and changes in ``actions/``, the recipes in
``procedures/``, the files in ``output/``, the microscope's own settings in
``configuration/``. Today the methods reach those parts through the
functions in ``zmart_adapter/``, which still hold the coordinate arithmetic
and the connect flow; folding that into the parts is the next step of the
refactor, not this one.

Each method hands back ``True`` and the values when the microscope did what
was asked. When it did not, the function behind the method raises:
``ValueError`` for a request that is wrong (a position outside the limits,
an unknown motor, setting or job) and ``RuntimeError`` for a microscope that
failed. The controller reports a raised error with its text, so a workflow
always sees ``{"success": False, "content": "..."}`` and never has to catch
anything itself.

Positions are micrometres from the saved origin, in the sample's coordinate
system. The ``connection`` dictionary comes from ``zmart_driver.json``; every
key may be left out and then takes the driver's default (see
``zmart_adapter.CONNECTION``).

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from typing import Any

from .zmart_adapter import zmart_adapter as _adapter

#: The three axes, in the order the controller's canvas lists them.
_AXES = ("x", "y", "z")


class ZmartDriver:
    """One connected STELLARIS: the LAS X session and everything loaded for it."""

    def __init__(self, connection: dict[str, Any] | None = None) -> None:
        """Open LAS X and load this microscope's configuration.

        Making the driver is the connection: it opens the LAS X CAM API,
        loads the limits (which switch on the limits gate), the saved
        origin, the image-to-stage orientation and the objective
        calibration, and checks that LAS X answers. Raises when any of
        these fail, and leaves nothing open behind.
        """
        self._handle = _adapter.connect(dict(connection or {}))

    def disconnect(self) -> None:
        """Close the LAS X session. A second call does nothing."""
        _adapter.disconnect(self._handle)

    # --- describing the setup ---------------------------------------------------------

    def get_info(self):
        """``True, description``: the microscope in plain words.

        The text says what the other commands cannot: what each setting
        means, in which unit and within which bounds, which objective sits
        in which slot, and which way +z points. The bounds come from the
        limits loaded at connect.
        """
        return True, _adapter.get_info(self._handle)["description"]

    def get_actuators(self):
        """``True, (x_motors, y_motors, z_motors)``: the motor names per axis.

        x and y have one motor each, ``motoric``. z has two: ``z-wide``, the
        focus drive, and ``z-galvo``, the fast drive for small steps. The
        first name of each axis is the default when a move does not pick one.
        """
        motors = _adapter.get_actuators(self._handle)
        return True, tuple(list(motors[axis]) for axis in _AXES)

    # --- the stage ----------------------------------------------------------------------

    def get_xyz(self, with_actuators=None):
        """``True, (x, y, z, actuators, canvas)``, everything in micrometres.

        ``x``, ``y`` and ``z`` are measured from the saved origin; ``z`` is
        the focus, the two z drives added together. ``actuators`` gives
        every motor of each axis with its own raw reading, as LAS X reports
        it. ``canvas`` is everywhere a picture can show, in the same frame.
        ``with_actuators`` only checks that the motors it names exist.
        """
        answer = _adapter.get_xyz(self._handle, with_actuators=with_actuators)
        return True, _xyz(answer)

    def set_xyz(self, x, y, z, with_actuators=None):
        """Move the stage, then answer exactly as ``get_xyz`` does.

        The limits gate refuses a position outside the travel before
        anything moves (``ValueError``). ``with_actuators`` picks the z
        drive, for example ``{"z": "z-galvo"}`` for a small fast step. The
        answer is read back from LAS X once the stage has arrived, so it
        shows where the stage really is.
        """
        answer = _adapter.set_xyz(self._handle, x, y, z, with_actuators=with_actuators)
        return True, _xyz(answer)

    # --- the settings -------------------------------------------------------------------

    def get_state(self):
        """``True, (changeable, observed)``.

        ``changeable`` holds what ``set_state`` applies: today the selected
        LAS X job. ``observed`` describes the instrument and the session:
        serial number, objectives, pixel size, the jobs on offer, and which
        limits file governs.
        """
        state = _adapter.get_state(self._handle)
        return True, (state["changeable"], state["observed"])

    def set_state(self, changeable):
        """Apply the changeable settings and answer ``True, applied``.

        An unknown setting or job is refused with ``ValueError``.
        """
        return True, _adapter.set_state(self._handle, {"changeable": dict(changeable)})["applied"]

    # --- acquiring ----------------------------------------------------------------------

    def get_acquisition_settings(self):
        """``True, settings``: the choices for one picture, with the value in use."""
        return True, _adapter.get_acquisition_settings(self._handle)

    def acquire(self, position_label, acquisition_settings=None):
        """Capture here with the selected job, save, and answer ``True, (files, planes)``.

        The files are flat OME-TIFF, one plane per file, named after the
        position label and never overwriting an earlier picture. Every
        plane entry carries its file, its channel, depth and time index,
        and the stage position it was taken at, in the sample's frame.
        """
        answer = _adapter.acquire(
            self._handle, position_label=position_label, acquisition_settings=acquisition_settings
        )
        return True, (answer["files"], answer["planes"])

    # --- procedures ---------------------------------------------------------------------

    def get_procedures(self):
        """``True, procedures``: backlash takeup, parking the z-galvo, autofocus."""
        return True, _adapter.get_procedures(self._handle)

    def run_procedure(self, procedure):
        """Run the routine named by ``procedure["name"]``; the other keys are its arguments.

        Answers ``True`` and what the routine reports: the procedure that
        ran, and for autofocus the focus read back afterwards. A name that
        ``get_procedures`` does not list is refused with ``ValueError``.
        """
        return True, _adapter.run_procedure(self._handle, dict(procedure))


def _xyz(answer: dict) -> tuple:
    """Turn the per-axis answer of the parts into the tuple the controller takes.

    The parts answer ``{"x": {"position", "unit", "actuators", "canvas"}, ...}``.
    The controller wants ``(x, y, z, actuators, canvas)`` with ``actuators``
    keyed by axis and ``canvas`` as ``(x_min, x_max, y_min, y_max, z_min,
    z_max)``; it builds the same per-axis answer again for the workflow.
    """
    position = tuple(answer[axis]["position"] for axis in _AXES)
    actuators = {axis: dict(answer[axis]["actuators"]) for axis in _AXES}
    canvas = tuple(bound for axis in _AXES for bound in answer[axis]["canvas"])
    return (*position, actuators, canvas)
