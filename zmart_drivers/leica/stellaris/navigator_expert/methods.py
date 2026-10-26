"""The STELLARIS driven directly, from a notebook or a script: connect(), the Microscope, its Session and its running log.

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

The session every call works in, and the questions nearly every call asks.

A :class:`Session` is made when the driver connects, and the ``Microscope``
keeps it. It holds the open connection to LAS X and everything the driver remembers about
this session: the zero point of the frame, the objective calibration, the
data location, and the changes that were sent but could not be confirmed.
The small functions here are the ones nearly every command needs: refuse a
closed session, record an unconfirmed change, name the selected job, list the
jobs, and say where files are saved.

The running log: one file that records what the driver did, for finding out later what went wrong.

When something unexpected happens at the microscope, the most useful thing to
have is a plain record of what the driver sent, what came back, and what it
decided. This file starts that record. Every message the driver already
writes (each send, retry, fallback to another source and final answer) is
copied into one text file with a time on each line.

The file is kept small on purpose: when it reaches about five megabytes it is
renamed and a new one is started, and only the three newest old files are
kept, so the record can never fill the disk. This is separate from the log
that is saved beside the pictures of one acquisition, which says how those
pictures were made.

A problem with the log file itself (a full disk, a folder that cannot be
written) is noted once and never stops the experiment.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from typing import Any
from .dispatcher import limits as gate
from .driver_setup import data_location
from .driver_setup.limits import handshake
from .driver_setup.optical_calibration.model import load_objective_calibration
from .driver_setup.orientation import load_rig_orientation
from .driver_setup.origin import saved_origin
from .driver_setup.origin import set_origin as origin_step
from .driver_setup.store import MACHINE
from .procedures import absolute_coordinate_system
from .procedures import acquire as acquire_step
from .procedures.clear_cache import clear as clear_step
from .procedures import state as state_step
from .procedures import keep_in_view
from .procedures.save_data import save_waiting as save_step
from .procedures.save_data.naming import run_hash
from .primitives.cam_api import client as lasx_client
from .procedures import readings
from .primitives.cam_api import writes
from .procedures import changes
from .procedures import answer_dialogs
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from .procedures import readings as _readers
from logging.handlers import RotatingFileHandler


#: The driver's package: every part of the driver logs under this name.
DRIVER_PACKAGE = __package__

#: The connection settings used for any key the caller leaves out.
#: The first seven are the ZMART Controller's connection words, which every
#: client shows when connecting. ``microscope`` names this instrument and
#: ``api_type`` how LAS X is reached; neither is needed to connect. ``host`` is
#: where LAS X runs: its CAM API answers on this computer only, so any other
#: host is refused. ``client`` is the name the CAM API knows this program by.
#: LAS X asks no ``password``. ``config`` will name the saved configuration to
#: work with; until the driver keeps named configurations it must stay empty.
#: ``output_root`` is where a capture is saved when it is not told a folder.
#: Then the driver's own: ``api_delay_ms`` feeds the LAS X connection.
#: ``load_limits`` and ``load_calibration`` load this microscope's limits and
#: objective calibration at connect; leave both on for normal work.
#: ``load_origin`` loads the saved zero point of the coordinate frame; set it
#: to False only to start in plain stage coordinates, for example to capture a
#: new origin when the saved one is damaged. ``calibration_name`` (not set by
#: default) picks a named calibration set.
DEFAULT_CONNECTION = {
    "microscope": "stellaris-y42h93",
    "api_type": "LAS X CAM API - Navigator Expert",
    "host": "localhost",
    "client": "PythonClient",
    "password": None,
    "config": None,
    "output_root": None,
    "api_delay_ms": None,
    "load_limits": True,
    "load_calibration": True,
    "load_origin": True,
}

#: The hosts that name this computer, besides its own name.
THIS_COMPUTER = ("localhost", "127.0.0.1", "::1")


def connect(**connection: Any) -> Microscope:
    """Open LAS X and hand back the connected microscope; see :data:`DEFAULT_CONNECTION` for the keys."""
    return Microscope(connection)


def checked_connection(connection: dict[str, Any] | None) -> dict[str, Any]:
    """*connection* over :data:`DEFAULT_CONNECTION`, refused before LAS X is touched when it cannot be honoured.

    A field left empty takes the default. A host that is not this computer
    and a configuration name are refused rather than ignored, so a connection
    never silently goes somewhere other than what was typed.
    """
    import socket

    given = {key: value for key, value in (connection or {}).items() if value not in (None, "")}
    connection = {**DEFAULT_CONNECTION, **given}
    host = str(connection["host"]).strip()
    if host.lower() not in {*THIS_COMPUTER, socket.gethostname().lower()}:
        raise ValueError(
            f"host {host!r}: LAS X's CAM API answers on this computer only; "
            f"leave host empty or use localhost"
        )
    if connection["config"] is not None:
        raise ValueError(
            f"config {connection['config']!r}: this driver does not keep named configurations "
            "yet; leave config empty to work with this microscope's current setup"
        )
    return connection


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
        connection = checked_connection(connection)
        origin = saved_origin.load_saved_origin() if connection["load_origin"] else None
        location = saved_data_location()
        MACHINE.ensure_layout()
        # Kept beside the saved settings, so that it survives a restart of the driver.
        start(MACHINE.snapshot_root() / "logs", DRIVER_PACKAGE)
        client = lasx_client.connect_python_client(
            client_name=connection["client"], api_delay_ms=connection["api_delay_ms"]
        )
        if not readings.ping(client):
            raise RuntimeError("LAS X does not answer: the ping after connecting failed.")

        # The limits are loaded first, so nothing can move before they govern.
        handshake.connect_handshake(client, load=connection["load_limits"])
        answer_dialogs.keep_watching()
        orientation, orientation_info = load_rig_orientation()
        translations, calibration_info = load_objective_calibration(
            connection.get("calibration_name"), enabled=connection["load_calibration"]
        )
        handle = Session(
            client=client,
            # Without the password: the session's connection reaches saved state and metadata.
            connection={key: value for key, value in connection.items() if key != "password"},
            hash6=run_hash(),
            translations=translations,
            orientation=orientation,
            calibration_name=calibration_info["name"],
            orientation_info=orientation_info,
            calibration_info=calibration_info,
        )
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
        self.handle.closed = True

    # --- the stage ----------------------------------------------------------------------

    def position(self, with_actuators: dict | None = None) -> dict:
        """Where the stage is, per axis, in micrometres from the saved origin.

        Each of ``x``, ``y`` and ``z`` holds its ``position``, its ``unit``,
        every motor with its own raw reading (``actuator_readings``) and the range a
        picture can show (``canvas``). ``z`` is the focus: the two z drives
        added together.
        """
        return absolute_coordinate_system.get_xyz(self.handle, with_actuators=with_actuators)

    def move_to(self, x: float, y: float, z: float, with_actuators: dict | None = None) -> dict:
        """Move to ``x``, ``y``, ``z`` (micrometres from the origin) and answer as :meth:`position`.

        The limits refuse a position outside the travel before anything
        moves. The focus moves with z-wide, and the answer is read back from
        LAS X once the stage has arrived.
        """
        return absolute_coordinate_system.set_xyz(self.handle, x, y, z, with_actuators=with_actuators)

    def motors(self) -> dict:
        """The motors of each axis, and the one used to move it: ``{"options": [...], "default": ...}``."""
        return absolute_coordinate_system.get_actuators(self.handle)

    def set_origin(self) -> dict:
        """Make the current stage position the zero point of the frame, and save it for later connections."""
        return origin_step.set_origin(self.handle)

    def zero_z_galvo(self) -> dict:
        """Park the z-galvo at 0 without changing the focus: its offset moves onto z-wide."""
        return absolute_coordinate_system.zero_z_galvo(self.handle)

    # --- jobs and settings -----------------------------------------------------------------

    def jobs(self) -> dict:
        """The LAS X jobs: ``{"selected", "jobs", "autofocus_jobs"}``, by name."""
        normal, autofocus = self.handle.job_catalog()
        return {
            "selected": self.handle.selected_job_name(),
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
        self.handle.require_open()
        return readings.get_job_settings(self.handle.client, job or self.handle.selected_job_name())

    def change_setting(self, name: str, job: str | None = None, **arguments: Any) -> dict:
        """Change the setting *name* of *job* (the selected job when None), and read it back.

        *name* is one of the job's settings (``writes.SETTINGS``), such as ``"zoom"`` or
        ``"laser_intensity"``; the keywords are what that setting takes, such
        as ``value=2.0``. The limits are asked first. Answers the change's
        result, with ``confirmed`` True when the readback showed the new
        value; a change that was never seen is recorded and does not raise.
        """
        self.handle.require_open()
        writes.arguments_of(name, job or "", arguments)  # a wrong name or argument raises here
        job = job or self.handle.selected_job_name()
        result = changes.change(self.handle.client, name, job, **arguments)
        return checked(self.handle, result, f"change_setting({name!r}, job={job!r})")

    def change_objective(self, slot: int | None = None, name: str | None = None, magnification: float | None = None) -> dict:
        """Change the objective, by its slot, name or magnification, and keep the same spot in view.

        The stage is moved by the measured offset between the two objectives,
        so the frame still points at the same place on the sample.
        """
        self.handle.require_open()
        result = keep_in_view.set_objective(
            self.handle.client,
            self.handle.selected_job_name(),
            readings.get_hardware_info(self.handle.client),
            slot_index=slot,
            name=name,
            magnification=magnification,
            translations=self.handle.translations,
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
        handle.note_unconfirmed(what, result)
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


log = logging.getLogger(__name__)

#: What to run when no data location was set up, said in every refusal.
SET_UP = (
    "set this microscope's data location once with "
    "driver_setup/data_location/notebooks/set_data_location.ipynb, "
    "or connect with an output_root"
)


@dataclass
class Session:
    """One connection's session: the LAS X client and everything the driver remembers about it.

    Attributes:
        client: Connected LAS X CAM client (process-lifetime, no close).
        connection: The connection dict this session was opened with
            (carries ``output_root`` and the connect params).
        hash6: SESSION hash, minted at connect. Travels in lineage /
            ``session_hash6`` provenance only — each :func:`acquire` mints
            its own acquired-position hash for the output Naming.
        origin: The frame zero point: stage XY, both z drives, their focus
            sum, and the objective it was captured under. :func:`connect`
            fills it from the newest origin saved by :func:`set_origin`.
            When no origin has been saved, it stays all-zero, which means
            the frame is simply the absolute stage coordinates.
        position_counter: Next per-session position index handed to an
            unlabeled :func:`acquire` (formatted 6-digit zero-padded). An
            explicit ``position_label`` does not consume a counter value.
        translations: Per-objective-slot translation triples (µm) from the
            active calibration, loaded at connect; ``None`` when it could not
            be read (cross-objective moves are then refused, reads warn, and
            an objective change is not compensated).
        orientation: The camera-to-stage turn applied to the pixels this
            session saves, loaded at connect; ``None`` when none was loaded.
        calibration_name: Which named calibration set was loaded.
        orientation_info, calibration_info: What was loaded at connect, as
            the driver reports it in its readiness verdict.

    Loaded once, at connect, so one connection carries one consistent picture
    of the microscope for its whole life; re-measuring takes effect on the
    next connection.
        closed: Set by :func:`disconnect`; every op refuses a closed
            handle.
        unconfirmed: Every change this session sent that was accepted but
            could not be confirmed by a readback, newest last: what it was,
            when, and the driver's message. The driver does its best to
            confirm every change; when it cannot, it says so here (and in a
            warning) and carries on, so a workflow is told rather than
            stopped. ``get_state`` reports the list under ``observed``.
    """

    client: Any
    connection: dict[str, Any]
    hash6: str
    origin: dict[str, Any] = field(
        default_factory=lambda: {
            "x_um": 0.0,
            "y_um": 0.0,
            "z_wide_um": 0.0,
            "z_galvo_um": 0.0,
            "z_focus_um": 0.0,
            "objective": None,
        }
    )
    #: Where :func:`get_xyz` last read the stage (and :func:`set_xyz` ends by
    #: reading it): the frame position, with the z-wide and galvo readings.
    #: Remembered rather than re-read, so that the capture path holds no extra
    #: call that can hang. The drives ride along because a job states its stack
    #: in its drive's own positions, and without them those slices cannot be
    #: put in the frame.
    driven_to: dict[str, float] | None = None
    position_counter: int = 0
    translations: dict[int, tuple[float, float, float]] | None = None
    orientation: Any = None
    calibration_name: str | None = None
    orientation_info: dict | None = None
    calibration_info: dict | None = None
    #: This microscope's data location, set up once and loaded at connect
    #: (driver_setup/data_location); None when it was never set up.
    data_location: Path | None = None
    closed: bool = False
    unconfirmed: list[dict[str, Any]] = field(default_factory=list)
    #: The captures taken whose files are not yet written, each with the note
    #: made as it was taken. ``acquire`` adds to it; ``save_data`` empties it.
    unsaved: list[Any] = field(default_factory=list)
    #: The pictures in LAS X's cache whose captures are saved, as
    #: ``(element number, name)``. ``save_data`` adds to it; ``clear_cache``
    #: empties it.
    in_cache: list[tuple[int, str]] = field(default_factory=list)

    def require_open(self) -> None:
        """Refuse to drive a disconnected self."""
        if self.closed:
            raise RuntimeError("session is disconnected")

    def note_unconfirmed(self, what: str, result: Any) -> None:
        """Record a change that was sent and accepted but never confirmed, and carry on.

        The driver confirms what it can. When a readback never matched in
        time, the change is not known to have failed, only not known to have
        happened, so it is written down here, said in a warning, and the
        command goes on. The workflow sees the list in ``get_state``.
        """
        message = result.get("message") if isinstance(result, dict) else str(result)
        note = {
            "what": what,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "message": message or "readback did not confirm the change",
        }
        self.unconfirmed.append(note)
        del self.unconfirmed[:-50]  # keep the newest fifty
        log.warning("unconfirmed, carrying on: %s: %s", what, note["message"])

    def selected_job_name(self) -> str | None:
        """Name of the currently selected LAS X job, or ``None`` when it cannot be read.

        A reading that fails is unknown, not a stop. The commands that only
        use this to decide whether a selection is needed go ahead and select;
        the ones that need the name to compute a move say so and refuse.
        """
        selected = _readers.get_selected_job(self.client)
        name = selected.get("Name") if selected else None
        if not name:
            log.warning("could not determine the selected LAS X job")
            return None
        return name

    def job_catalog(self) -> tuple[list[dict], list[dict]]:
        """The live job catalog split into (normal, autofocus) jobs.

        Autofocus jobs are a separate category: they never appear as acquisition
        or state options, and the driver does not select them.
        """
        jobs = _readers.get_jobs(self.client) or []
        normal = [dict(j) for j in jobs if not j.get("IsAutofocus")]
        autofocus = [dict(j) for j in jobs if j.get("IsAutofocus")]
        return normal, autofocus

    def output_root(self) -> Path:
        """The folder this session saves under: its own ``output_root``, else the set-up data location.

        Raises ``RuntimeError`` saying how to set one up when neither is there:
        data saved somewhere nobody chose is data lost.
        """
        root = self.connection.get("output_root") or self.data_location
        if not root:
            raise RuntimeError(
                f"this microscope does not know where to save its data: {SET_UP}"
            )
        return Path(root).expanduser().resolve()


FILE_NAME = "running_log.txt"
MAX_BYTES = 5_000_000
OLD_FILES_KEPT = 3
LINE_FORMAT = "%(asctime)s  %(levelname)-7s  %(name)s  %(message)s"

_log = logging.getLogger(__name__)


def start(folder, logger_name, *, level=logging.INFO) -> Path | None:
    """Begin copying the driver's messages into ``<folder>/running_log.txt``.

    *logger_name* is the driver's package name; every message from every part
    of the driver reaches it. Calling this again for the same file does
    nothing, so each connect can call it safely. Returns the path of the
    file, or None when it could not be opened.
    """
    path = Path(folder) / FILE_NAME
    package_logger = logging.getLogger(logger_name)
    for handler in package_logger.handlers:
        if getattr(handler, "baseFilename", None) == str(path.resolve()):
            return path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path, maxBytes=MAX_BYTES, backupCount=OLD_FILES_KEPT, encoding="utf-8"
        )
    except OSError as why:
        _log.warning("the running log could not be opened at %s: %s", path, why)
        return None
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(LINE_FORMAT))
    package_logger.addHandler(handler)
    if package_logger.level == logging.NOTSET or package_logger.level > level:
        package_logger.setLevel(level)
    return path
