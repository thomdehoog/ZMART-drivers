"""The FV4000 driven directly, from a notebook or a script: connect(), the Microscope, its Session and its running log.

``connect()`` reaches FLUOVIEW's Remote Development Kit (RDK), loads this
microscope's configuration and hands back a :class:`Microscope`. Its
methods are everything the driver can do: read and move the stage in the
sample's frame, change the objective or a setting, take a picture, save it,
and describe the instrument::

    import zmart_drivers.evident.fv4000.rdk as fv4000

    scope = fv4000.connect(host="127.0.0.1")
    scope.move_to(100.0, 250.0, 0.0)
    scope.change_setting("laser_intensity", laser_id="L488", value=2.0)
    scope.acquire("A1")
    scope.save()
    scope.close()

Every method goes through the same parts as the controller's commands do: the
limits are asked before anything moves, every change is read back, and a
change that was sent but never seen is recorded (``state()`` lists it) while
the work goes on. A request that is wrong raises ``ValueError``; a change
FLUOVIEW refused or that failed raises ``RuntimeError`` with the reason.

The ZMART Controller's ``ZmartControllerPlugin`` class
(``zmart_controller_plugin/``) is this class with the controller's names and
answers.

The :class:`Session` is what the driver remembers about one connection: the
client, the origin, the orientation and calibration loaded at connect, the
captures not yet saved, and the changes that were never confirmed.

The running log is one file in the configuration folder that records what
the driver sent, what came back and what it decided. It is renamed at about
five megabytes and the three newest old files are kept, so it can never fill
the disk. A problem with the log file itself is noted once and never stops
the experiment.

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
from .driver_setup import frame as frame_record
from .driver_setup.limits import handshake
from .driver_setup.optical_calibration.model import load_objective_calibration
from .driver_setup.orientation import load_rig_orientation
from .driver_setup.origin import saved_origin
from .driver_setup.origin import set_origin as origin_step
from .driver_setup.store import MACHINE, use_configuration
from .primitives.rdk import client as rdk_client
from .primitives.rdk import writes
from .procedures import absolute_coordinate_system, changes, keep_in_view, readings
from .procedures import acquire as acquire_step
from .procedures import state as state_step
from .procedures.save_data import save as save_step
from .procedures.save_data.naming import run_hash

log = logging.getLogger(__name__)

#: The driver's package: every part of the driver logs under this name.
DRIVER_PACKAGE = __package__

#: The connection settings used for any key the caller leaves out.
#: The first seven are the ZMART Controller's connection words, which every
#: client shows when connecting. ``microscope`` names this instrument and
#: ``api_type`` how FLUOVIEW is reached; neither is needed to connect.
#: ``host`` is the computer FLUOVIEW runs on (``127.0.0.1`` when the driver
#: runs there too) and ``port`` the RDK's port, 8080 unless FLUOVIEW was told
#: otherwise. FLUOVIEW asks no ``password``. ``config`` names the config to
#: work with, a ``config_YYYYMMDD_HHMM`` folder kept on this computer; empty,
#: the most recent. ``output_root`` is where a capture is saved when it is
#: not told a folder. Then the driver's own: ``remote_mode`` puts FLUOVIEW in
#: REMOTE mode at connect, in which it shows no pop-up dialogs that would
#: wait for a person; ``escape_objective`` lets the objective drop away while
#: the stage moves. ``load_limits``, ``load_calibration`` and ``load_origin``
#: load this microscope's saved settings at connect; leave them on for normal
#: work, and switch ``load_origin`` off only to start in FLUOVIEW's plain
#: stage coordinates, for example to capture a new origin.
DEFAULT_CONNECTION = {
    "microscope": "fv4000",
    "api_type": "FLUOVIEW RDK (XML-RPC)",
    "host": "127.0.0.1",
    "port": rdk_client.DEFAULT_PORT,
    "password": None,
    "config": None,
    "output_root": None,
    "remote_mode": True,
    "escape_objective": False,
    "load_limits": True,
    "load_calibration": True,
    "load_origin": True,
}


def connect(**connection: Any) -> Microscope:
    """Reach FLUOVIEW and hand back the connected microscope; see :data:`DEFAULT_CONNECTION` for the keys."""
    return Microscope(connection)


def named_configurations() -> list[str]:
    """The configs kept on this computer, by folder name, the most recent first."""
    return MACHINE.configurations()


def checked_connection(connection: dict[str, Any] | None) -> dict[str, Any]:
    """*connection* over :data:`DEFAULT_CONNECTION`, refused before FLUOVIEW is touched when it cannot be honoured.

    A field left empty takes the default. The port must be a whole number. An
    empty ``config`` is the most recent config kept on this computer (none
    when there is none); a config that is not kept here is refused rather
    than ignored.
    """
    given = {
        key: value
        for key, value in (connection or {}).items()
        if value not in (None, "")
    }
    connection = {**DEFAULT_CONNECTION, **given}
    try:
        connection["port"] = int(connection["port"])
    except (TypeError, ValueError):
        raise ValueError(
            f"port {connection['port']!r} is not a whole number; FLUOVIEW's RDK listens on 8080 unless told otherwise"
        ) from None
    connection["host"] = str(connection["host"]).strip()
    kept = named_configurations()
    if connection["config"] is None:
        connection["config"] = kept[0] if kept else None
    else:
        connection["config"] = str(connection["config"]).strip()
        if connection["config"] not in kept:
            raise ValueError(
                f"config {connection['config']!r} is not kept on this computer; choose one of {kept} or leave config empty"
                if kept
                else f"config {connection['config']!r}: no configs are kept on this computer yet; leave config empty"
            )
    return connection


class Microscope:
    """One connected FV4000: the RDK session and everything loaded for it."""

    def __init__(self, connection: dict[str, Any] | None = None) -> None:
        """Reach FLUOVIEW and load this microscope's configuration.

        Making the microscope is the connection: it opens the RDK client,
        checks that FLUOVIEW answers, puts it in REMOTE mode when asked,
        loads the limits (which switch on the limits gate), the saved origin,
        the orientation and the objective calibration. Raises when any of
        these fail. The saved origin is read before FLUOVIEW is contacted: one
        that exists but cannot be read stops the connection, because quietly
        falling back to stage coordinates would put every later move somewhere
        other than intended.
        """
        connection = checked_connection(connection)
        use_configuration(connection["config"])
        origin = saved_origin.load_saved_origin() if connection["load_origin"] else None
        location = saved_data_location()
        MACHINE.ensure_layout()
        start(MACHINE.snapshot_root() / "logs", DRIVER_PACKAGE)
        client = rdk_client.connect_client(connection["host"], connection["port"])
        version = readings.get_version(client)
        if version is None:
            raise RuntimeError(
                f"FLUOVIEW does not answer at {client.address.url}: check that it is running and that its RDK is switched on"
            )

        # The limits are loaded first, so nothing can move before they govern.
        handshake.connect_handshake(client, load=connection["load_limits"])
        orientation, orientation_info = load_rig_orientation()
        translations, calibration_info = load_objective_calibration(
            enabled=connection["load_calibration"]
        )
        handle = Session(
            client=client,
            connection={
                key: value for key, value in connection.items() if key != "password"
            },
            hash6=run_hash(),
            fluoview_version=str(version),
            translations=translations,
            orientation=orientation,
            orientation_info=orientation_info,
            calibration_info=calibration_info,
            escape_objective=bool(connection["escape_objective"]),
            frames=frame_record.load_frames(),
        )
        if origin is not None:
            handle.origin = origin
        handle.data_location = location
        self.handle = handle
        if connection["remote_mode"]:
            result = changes.set_remote_mode(client, True)
            if not result.get("success"):
                raise RuntimeError(
                    f"FLUOVIEW could not be put in REMOTE mode: {result.get('message')}"
                )

    @property
    def client(self):
        """The connected RDK client, for the setup notebooks that work with FLUOVIEW directly."""
        return self.handle.client

    def close(self) -> None:
        """Close the session: hand FLUOVIEW back to the person at the screen (LOCAL mode) and drop what was loaded.

        A second call does nothing; every later call is refused.
        """
        if self.handle.closed:
            return
        if self.handle.connection.get("remote_mode"):
            try:
                changes.set_remote_mode(self.handle.client, False)
            except Exception as why:  # noqa: BLE001 -- closing must never fail over a lost FLUOVIEW
                log.warning("FLUOVIEW could not be put back in LOCAL mode: %s", why)
        gate.uninstall(self.handle.client)
        self.handle.closed = True

    # --- the stage ----------------------------------------------------------------------

    def position(self, with_actuators: dict | None = None) -> dict:
        """Where the stage is, per axis, in micrometres from the saved origin.

        Each of ``x``, ``y`` and ``z`` holds its ``position``, its ``unit``,
        every motor with its own raw reading (``actuator_readings``) and the
        range a picture can show (``canvas``). ``z`` is the focus motor; the
        piezo is reported beside it.
        """
        return absolute_coordinate_system.get_xyz(
            self.handle, with_actuators=with_actuators
        )

    def move_to(
        self, x: float, y: float, z: float, with_actuators: dict | None = None
    ) -> dict:
        """Move to ``x``, ``y``, ``z`` (micrometres from the origin) and answer as :meth:`position`, read back once arrived.

        The limits refuse a position outside the travel before anything moves.
        """
        return absolute_coordinate_system.set_xyz(
            self.handle, x, y, z, with_actuators=with_actuators
        )

    def motors(self) -> dict:
        """The motors of each axis, and the one used to move it: ``{"options": [...], "default": ...}``."""
        return absolute_coordinate_system.get_actuators(self.handle)

    def set_origin(self) -> dict:
        """Make the current stage position the zero point of the frame, and save it for later connections."""
        return origin_step.set_origin(self.handle)

    def move_piezo(self, z_um: float) -> dict:
        """Move the piezo to *z_um* micrometres from FLUOVIEW's Z origin, within the limits, and read it back."""
        self.handle.require_open()
        return checked(
            self.handle,
            changes.move_piezo(self.handle.client, z_um),
            f"move_piezo({z_um})",
        )

    # --- the objective and the settings -----------------------------------------------

    def objectives(self) -> dict:
        """The objectives on the nosepiece and the one in use: ``{"current": {"slot", "name"}, "objectives": [...]}``."""
        self.handle.require_open()
        return {
            "current": readings.get_objective(self.handle.client),
            "objectives": readings.get_objectives(self.handle.client) or [],
        }

    def change_objective(
        self, slot: int | None = None, name: str | None = None
    ) -> dict:
        """Change the objective, by its nosepiece slot (from 0) or its name, and keep the same spot in view.

        The stage is moved by the measured offset between the two objectives,
        so the frame still points at the same place on the sample. Without a
        calibration that covers the pair, the change is refused.
        """
        self.handle.require_open()
        result = keep_in_view.set_objective(
            self.handle.client,
            readings.get_objectives(self.handle.client),
            slot=slot,
            name=name,
            translations=self.handle.translations,
        )
        return checked(self.handle, result, "change_objective")

    def settings(self) -> dict:
        """The settings :meth:`change_setting` can change, each with the arguments it takes."""
        return {name: list(setting.takes) for name, setting in writes.SETTINGS.items()}

    def change_setting(self, name: str, **arguments: Any) -> dict:
        """Change the setting *name* with its arguments (see :meth:`settings`), and read it back.

        For example ``change_setting("laser_intensity", laser_id="L488", value=2.0)``
        or ``change_setting("z_stack_slices", value=21)``. The limits are asked
        first. Answers the change's result, with ``confirmed`` True when the
        read-back showed the new value; a change that was never seen is
        recorded and does not raise.
        """
        self.handle.require_open()
        writes.arguments_of(name, arguments)  # a wrong name or argument raises here
        return checked(
            self.handle,
            changes.change(self.handle.client, name, **arguments),
            f"change_setting({name!r})",
        )

    def load_settings_from(self, image_path: str) -> dict:
        """Recall the whole imaging setup a saved picture was taken with (FLUOVIEW's ``loadParameter``)."""
        self.handle.require_open()
        result = checked(
            self.handle,
            changes.load_parameter(self.handle.client, image_path),
            f"load_settings_from({image_path!r})",
        )
        self.handle.settings_loaded_from = str(image_path)
        return result

    # --- pictures -------------------------------------------------------------------------

    def acquisition_settings(self) -> dict:
        """How a capture can be triggered: ``full``, the imaging set up in FLUOVIEW."""
        return acquire_step.get_acquisition_settings(self.handle)

    def acquire(
        self,
        position_label: str | None = None,
        *,
        acquisition_type: str = "acquisition",
        output_folder: str | None = None,
        acquisition_settings: dict | None = None,
    ) -> dict:
        """Start the imaging set up in FLUOVIEW, wait for it, and keep a note of the capture.

        Nothing is written yet by the driver: :meth:`save` writes the files,
        named after *acquisition_type* and *position_label*, in
        *output_folder*, or else in this session's folder under the data
        location. FLUOVIEW's own file is named in the answer.
        """
        return acquire_step.acquire(
            self.handle,
            position_label=position_label,
            acquisition_settings=acquisition_settings,
            acquisition_type=acquisition_type,
            output_folder=output_folder,
        )

    def save(self, label: str | None = None) -> list[dict]:
        """Read the waiting captures back from FLUOVIEW (only *label* when given) and write each as OME-Zarr; one record each."""
        return save_step.save_waiting(self.handle, label)

    def stop(self) -> dict:
        """Stop the imaging that is running, and see FLUOVIEW go idle."""
        self.handle.require_open()
        return checked(self.handle, changes.stop(self.handle.client), "stop")

    def live(self, on: bool = True) -> dict:
        """Start (``True``) or stop FLUOVIEW's live, repeating scan, as the Live button does."""
        self.handle.require_open()
        return checked(
            self.handle, changes.repeat_scan(self.handle.client, on), f"live({on})"
        )

    # --- the state ------------------------------------------------------------------------

    def state(self) -> dict:
        """``{"changeable", "observed"}``: what :meth:`set_state` can change, and what can only be read, with the unconfirmed changes."""
        return state_step.get_state(self.handle)

    def set_state(self, changeable: dict) -> dict:
        """Apply the changeable settings (see :meth:`state`); answers ``{"applied", "unconfirmed"}``."""
        return state_step.set_state(self.handle, {"changeable": changeable})


def checked(handle: Session, result: dict, what: str) -> dict:
    """A change's result, raising when it failed and recording it when it was never seen."""
    if not result.get("success"):
        raise RuntimeError(f"{what} failed: {result.get('message')}")
    if result.get("confirmed") is False:
        handle.note_unconfirmed(what, result)
    return result


def saved_data_location():
    """This microscope's set-up data location, or None when it was never set up; a damaged record stops the connection."""
    try:
        return data_location.data_location()
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            f"this microscope's saved data location could not be read ({exc}); set it again with "
            "driver_setup/data_location/notebooks/set_data_location.ipynb"
        ) from exc


#: What to run when no data location was set up, said in every refusal.
SET_UP = "set this microscope's data location once with driver_setup/data_location/notebooks/set_data_location.ipynb, or connect with an output_root"


@dataclass
class Session:
    """One connection's session: the RDK client and everything the driver remembers about it.

    ``origin`` is the zero point of the frame (FLUOVIEW's stage, focus motor
    and piezo readings, and the objective it was captured under); without a
    saved origin it is all zero, so the frame is FLUOVIEW's own coordinates.
    ``translations`` are the objective calibration's offsets by slot (None:
    declined; ``{}``: wanted but unusable, so objective changes refuse).
    ``unconfirmed`` lists every change this session sent that was accepted
    but never confirmed by a read-back, newest last. ``unsaved`` holds the
    captures taken whose files are not yet written.
    """

    client: Any
    connection: dict[str, Any]
    hash6: str
    fluoview_version: str | None = None
    origin: dict[str, Any] = field(default_factory=saved_origin.empty_origin)
    #: Where get_xyz last read the stage, with the drives' own readings and the objective.
    driven_to: dict[str, Any] | None = None
    position_counter: int = 0
    translations: dict[int, tuple[float, float, float]] | None = None
    orientation: Any = None
    orientation_info: dict | None = None
    calibration_info: dict | None = None
    data_location: Path | None = None
    escape_objective: bool = False
    #: The picture whose settings were last loaded with load_settings_from, this session.
    settings_loaded_from: str | None = None
    #: What one picture covers under each objective slot (image size and pixel size), learned from the
    #: pictures taken on this microscope; FLUOVIEW's settings do not report it. See driver_setup/frame.py.
    frames: dict[int, dict[str, Any]] = field(default_factory=dict)
    closed: bool = False
    unconfirmed: list[dict[str, Any]] = field(default_factory=list)
    unsaved: list[Any] = field(default_factory=list)

    def require_open(self) -> None:
        """Refuse to drive a closed session."""
        if self.closed:
            raise RuntimeError("session is disconnected")

    def note_unconfirmed(self, what: str, result: Any) -> None:
        """Record a change that was sent and accepted but never confirmed, say so in a warning, and carry on."""
        message = result.get("message") if isinstance(result, dict) else str(result)
        note = {
            "what": what,
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "message": message or "the read-back did not confirm the change",
        }
        self.unconfirmed.append(note)
        del self.unconfirmed[:-50]  # keep the newest fifty
        log.warning("unconfirmed, carrying on: %s: %s", what, note["message"])

    def output_root(self) -> Path:
        """The folder this session saves under: its own ``output_root``, else the set-up data location; raises saying how to set one up."""
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


def start(folder, logger_name, *, level=logging.INFO) -> Path | None:
    """Begin copying the driver's messages into ``<folder>/running_log.txt``; calling it again for the same file does nothing."""
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
        log.warning("the running log could not be opened at %s: %s", path, why)
        return None
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(LINE_FORMAT))
    package_logger.addHandler(handler)
    if package_logger.level == logging.NOTSET or package_logger.level > level:
        package_logger.setLevel(level)
    return path
