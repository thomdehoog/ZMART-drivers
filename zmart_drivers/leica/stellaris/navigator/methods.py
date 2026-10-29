"""The STELLARIS driven through LAS X's plain Navigator, from a notebook or a script: connect(), the Microscope, its Session and its running log.

``connect()`` reaches LAS X's hardware server, loads this microscope's
configuration and hands back a :class:`Microscope`. Its methods are
everything the driver can do: read and move the stage in the sample's frame,
change the five settings LAS X's plain Navigator takes, change the
objective, take a picture or a tile scan, save them, let LAS X's cache go,
and describe the instrument::

    import zmart_drivers.leica.stellaris.navigator as navigator

    scope = navigator.connect()
    scope.move_to(100.0, 250.0, 0.0)
    scope.change_setting("zoom", 2.0)
    scope.acquire("A1")
    scope.tile_scan([(0.0, 0.0), (600.0, 0.0)], "row1")
    scope.save()
    scope.close()

Every method goes through the same parts as the controller's commands do: the
limits are asked before anything moves, every change is read back, and a
change that was sent but never seen is recorded (``state()`` lists it) while
the work goes on. A request that is wrong raises ``ValueError``; a change LAS
X refused or that failed raises ``RuntimeError`` with the reason.

A :class:`Session` is made when the driver connects, and the ``Microscope``
keeps it. It holds the connection to LAS X and everything the driver
remembers about this session: the zero point of the frame, the objective
calibration, the data location, the pictures not yet saved, and the changes
that were sent but could not be confirmed.

The running log: one file that records what the driver did, for finding out
later what went wrong. Every message the driver writes (each send, each
answer, each decision) is copied into one text file in the configuration
folder, with a time on each line. The file is kept small: at about five
megabytes it is renamed and a new one is started, and only the three newest
old files are kept. A problem with the log file itself is noted once and
never stops the experiment.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from .dispatcher import limits as gate
from .driver_setup import data_location
from .driver_setup.limits import handshake
from .driver_setup.optical_calibration.model import load_objective_calibration
from .driver_setup.orientation import load_rig_orientation
from .driver_setup.origin import saved_origin
from .driver_setup.origin import set_origin as origin_step
from .driver_setup.store import MACHINE, use_configuration
from .primitives.direct import client as hardware_server
from .primitives.direct.writes import SETTINGS
from .procedures import (
    absolute_coordinate_system,
    answer_dialogs,
    changes,
    keep_in_view,
    readings,
)
from .procedures import acquire as acquire_step
from .procedures import state as state_step
from .procedures.clear_cache import clear as clear_step
from .procedures.save_data import save_waiting as save_step
from .procedures.save_data.naming import run_hash

#: The driver's package: every part of the driver logs under this name.
DRIVER_PACKAGE = __package__

#: The connection settings used for any key the caller leaves out.
#: The first seven are the ZMART Controller's connection words, which every
#: client shows when connecting. ``microscope`` names this instrument and
#: ``api_type`` how LAS X is reached; neither is needed to connect. ``host``
#: is where LAS X runs: its hardware server answers on this computer only, so
#: any other host is refused. ``client`` is the name the driver calls itself
#: in its log. LAS X asks no ``password``. ``config`` names the config to
#: work with, a ``config_YYYYMMDD_HHMM`` folder kept on this computer; empty,
#: the most recent. ``output_root`` is where a capture is saved when it is
#: not told a folder. ``load_limits`` and ``load_calibration`` load this
#: microscope's limits and objective calibration at connect; leave both on
#: for normal work. ``load_origin`` loads the saved zero point of the frame;
#: set it to False only to start in plain stage coordinates, for example to
#: capture a new origin when the saved one is damaged. ``calibration_name``
#: (not set by default) picks a named calibration set.
DEFAULT_CONNECTION = {
    "microscope": "stellaris-y42h93",
    "api_type": "LAS X Navigator - hardware server",
    "host": "localhost",
    "client": "ZMART",
    "password": None,
    "config": None,
    "output_root": None,
    "load_limits": True,
    "load_calibration": True,
    "load_origin": True,
}

#: The hosts that name this computer, besides its own name.
THIS_COMPUTER = ("localhost", "127.0.0.1", "::1")


def connect(**connection: Any) -> Microscope:
    """Reach LAS X and hand back the connected microscope; see :data:`DEFAULT_CONNECTION` for the keys."""
    return Microscope(connection)


def named_configurations() -> list[str]:
    """The configs kept on this computer, by folder name, the most recent first."""
    return MACHINE.configurations()


def checked_connection(connection: dict[str, Any] | None) -> dict[str, Any]:
    """*connection* over :data:`DEFAULT_CONNECTION`, refused before LAS X is touched when it cannot be honoured.

    A field left empty takes the default. An empty ``config`` is the most
    recent config kept on this computer (none when there is none). A host
    that is not this computer and a config that is not kept here are refused
    rather than ignored, so a connection never silently goes somewhere other
    than what was typed.
    """
    import socket

    given = {key: value for key, value in (connection or {}).items() if value not in (None, "")}
    connection = {**DEFAULT_CONNECTION, **given}
    host = str(connection["host"]).strip()
    if host.lower() not in {*THIS_COMPUTER, socket.gethostname().lower()}:
        raise ValueError(
            f"host {host!r}: LAS X's hardware server answers on this computer only; "
            f"leave host empty or use localhost"
        )
    kept = named_configurations()
    if connection["config"] is None:
        connection["config"] = kept[0] if kept else None
    else:
        connection["config"] = str(connection["config"]).strip()
        if connection["config"] not in kept:
            raise ValueError(
                f"config {connection['config']!r} is not kept on this computer; "
                f"choose one of {kept} or leave config empty for the most recent"
                if kept
                else f"config {connection['config']!r}: no configs are kept on this computer yet; "
                "leave config empty"
            )
    return connection


class Microscope:
    """One connected STELLARIS, through its plain Navigator: the LAS X session and everything loaded for it."""

    def __init__(self, connection: dict[str, Any] | None = None) -> None:
        """Reach LAS X and load this microscope's configuration.

        Making the microscope is the connection: it reaches LAS X's hardware
        server, loads the limits (which switch on the limits gate), the saved
        origin, the image-to-stage orientation and the objective calibration,
        and checks that LAS X shows its plain Navigator. Raises when any of
        these fail, and leaves nothing open behind.

        The saved origin is read before LAS X is contacted. A saved origin
        that exists but cannot be read stops the connection, because
        quietly falling back to stage coordinates would put every later
        move somewhere other than intended.
        """
        connection = checked_connection(connection)
        # Every setting below is read from, and saved in, the chosen config.
        use_configuration(connection["config"])
        origin = saved_origin.load_saved_origin() if connection["load_origin"] else None
        location = saved_data_location()
        MACHINE.ensure_layout()
        # Kept beside the saved settings, so that it survives a restart of the driver.
        start(MACHINE.snapshot_root() / "logs", DRIVER_PACKAGE)
        try:
            client = hardware_server.connect(connection["client"])
        except hardware_server.HardwareServerUnavailable as why:
            raise RuntimeError(f"LAS X does not answer: {why}") from why
        if readings.shows_plain_navigator(client) is False:
            raise RuntimeError(
                "LAS X lists Navigator Expert jobs, so it shows Navigator Expert, not its plain "
                "Navigator; switch LAS X to its Navigator, or use the navigator_expert driver"
            )

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
        """The session's connection to LAS X's hardware server, for the setup notebooks."""
        return self.handle.client

    def close(self) -> None:
        """Close the session and drop what was loaded for it. A second call does nothing.

        The driver forgets the limits and the calibration it installed for
        this connection and marks the session closed; every later call is
        then refused.
        """
        gate.uninstall(self.handle.client)
        self.handle.closed = True

    # --- the stage ----------------------------------------------------------------------

    def position(self, with_actuators: dict | None = None) -> dict:
        """Where the stage is, per axis, in micrometres from the saved origin.

        Each of ``x``, ``y`` and ``z`` holds its ``position``, its ``unit``,
        every motor with its own raw reading (``actuator_readings``) and the
        range a picture can show (``canvas``). ``z`` is the focus: the two z
        drives added together.
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

    # --- the settings and the objective ---------------------------------------------------

    def settings(self) -> dict:
        """What LAS X is set to now: ``zoom``, ``scan_speed``, ``scan_field_rotation``, ``averaging``, ``pinhole``, ``format``, ``scan_mode``, ``objective``, ``z_stack``."""
        self.handle.require_open()
        found = acquire_step.current_settings(self.handle)
        return {name: found[name] for name in found if name not in ("fov_m", "bit_depth")}

    def change_setting(self, name: str, value: float) -> dict:
        """Change the setting *name* to *value*, and read it back.

        *name* is one of the settings the plain Navigator route can change
        (``zoom``, ``scan_speed``, ``scan_field_rotation``, ``line_average``,
        ``pinhole_airy``). The limits are asked first. Answers the change's
        result, with ``confirmed`` True when the read-back showed the new
        value; a change that was never seen is recorded and does not raise.
        """
        self.handle.require_open()
        if name not in SETTINGS:
            raise ValueError(f"no setting {name!r}; the settings are {sorted(SETTINGS)}")
        result = changes.change(self.handle.client, name, value)
        return checked(self.handle, result, f"change_setting({name!r}, {value!r})")

    def change_objective(self, slot: int | None = None, name: str | None = None, magnification: float | None = None) -> dict:
        """Change the objective, by its slot, name or magnification, and keep the same spot in view.

        The stage is moved by the measured offset between the two objectives,
        so the frame still points at the same place on the sample.
        """
        self.handle.require_open()
        result = keep_in_view.set_objective(
            self.handle.client,
            readings.get_objectives(self.handle.client),
            slot_index=slot,
            name=name,
            magnification=magnification,
            translations=self.handle.translations,
        )
        return checked(self.handle, result, "change_objective")

    # --- pictures -------------------------------------------------------------------------

    def acquisition_settings(self) -> dict:
        """How a capture can be triggered: ``full``, one picture with the current settings."""
        return acquire_step.get_acquisition_settings(self.handle)

    def acquire(
        self,
        position_label: str | None = None,
        *,
        acquisition_type: str = "acquisition",
        output_folder: str | None = None,
        acquisition_settings: dict | None = None,
    ) -> dict:
        """Take one picture with the current settings and keep it in LAS X's cache, with a note of how it was taken.

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

    def tile_scan(
        self,
        positions: list[tuple[float, float]],
        position_label: str | None = None,
        *,
        acquisition_type: str = "tile_scan",
        output_folder: str | None = None,
    ) -> dict:
        """Take one picture of several fields at *positions* (x, y pairs in micrometres from the origin), at the current focus.

        LAS X scans them in one acquisition. :meth:`save` writes one store
        per field, named ``<acquisition_type>_<label>_field<n>``, each placed
        where its field was taken. Every position is shown to the limits
        first; one outside them refuses the whole scan. The stage is put back
        at the first field afterwards.
        """
        return acquire_step.tile_scan(
            self.handle,
            positions,
            position_label=position_label,
            acquisition_type=acquisition_type,
            output_folder=output_folder,
        )

    def row_of_fields(self, count: int) -> list[tuple[float, float]]:
        """*count* positions in a row along x from here, a field and a tenth apart, for :meth:`tile_scan`."""
        return acquire_step.row_of_fields(self.handle, count)

    def save(self, label: str | None = None) -> list[dict]:
        """Write the pictures that are waiting (only *label* when given) as OME-Zarr; answer one record each."""
        return save_step.save_waiting(self.handle, label)

    def clear_cache(self) -> dict:
        """Have LAS X let go of the pictures saved so far; answers ``{"cleared", "failed"}``."""
        return clear_step.clear(self.handle)

    # --- the state ------------------------------------------------------------------------

    def state(self) -> dict:
        """``{"changeable", "observed"}``: the settings and the objective, and what can only be read, with the unconfirmed changes."""
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

    ``client`` is the connection to LAS X's hardware server. ``connection``
    is what the session was opened with. ``hash6`` names the session in the
    files it saves. ``origin`` is the frame's zero point: stage XY, both z
    drives, their focus sum, and the objective it was captured under; with
    none saved it stays all-zero, and the frame is the stage's own
    coordinates. ``driven_to`` is where the stage was last read, remembered
    so a capture needs no extra call that can hang. ``translations`` are the
    per-objective offsets of the loaded calibration (None when none was
    loaded: an objective change is then not compensated). ``orientation`` is
    the camera-to-stage turn applied to saved pictures. ``unsaved`` holds the
    pictures taken whose files are not written yet, ``in_cache`` the pictures
    in LAS X's cache whose captures are saved (``clear_cache`` empties it),
    and ``unconfirmed`` every change that was accepted but never seen,
    newest last: the driver carries on and says so here.
    """

    client: Any
    connection: dict[str, Any]
    hash6: str
    origin: dict[str, Any] = field(
        default_factory=lambda: {
            "x_um": 0.0, "y_um": 0.0, "z_wide_um": 0.0, "z_galvo_um": 0.0, "z_focus_um": 0.0, "objective": None,
        }
    )
    driven_to: dict[str, float] | None = None
    position_counter: int = 0
    translations: dict[int, tuple[float, float, float]] | None = None
    orientation: Any = None
    calibration_name: str | None = None
    orientation_info: dict | None = None
    calibration_info: dict | None = None
    data_location: Path | None = None
    closed: bool = False
    unconfirmed: list[dict[str, Any]] = field(default_factory=list)
    unsaved: list[Any] = field(default_factory=list)
    in_cache: list[tuple[int, str]] = field(default_factory=list)

    def require_open(self) -> None:
        """Refuse to drive a disconnected session."""
        if self.closed:
            raise RuntimeError("session is disconnected")

    def note_unconfirmed(self, what: str, result: Any) -> None:
        """Record a change that was sent and accepted but never confirmed, and carry on.

        A read-back that never matched in time does not mean the change
        failed, only that it is not known to have happened, so it is written
        down here, said in a warning, and the command goes on. The workflow
        sees the list in ``state()``.
        """
        message = result.get("message") if isinstance(result, dict) else str(result)
        note = {
            "what": what,
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "message": message or "readback did not confirm the change",
        }
        self.unconfirmed.append(note)
        del self.unconfirmed[:-50]  # keep the newest fifty
        log.warning("unconfirmed, carrying on: %s: %s", what, note["message"])

    def output_root(self) -> Path:
        """The folder this session saves under: its own ``output_root``, else the set-up data location.

        Raises ``RuntimeError`` saying how to set one up when neither is there:
        data saved somewhere nobody chose is data lost.
        """
        root = self.connection.get("output_root") or self.data_location
        if not root:
            raise RuntimeError(f"this microscope does not know where to save its data: {SET_UP}")
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
        handler = RotatingFileHandler(path, maxBytes=MAX_BYTES, backupCount=OLD_FILES_KEPT, encoding="utf-8")
    except OSError as why:
        _log.warning("the running log could not be opened at %s: %s", path, why)
        return None
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(LINE_FORMAT))
    package_logger.addHandler(handler)
    if package_logger.level == logging.NOTSET or package_logger.level > level:
        package_logger.setLevel(level)
    return path
