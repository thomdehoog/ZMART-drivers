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

The methods only translate. The work of every command lives in the parts
below: the readings and changes in ``vendor_interface/`` and ``dispatcher/``,
the recipes (including saving the files) in ``procedures/``, the microscope's
own settings in ``configuration/``. This class opens the connection, hands
each call to the procedure that does the work, and turns the answer into the
shape the controller takes.

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
``DEFAULT_CONNECTION`` below).

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
from typing import Any

from ..configuration.origin import saved_origin as _saved_origin
from ..configuration.origin import set_origin as _set_origin
from ..configuration import data_location as _data_location
from .. import running_log
from ..configuration.image_stage_registration import load_rig_orientation
from ..configuration.optical_calibration.model import load_objective_calibration
from ..procedures import session_state as _session_state
from ..configuration.store import MACHINE
from ..dispatcher import gate as _gate
from ..vendor_interface.naming import run_hash
from ..procedures.acquisition_and_saving import acquisition as _acquisition
from ..procedures import frame as _frame
from ..procedures import instrument as _instrument
from ..procedures.session import ZmartHandle
from ..vendor_interface.connection import lasx_client
from ..vendor_interface.primitives.interface import lasx_dialogs

log = logging.getLogger(__name__)

#: The three axes, in the order the controller's canvas lists them.
_AXES = ("x", "y", "z")

#: The connection settings used for any key the caller leaves out.
#: ``microscope`` names this instrument. ``client`` and ``api_delay_ms`` feed
#: the LAS X connection. ``output_root`` is where ``acquire`` saves when it is
#: not told a folder. ``load_limits`` and ``load_calibration`` load this
#: microscope's limits and objective calibration at connect; leave both on for
#: normal work. ``load_origin`` loads the saved zero point of the coordinate
#: frame; set it to False only to start in plain stage coordinates, for
#: example to capture a new origin when the saved one is damaged.
#: ``calibration_name`` (not set by default) picks a named calibration set.
DEFAULT_CONNECTION = {
    "microscope": "stellaris5-y42h93",
    "client": "PythonClient",
    "api_delay_ms": None,
    "output_root": None,
    "load_limits": True,
    "load_calibration": True,
    "load_origin": True,
}


class ZmartDriver:
    """One connected STELLARIS: the LAS X session and everything loaded for it."""

    #: The connection settings a caller may leave out; see ``DEFAULT_CONNECTION``.
    DEFAULT_CONNECTION = DEFAULT_CONNECTION

    def __init__(self, connection: dict[str, Any] | None = None) -> None:
        """Open LAS X and load this microscope's configuration.

        Making the driver is the connection: it opens the LAS X CAM API,
        loads the limits (which switch on the limits gate), the saved
        origin, the image-to-stage orientation and the objective
        calibration, and checks that LAS X answers. Raises when any of
        these fail, and leaves nothing open behind.

        The saved origin is read before LAS X is contacted. A saved origin
        that exists but cannot be read stops the connection, because
        quietly falling back to stage coordinates would put every later
        move somewhere other than intended.
        """
        connection = {**DEFAULT_CONNECTION, **(connection or {})}
        saved_origin = (
            _saved_origin.load_saved_origin()
            if connection.get("load_origin", True)
            else None
        )
        saved_location = _load_data_location()
        MACHINE.ensure_layout()
        # Kept beside the saved settings so that it survives a restart of the driver.
        running_log.start(MACHINE.snapshot_root() / "logs", __package__)
        client = lasx_client.connect_python_client(
            client_name=connection.get("client", "PythonClient"),
            api_delay_ms=connection.get("api_delay_ms"),
        )
        # The limits are loaded first, so nothing can move before they govern.
        _gate.connect_handshake(client, load=connection.get("load_limits", True))
        lasx_dialogs.keep_watching()
        orientation, orientation_info = load_rig_orientation()
        translations, calibration_info = load_objective_calibration(
            connection.get("calibration_name"),
            enabled=connection.get("load_calibration", True),
        )
        _session_state.install(
            client,
            _session_state.SessionConfig(
                orientation=orientation,
                translations=translations,
                calibration_name=calibration_info["name"],
                orientation_info=orientation_info,
                calibration_info=calibration_info,
            ),
        )
        handle = ZmartHandle(
            client=client, connection=dict(connection), hash6=run_hash()
        )
        loaded = _session_state.get(client)
        handle.translations = loaded.translations if loaded is not None else None
        if saved_origin is not None:
            handle.origin = saved_origin
        handle.data_location = saved_location
        self._handle = handle

    @property
    def client(self):
        """The connected LAS X client, for the setup notebooks that work with LAS X directly."""
        return self._handle.client

    def disconnect(self) -> None:
        """Close the session and drop what was loaded for it. A second call does nothing.

        LAS X's client has no close of its own, so the driver only forgets
        the limits and the calibration it installed for this connection and
        marks the session closed; every later command is then refused.
        """
        _gate.uninstall(self._handle.client)
        _session_state.uninstall(self._handle.client)
        self._handle.closed = True

    # --- describing the setup ---------------------------------------------------------

    def get_info(self):
        """``True, (description, setup, chooses_its_own_folder)``.

        The description says what the other commands cannot: what each
        setting means, in which unit and within which bounds, which
        objective sits in which slot, and which way +z points. The bounds
        come from the limits loaded at connect. ``setup`` is each thing set
        up once on this microscope (limits, orientation, objective
        calibration, origin, data location), done or not, in words.
        ``chooses_its_own_folder`` is False: the driver writes every file
        itself, from LAS X's cache, so it saves where ``acquire`` is told.
        """
        info = _instrument.get_info(self._handle)
        return True, (
            info["description"],
            info["setup"],
            info["chooses_its_own_folder"],
        )

    def get_actuators(self):
        """``True, (x, y, z)``: per axis the motors and the default, ``{"options": [...], "default": ...}``.

        x and y have one motor each, ``motoric``. z has two: ``z-wide``, the
        focus drive, which is the default, and ``z-galvo``, the fast drive for
        small steps.
        """
        motors = _frame.get_actuators(self._handle)
        return True, tuple(dict(motors[axis]) for axis in _AXES)

    # --- the stage ----------------------------------------------------------------------

    def get_xyz(self, with_actuators=None):
        """``True, (x, y, z, actuators, canvas)``, everything in micrometres.

        ``x``, ``y`` and ``z`` are measured from the saved origin; ``z`` is
        the focus, the two z drives added together. ``actuators`` gives
        every motor of each axis with its own raw reading, as LAS X reports
        it. ``canvas`` is everywhere a picture can show, in the same frame.
        ``with_actuators`` names the motor of every axis; the controller has
        filled in the defaults.
        """
        answer = _frame.get_xyz(self._handle, with_actuators=with_actuators)
        return True, _xyz(answer)

    def set_xyz(self, x, y, z, with_actuators=None):
        """Move the stage, then answer exactly as ``get_xyz`` does.

        The limits gate refuses a position outside the travel before
        anything moves (``ValueError``). ``with_actuators`` picks the z
        drive, for example ``{"z": "z-galvo"}`` for a small fast step. The
        answer is read back from LAS X once the stage has arrived, so it
        shows where the stage really is.
        """
        answer = _frame.set_xyz(self._handle, x, y, z, with_actuators=with_actuators)
        return True, _xyz(answer)

    def set_origin(self):
        """Make the current stage position the zero point of the frame, and save it.

        This is a setup step you run yourself, once, working with the driver
        directly; it is not a controller command. It records the stage
        position, both z drives and the objective in place, and saves them to
        this microscope's configuration. Every later connection starts from the
        saved zero point. Returns where it was saved. See
        ``procedures/frame.py`` for the details.
        """
        return _set_origin.set_origin(self._handle)

    # --- the settings -------------------------------------------------------------------

    def get_state(self):
        """``True, (changeable, observed)``.

        ``changeable`` holds what ``set_state`` applies: today the selected
        LAS X job. ``observed`` describes the instrument and the session:
        serial number, objectives, pixel size, the jobs on offer, and which
        limits file governs.
        """
        state = _instrument.get_state(self._handle)
        return True, (state["changeable"], state["observed"])

    def set_state(self, changeable):
        """Apply the changeable settings and answer ``True, applied``.

        An unknown setting or job is refused with ``ValueError``.
        """
        return True, _instrument.set_state(
            self._handle, {"changeable": dict(changeable)}
        )["applied"]

    # --- acquiring ----------------------------------------------------------------------

    def get_acquisition_settings(self):
        """``True, settings``: how a capture is triggered, ``full`` and nothing else."""
        return True, _acquisition.get_acquisition_settings(self._handle)

    def acquire(
        self, position_label, acquisition_settings, acquisition_type, output_folder
    ):
        """Capture here with the selected job, save, and answer ``True, (files, planes)``.

        The files are saved in ``output_folder``, or else in this session's
        staging folder under the data location; each name is the
        ``acquisition_type``, then the position label the workflow gave,
        and never overwrites an earlier picture. Every
        plane entry carries its file, its channel, depth and time index,
        and the stage position it was taken at, in the sample's frame.
        """
        answer = _acquisition.acquire(
            self._handle,
            position_label=position_label,
            acquisition_settings=acquisition_settings,
            acquisition_type=acquisition_type,
            output_folder=output_folder,
        )
        return True, (answer["files"], answer["planes"])

    # --- procedures ---------------------------------------------------------------------

    def get_procedures(self):
        """``True, procedures``: backlash takeup, parking the z-galvo, autofocus."""
        return True, _instrument.get_procedures(self._handle)

    def run_procedure(self, procedure):
        """Run the routine named by ``procedure["name"]``; the other keys are its arguments.

        Answers ``True`` and what the routine reports: the procedure that
        ran, and for autofocus the focus read back afterwards. A name that
        ``get_procedures`` does not list is refused with ``ValueError``.
        """
        return True, _instrument.run_procedure(self._handle, dict(procedure))


def _load_data_location():
    """This microscope's set-up data location, or None when it was never set up.

    A record that cannot be read stops the connection with how to fix it,
    rather than being taken for "not set up".
    """
    try:
        return _data_location.data_location()
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            f"this microscope's saved data location could not be read ({exc}); "
            "set it again with configuration/data_location/notebooks/set_data_location.ipynb"
        ) from exc


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
