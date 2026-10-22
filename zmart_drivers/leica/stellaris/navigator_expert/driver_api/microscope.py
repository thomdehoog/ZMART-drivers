r"""The STELLARIS driven directly, from a notebook or a script, without the ZMART Controller.

``connect()`` opens LAS X, loads this microscope's configuration and hands back
a :class:`Microscope`. Its methods are everything the driver can do: read and
move the stage in the sample's frame, choose a job and change its settings,
change the objective, take a picture, save it, let LAS X's cache go, and
describe the instrument::

    import zmart_drivers.leica.stellaris.navigator_expert as navigator_expert

    scope = navigator_expert.connect()
    scope.move_to(100.0, 250.0, 0.0)
    scope.select_job("HiRes")
    scope.change_setting("zoom", value=2.0)
    scope.acquire("A1")
    scope.save()
    scope.close()

Every method goes through the same parts as the controller's commands do: the
limits are asked before anything moves, every change is read back, and a
change that was sent but never seen is recorded (``state()`` lists it) while
the work goes on. A request that is wrong raises ``ValueError``; a change LAS
X refused or that failed raises ``RuntimeError`` with the reason.

The ZMART Controller's ``ZmartControllerPlugin`` class (``zmart_controller_plugin/``) is
this class with the controller's names and answers.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from typing import Any

from ..dispatcher import gate
from ..driver_setup import data_location
from ..driver_setup.limits import handshake
from ..driver_setup.optical_calibration.model import load_objective_calibration
from ..driver_setup.orientation import load_rig_orientation
from ..driver_setup.origin import saved_origin
from ..driver_setup.origin import set_origin as origin_step
from ..driver_setup.store import MACHINE
from ..procedures.absolute_coordinate_system import frame
from . import acquire as acquire_step
from ..procedures.clear_cache import clear as clear_step
from . import state as state_step
from ..procedures.keep_in_view import compensated_changes
from ..procedures.save_data import save_waiting as save_step
from ..procedures.save_data.naming import run_hash
from ..session import loaded_config
from ..session.handle import Session, job_catalog, note_unconfirmed, require_open, selected_job_name
from ..connection import lasx_client
from ..operations.readings import named_readings as readings
from ..operations.changes import job_settings
from ..primitives.interface import lasx_dialogs
from . import running_log

#: The driver's package: every part of the driver logs under this name.
DRIVER_PACKAGE = __package__.rpartition(".")[0]

#: The connection settings used for any key the caller leaves out.
#: ``microscope`` names this instrument. ``client`` and ``api_delay_ms`` feed
#: the LAS X connection. ``output_root`` is where a capture is saved when it is
#: not told a folder. ``load_limits`` and ``load_calibration`` load this
#: microscope's limits and objective calibration at connect; leave both on for
#: normal work. ``load_origin`` loads the saved zero point of the coordinate
#: frame; set it to False only to start in plain stage coordinates, for
#: example to capture a new origin when the saved one is damaged.
#: ``calibration_name`` (not set by default) picks a named calibration set.
DEFAULT_CONNECTION = {
    "microscope": "stellaris-y42h93",
    "client": "PythonClient",
    "api_delay_ms": None,
    "output_root": None,
    "load_limits": True,
    "load_calibration": True,
    "load_origin": True,
}


def connect(**connection: Any) -> Microscope:
    """Open LAS X and hand back the connected microscope; see :data:`DEFAULT_CONNECTION` for the keys."""
    return Microscope(connection)


class Microscope:
    """One connected STELLARIS: the LAS X session and everything loaded for it."""

    def __init__(self, connection: dict[str, Any] | None = None) -> None:
        """Open LAS X and load this microscope's configuration.

        Making the microscope is the connection: it opens the LAS X CAM API,
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
        origin = saved_origin.load_saved_origin() if connection["load_origin"] else None
        location = saved_data_location()
        MACHINE.ensure_layout()
        # Kept beside the saved settings, so that it survives a restart of the driver.
        running_log.start(MACHINE.snapshot_root() / "logs", DRIVER_PACKAGE)
        client = lasx_client.connect_python_client(
            client_name=connection["client"], api_delay_ms=connection["api_delay_ms"]
        )
        if not readings.ping(client):
            raise RuntimeError("LAS X does not answer: the ping after connecting failed.")

        # The limits are loaded first, so nothing can move before they govern.
        handshake.connect_handshake(client, load=connection["load_limits"])
        lasx_dialogs.keep_watching()
        orientation, orientation_info = load_rig_orientation()
        translations, calibration_info = load_objective_calibration(
            connection.get("calibration_name"), enabled=connection["load_calibration"]
        )
        loaded_config.install(
            client,
            loaded_config.SessionConfig(
                orientation=orientation,
                translations=translations,
                calibration_name=calibration_info["name"],
                orientation_info=orientation_info,
                calibration_info=calibration_info,
            ),
        )

        handle = Session(client=client, connection=dict(connection), hash6=run_hash())
        handle.translations = translations
        if origin is not None:
            handle.origin = origin
        handle.data_location = location
        self.handle = handle

    @property
    def client(self):
        """The connected LAS X client, for the setup notebooks that work with LAS X directly."""
        return self.handle.client

    def close(self) -> None:
        """Close the session and drop what was loaded for it. A second call does nothing.

        LAS X's client has no close of its own, so the driver forgets the
        limits and the calibration it installed for this connection and marks
        the session closed; every later call is then refused.
        """
        gate.uninstall(self.handle.client)
        loaded_config.uninstall(self.handle.client)
        self.handle.closed = True

    # --- the stage ----------------------------------------------------------------------

    def position(self, with_actuators: dict | None = None) -> dict:
        """Where the stage is, per axis, in micrometres from the saved origin.

        Each of ``x``, ``y`` and ``z`` holds its ``position``, its ``unit``,
        every motor with its own raw reading (``actuator_readings``) and the range a
        picture can show (``canvas``). ``z`` is the focus: the two z drives
        added together.
        """
        return frame.get_xyz(self.handle, with_actuators=with_actuators)

    def move_to(self, x: float, y: float, z: float, with_actuators: dict | None = None) -> dict:
        """Move to ``x``, ``y``, ``z`` (micrometres from the origin) and answer as :meth:`position`.

        The limits refuse a position outside the travel before anything
        moves. The focus moves with z-wide, and the answer is read back from
        LAS X once the stage has arrived.
        """
        return frame.set_xyz(self.handle, x, y, z, with_actuators=with_actuators)

    def motors(self) -> dict:
        """The motors of each axis, and the one used to move it: ``{"options": [...], "default": ...}``."""
        return frame.get_actuators(self.handle)

    def set_origin(self) -> dict:
        """Make the current stage position the zero point of the frame, and save it for later connections."""
        return origin_step.set_origin(self.handle)

    def zero_z_galvo(self) -> dict:
        """Park the z-galvo at 0 without changing the focus: its offset moves onto z-wide."""
        return frame.zero_z_galvo(self.handle)

    # --- jobs and settings -----------------------------------------------------------------

    def jobs(self) -> dict:
        """The LAS X jobs: ``{"selected", "jobs", "autofocus_jobs"}``, by name."""
        normal, autofocus = job_catalog(self.handle)
        return {
            "selected": selected_job_name(self.handle),
            "jobs": [job["Name"] for job in normal if job.get("Name")],
            "autofocus_jobs": [job["Name"] for job in autofocus if job.get("Name")],
        }

    def select_job(self, name: str) -> dict:
        """Select the LAS X job *name*; answers ``{"applied", "unconfirmed"}``.

        An autofocus job of Navigator Expert, and a name LAS X does not
        have, are refused with ``ValueError``.
        """
        return state_step.set_state(self.handle, {"changeable": {"job": name}})

    def job_settings(self, job: str | None = None) -> dict:
        """The settings of *job* (the selected job when None), as LAS X reports them."""
        require_open(self.handle)
        return readings.get_job_settings(self.handle.client, job or selected_job_name(self.handle))

    def change_setting(self, name: str, job: str | None = None, **arguments: Any) -> dict:
        """Change the setting *name* of *job* (the selected job when None), and read it back.

        *name* is one of the job's settings (``job_settings.SETTINGS``), such as ``"zoom"`` or
        ``"laser_intensity"``; the keywords are what that setting takes, such
        as ``value=2.0``. The limits are asked first. Answers the change's
        result, with ``confirmed`` True when the readback showed the new
        value; a change that was never seen is recorded and does not raise.
        """
        require_open(self.handle)
        job_settings.arguments_of(name, job or "", arguments)  # a wrong name or argument raises here
        job = job or selected_job_name(self.handle)
        result = job_settings.change(self.handle.client, name, job, **arguments)
        return checked(self.handle, result, f"change_setting({name!r}, job={job!r})")

    def change_objective(self, slot: int | None = None, name: str | None = None, magnification: float | None = None) -> dict:
        """Change the objective, by its slot, name or magnification, and keep the same spot in view.

        The stage is moved by the measured offset between the two objectives,
        so the frame still points at the same place on the sample.
        """
        require_open(self.handle)
        result = compensated_changes.set_objective(
            self.handle.client,
            selected_job_name(self.handle),
            readings.get_hardware_info(self.handle.client),
            slot_index=slot,
            name=name,
            magnification=magnification,
        )
        return checked(self.handle, result, "change_objective")

    # --- pictures -------------------------------------------------------------------------

    def acquisition_settings(self) -> dict:
        """How a capture can be triggered: ``full``, the whole selected job."""
        return acquire_step.get_acquisition_settings(self.handle)

    def acquire(
        self,
        position_label: str | None = None,
        *,
        acquisition_type: str = "acquisition",
        output_folder: str | None = None,
        acquisition_settings: dict | None = None,
    ) -> dict:
        """Run the selected job and keep the picture in LAS X's cache, with a note of how it was taken.

        Nothing is written yet: :meth:`save` writes the files. The files will
        be named after *acquisition_type* and *position_label*, in
        *output_folder*, or else in this session's folder under the data
        location.
        """
        return acquire_step.acquire(
            self.handle,
            position_label=position_label,
            acquisition_settings=acquisition_settings,
            acquisition_type=acquisition_type,
            output_folder=output_folder,
        )

    def save(self, label: str | None = None) -> list[dict]:
        """Write the captures that are waiting (only *label* when given) as OME-Zarr; answer one record each."""
        return save_step.save_waiting(self.handle, label)

    def clear_cache(self) -> dict:
        """Have LAS X let go of the pictures saved so far; answers ``{"cleared", "failed"}``."""
        return clear_step.clear(self.handle)

    # --- the state ------------------------------------------------------------------------

    def state(self) -> dict:
        """``{"changeable", "observed"}``: the selected job, and what can only be read, with the unconfirmed changes."""
        return state_step.get_state(self.handle)


def checked(handle: Session, result: dict, what: str) -> dict:
    """A change's result, raising when it failed and recording it when it was never seen."""
    if not result.get("success"):
        raise RuntimeError(f"{what} failed: {result.get('message')}")
    if not result.get("confirmed"):
        note_unconfirmed(handle, what, result)
    return result


def saved_data_location():
    """This microscope's set-up data location, or None when it was never set up.

    A record that cannot be read stops the connection with how to fix it,
    rather than being taken for "not set up".
    """
    try:
        return data_location.data_location()
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            f"this microscope's saved data location could not be read ({exc}); "
            "set it again with driver_setup/data_location/notebooks/set_data_location.ipynb"
        ) from exc
