"""A ZEISS microscope driven through ZEN, from a notebook or a script: connect(), the Microscope, its Session and its running log.

``connect()`` opens the connection to the ZEN API Gateway, loads this
microscope's configuration and hands back a :class:`Microscope`. Its methods
are everything the driver can do: read and move the stage in the sample's
frame, change the objective and keep the sample in view, load a ZEN
experiment and change its z-stack, take a picture, save it, focus, and
describe the instrument::

    import zmart_drivers.zeiss.light_microscope.zen_api_grpc as zen

    scope = zen.connect(password="<the gateway's control token>", experiment="ZMART_Snap")
    scope.move_to(100.0, 250.0, 0.0)
    scope.acquire("A1")
    scope.save()
    scope.close()

Every method goes through the same parts as the controller's commands: the
limits are asked before anything moves, every change is read back, and a
change that was sent but never seen is recorded (``state()`` lists it) while
the work goes on. A request that is wrong raises ``ValueError``; a change
ZEN refused or that failed raises ``RuntimeError`` with the reason.

The session every call works in. A :class:`Session` is made when the driver
connects, and the ``Microscope`` keeps it: the open connection, the zero
point of the frame, the objective calibration and orientation, the loaded
experiment, where ZEN writes its images, the captures not yet saved, and the
changes that were sent but never confirmed.

The running log: one file in the configuration folder that records what the
driver sent, what came back and what it decided, for finding out later what
went wrong. It is renamed at about five megabytes, and the three newest old
files are kept, so it can never fill the disk.

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
from .primitives.gateway import client as gateway_client
from .procedures import absolute_coordinate_system, changes, keep_in_view, readings
from .procedures import acquire as acquire_step
from .procedures import state as state_step
from .procedures.absolute_coordinate_system import NO_ORIGIN
from .procedures.save_data import save as save_step
from .procedures.save_data.naming import run_hash

#: The driver's package: every part of the driver logs under this name.
DRIVER_PACKAGE = __package__

#: The connection settings used for any key the caller leaves out.
#:
#: The first seven are the ZMART Controller's connection words, which every
#: client shows when connecting. ``microscope`` names this instrument and
#: ``api_type`` how ZEN is reached; neither is needed to connect. ``host`` is
#: where the ZEN API Gateway listens (this computer, or the ZEN computer on
#: the network). ``client`` is the name this program is known by in the
#: running log. ``password`` is the gateway's control token; left empty, it
#: is read from ``zen_config_ini``, or from the gateway's own token file when
#: the gateway runs on this computer. ``config`` names the config to work
#: with, a ``config_YYYYMMDD_HHMM`` folder kept on this computer; empty, the
#: most recent. ``output_root`` is where a capture is saved when it is not
#: told a folder.
#:
#: Then the driver's own. ``port`` is the gateway's port. ``cert_file`` is the
#: gateway's root certificate, to check that the driver talks to the right
#: gateway; empty, the gateway's own folder is looked in. ``zen_config_ini``
#: is a ZEISS-style ``config.ini`` ([api] host, port, cert_file,
#: control-token), as ZEISS's own examples use; its values fill what is left
#: empty here. ``zen_images`` is the path under which this computer sees
#: ZEN's image folder, when ZEN runs on another computer. ``experiment`` is
#: the ZEN experiment loaded at connect. ``load_limits`` and
#: ``load_calibration`` load this microscope's limits and objective
#: calibration; ``load_origin`` loads the saved zero point (set it to False
#: only to start in ZEN's own coordinates, for example to save a new origin
#: when the saved one is damaged). ``calibration_name`` (not set by default)
#: picks a named calibration set.
DEFAULT_CONNECTION = {
    "microscope": "zeiss-zen",
    "api_type": "ZEN API (gRPC), zen_api 2025.10.1, ZEN 3.13",
    "host": "127.0.0.1",
    "client": "ZMART",
    "password": None,
    "config": None,
    "output_root": None,
    "port": gateway_client.DEFAULT_PORT,
    "cert_file": None,
    "zen_config_ini": None,
    "zen_images": None,
    "experiment": None,
    "load_limits": True,
    "load_calibration": True,
    "load_origin": True,
}


def connect(**connection: Any) -> Microscope:
    """Open ZEN and hand back the connected microscope; see :data:`DEFAULT_CONNECTION` for the keys."""
    return Microscope(connection)


def named_configurations() -> list[str]:
    """The configs kept on this computer, by folder name, the most recent first."""
    return MACHINE.configurations()


def checked_connection(connection: dict[str, Any] | None) -> dict[str, Any]:
    """*connection* over :data:`DEFAULT_CONNECTION`, refused before ZEN is touched when it cannot be honoured.

    A key left empty takes the value of ``zen_config_ini`` when one is given,
    else the default. An unknown key, a port that is not a number and a
    config that is not kept here are refused rather than ignored, so a
    connection never silently goes somewhere other than what was typed.
    """
    given = {
        key: value
        for key, value in (connection or {}).items()
        if value not in (None, "")
    }
    unknown = sorted(set(given) - set(DEFAULT_CONNECTION) - {"calibration_name"})
    if unknown:
        raise ValueError(
            f"unknown connection keys {unknown}; the keys are {sorted(DEFAULT_CONNECTION)} and calibration_name"
        )
    from_ini: dict[str, Any] = {}
    if given.get("zen_config_ini"):
        ini = gateway_client.read_config_ini(given["zen_config_ini"])
        from_ini = {
            "host": ini["host"],
            "port": ini["port"],
            "cert_file": ini["cert_file"],
            "password": ini["token"],
        }
        from_ini = {
            key: value for key, value in from_ini.items() if value not in (None, "")
        }
    connection = {**DEFAULT_CONNECTION, **from_ini, **given}
    try:
        connection["port"] = int(connection["port"])
    except (TypeError, ValueError) as why:
        raise ValueError(
            f"port must be a whole number, such as 5002, not {connection['port']!r}"
        ) from why
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


def control_token(connection: dict[str, Any]) -> str | None:
    """The gateway's control token: the connection's password, else the gateway's token file on this computer."""
    if connection.get("password"):
        return str(connection["password"]).strip()
    token_file = Path(gateway_client.TOKEN_FILE)
    if connection["host"] in ("127.0.0.1", "localhost", "::1") and token_file.is_file():
        return token_file.read_text(encoding="utf-8").strip() or None
    return None


def open_connection(connection: dict[str, Any]):
    """The client connected to the gateway named by *connection*. The offline tests replace this function."""
    return gateway_client.open_gateway(
        connection["host"],
        connection["port"],
        token=control_token(connection),
        cert_file=connection.get("cert_file"),
    )


class Microscope:
    """One connected ZEISS microscope: the ZEN session and everything loaded for it."""

    def __init__(self, connection: dict[str, Any] | None = None) -> None:
        """Open ZEN and load this microscope's configuration.

        Making the microscope is the connection: it opens the gateway, loads
        the limits (which switch on the limits gate), the saved origin, the
        orientation and the objective calibration, checks that ZEN answers,
        and loads the experiment named in the connection. Raises when any of
        these fail, and leaves nothing open behind.

        The saved origin is read before ZEN is contacted. A saved origin that
        exists but cannot be read stops the connection, because quietly
        falling back to ZEN's coordinates would put every later move
        somewhere other than intended.
        """
        connection = checked_connection(connection)
        use_configuration(connection["config"])
        origin = saved_origin.load_saved_origin() if connection["load_origin"] else None
        location = saved_data_location()
        MACHINE.ensure_layout()
        start(MACHINE.snapshot_root() / "logs", DRIVER_PACKAGE)
        client = open_connection(connection)
        try:
            if not readings.ping(client):
                raise RuntimeError(
                    "ZEN does not answer: the stage could not be read after connecting"
                )
            # The limits are loaded first, so nothing can move before they govern.
            handshake.connect_handshake(client, load=connection["load_limits"])
            orientation, orientation_info, frame = load_rig_orientation()
            translations, calibration_info = load_objective_calibration(
                connection.get("calibration_name"),
                enabled=connection["load_calibration"],
            )
            handle = Session(
                client=client,
                connection={
                    key: value for key, value in connection.items() if key != "password"
                },
                hash6=run_hash(),
                translations=translations,
                orientation=orientation,
                orientation_info=orientation_info,
                calibration_info=calibration_info,
                frame=frame,
                zen_images=connection.get("zen_images"),
            )
            if origin is not None:
                handle.origin = origin
            handle.data_location = location
            handle.image_output_path = readings.get_image_output_path(client)
            if connection.get("experiment"):
                state_step.set_state(
                    handle, {"changeable": {"experiment": connection["experiment"]}}
                )
        except BaseException:
            gate.uninstall(client)
            client.close()
            raise
        self.handle = handle
        log.info("connected to ZEN at %s:%s", connection["host"], connection["port"])

    @property
    def client(self):
        """The connected ZEN client, for the setup notebooks."""
        return self.handle.client

    def close(self) -> None:
        """Close the connection and forget what was loaded for it. A second call does nothing."""
        if self.handle.closed:
            return
        gate.uninstall(self.handle.client)
        self.handle.client.close()
        self.handle.closed = True

    # --- the stage ----------------------------------------------------------------------

    def position(self, with_actuators: dict | None = None) -> dict:
        """Where the stage is, per axis, in micrometres from the saved origin.

        Each of ``x``, ``y`` and ``z`` holds its ``position``, its ``unit``,
        its motor with ZEN's own reading (``actuator_readings``) and the range
        the stage may travel (``canvas``). ``z`` is the focus drive.
        """
        return absolute_coordinate_system.get_xyz(
            self.handle, with_actuators=with_actuators
        )

    def move_to(
        self, x: float, y: float, z: float, with_actuators: dict | None = None
    ) -> dict:
        """Move to ``x``, ``y``, ``z`` (micrometres from the origin) and answer as :meth:`position`, read back.

        The limits refuse a position outside the travel before anything moves.
        """
        return absolute_coordinate_system.set_xyz(
            self.handle, x, y, z, with_actuators=with_actuators
        )

    def motors(self) -> dict:
        """The motor of each axis, and the one used to move it: ``{"options": [...], "default": ...}``."""
        return absolute_coordinate_system.get_actuators(self.handle)

    def set_origin(self) -> dict:
        """Make the current stage position the zero point of the frame, and save it for later connections."""
        return origin_step.set_origin(self.handle)

    # --- objectives and experiments ----------------------------------------------------

    def objectives(self) -> list[dict]:
        """The objectives on the changer: ``position`` (from 1), ``name``, ``magnification``, ``na``."""
        self.handle.require_open()
        return readings.get_objectives(self.handle.client) or []

    def change_objective(
        self,
        position: int | None = None,
        name: str | None = None,
        magnification: float | None = None,
    ) -> dict:
        """Change the objective, by its position, name or magnification, and keep the same spot in view.

        The stage and the focus are moved by the measured offset between the
        two objectives. A pair the calibration does not cover is refused.
        """
        self.handle.require_open()
        result = keep_in_view.set_objective(
            self.handle, position=position, name=name, magnification=magnification
        )
        return checked(self.handle, result, "change_objective")

    def experiments(self) -> dict:
        """The experiments ZEN can load, and the one loaded: ``{"loaded", "experiments"}``."""
        self.handle.require_open()
        return {
            "loaded": (self.handle.experiment or {}).get("name"),
            "experiments": readings.get_experiments(self.handle.client) or [],
        }

    def load_experiment(self, name: str) -> dict:
        """Load the ZEN experiment *name*: it holds the imaging settings every capture uses. Answers ``{"applied", "unconfirmed"}``."""
        return state_step.set_state(self.handle, {"changeable": {"experiment": name}})

    def z_stack(self) -> dict:
        """The loaded experiment's z-stack: first and last slice and step, in micrometres from the origin's focus."""
        experiment = self._loaded()
        stack = readings.get_zstack(self.handle.client, experiment["id"])
        if stack is None:
            raise RuntimeError("ZEN did not report the experiment's z-stack")
        if stack["is_zstack"]:
            offset = self.handle.origin["z_um"]
            stack = {
                **stack,
                "first_um": stack["first_um"] - offset,
                "last_um": stack["last_um"] - offset,
            }
        return stack

    def set_z_stack(
        self,
        first_um: float | None = None,
        last_um: float | None = None,
        interval_um: float | None = None,
    ) -> dict:
        """Change the loaded experiment's z-stack; the ends are micrometres from the origin's focus, read back.

        A value left None keeps what the experiment has. The limits check the
        ends against the focus travel before anything is sent, and the change
        lasts while the experiment stays loaded (ZEN does not save it to the
        experiment's file).
        """
        experiment = self._loaded()
        offset = self.handle.origin["z_um"]
        if first_um is not None:
            first_um = changes.checked_number(first_um, "first_um") + offset
        if last_um is not None:
            last_um = changes.checked_number(last_um, "last_um") + offset
        result = changes.set_z_stack(
            self.handle.client,
            experiment["id"],
            experiment["name"],
            first_um=first_um,
            last_um=last_um,
            interval_um=interval_um,
        )
        return checked(self.handle, result, "set_z_stack")

    def _loaded(self) -> dict:
        self.handle.require_open()
        if not self.handle.experiment:
            raise RuntimeError(
                "no experiment is loaded; load one with load_experiment(name)"
            )
        return self.handle.experiment

    # --- pictures -----------------------------------------------------------------------

    def acquisition_settings(self) -> dict:
        """How a capture can be triggered: ``full`` (the whole loaded experiment) or ``snap`` (one picture)."""
        return acquire_step.get_acquisition_settings(self.handle)

    def acquire(
        self,
        position_label: str | None = None,
        *,
        acquisition_type: str = "acquisition",
        output_folder: str | None = None,
        acquisition_settings: dict | None = None,
    ) -> dict:
        """Run the loaded experiment once; ZEN writes the CZI, which waits in ZEN's folder until :meth:`save`.

        The capture will be named after *acquisition_type* and
        *position_label*, and saved in *output_folder*, or else in this
        session's folder under the data location.
        """
        return acquire_step.acquire(
            self.handle,
            position_label=position_label,
            acquisition_settings=acquisition_settings,
            acquisition_type=acquisition_type,
            output_folder=output_folder,
        )

    def save(self, label: str | None = None) -> list[dict]:
        """Save the captures that are waiting (only *label* when given), each as one OME-Zarr store; one answer each.

        Each answer lists the files written (the store, its projection for a
        z-stack, ZEN's CZI and ZMART's account of the capture) and the planes
        the store holds, in the controller's words.
        """
        return save_step.save_waiting(self.handle, label)

    # --- focusing and stopping ------------------------------------------------------------

    def autofocus(self, timeout_s: float | None = None) -> dict:
        """Run ZEN's software autofocus with the loaded experiment's settings; ``z_um`` is the focus found, from the origin."""
        experiment = self._loaded()
        result = checked(
            self.handle,
            changes.autofocus(
                self.handle.client,
                experiment["id"],
                experiment["name"],
                timeout_s=timeout_s,
            ),
            "autofocus",
        )
        return self._with_frame_z(result)

    def find_surface(self) -> dict:
        """Have the Definite Focus find the coverslip; ``z_um`` is where it ended, from the origin."""
        self.handle.require_open()
        return self._with_frame_z(
            checked(
                self.handle, changes.find_surface(self.handle.client), "find_surface"
            )
        )

    def store_focus(self) -> dict:
        """Have the Definite Focus remember the focus where it stands now."""
        self.handle.require_open()
        return checked(
            self.handle, changes.store_focus(self.handle.client), "store_focus"
        )

    def recall_focus(self) -> dict:
        """Have the Definite Focus go back to the focus it remembered; ``z_um`` is where it ended, from the origin."""
        self.handle.require_open()
        return self._with_frame_z(
            checked(
                self.handle, changes.recall_focus(self.handle.client), "recall_focus"
            )
        )

    def stop(self) -> dict:
        """Stop whatever ZEN is acquiring."""
        self.handle.require_open()
        return checked(self.handle, changes.stop(self.handle.client), "stop")

    def _with_frame_z(self, result: dict) -> dict:
        focus = result.get("focus_um")
        result["z_um"] = None if focus is None else focus - self.handle.origin["z_um"]
        return result

    # --- the state ------------------------------------------------------------------------

    def state(self) -> dict:
        """``{"changeable", "observed"}``: the experiment and the objective, and what can only be read."""
        return state_step.get_state(self.handle)


def checked(handle: Session, result: dict, what: str) -> dict:
    """A change's result, raising when it failed and recording it when it was never seen."""
    if not result.get("success"):
        raise RuntimeError(f"{what} failed: {result.get('message')}")
    if result.get("confirmed") is False:
        handle.note_unconfirmed(what, result)
    return result


def saved_data_location():
    """This microscope's set-up data location, or None when it was never set up.

    A record that cannot be read stops the connection with how to fix it,
    rather than being taken for "not set up".
    """
    try:
        return data_location.data_location()
    except (OSError, ValueError) as why:
        raise RuntimeError(
            f"this microscope's saved data location could not be read ({why}); set it again with "
            "driver_setup/data_location/notebooks/set_data_location.ipynb"
        ) from why


log = logging.getLogger(__name__)

#: What to run when no data location was set up, said in every refusal.
SET_UP = (
    "set this microscope's data location once with driver_setup/data_location/notebooks/set_data_location.ipynb, "
    "or connect with an output_root"
)


@dataclass
class Session:
    """One connection's session: the ZEN client and everything the driver remembers about it.

    Everything here is loaded once, at connect, so one connection carries one
    consistent picture of the microscope for its whole life; a new
    measurement takes effect on the next connection.

    Attributes:
        client: The connected ZEN client.
        connection: The connection settings this session was opened with, without the token.
        hash6: Six characters naming this session, in folder names and records.
        origin: The zero point of the frame (ZEN coordinates of x, y and the
            focus, and the objective it was saved under). All zero when none
            was saved: the frame is then ZEN's own coordinates.
        translations: The objective offsets from the calibration, by changer
            position; ``{}`` when it could not be loaded (changes across
            objectives are then refused), None when it was switched off.
        orientation, orientation_info: The measured turn of the picture, and what was loaded.
        calibration_info: What calibration was loaded.
        frame: The image size and pixel size the orientation setup measured; None until measured.
        last_frame: The same, from this session's last capture, which is exact.
        experiment: The loaded ZEN experiment, ``{"name", "id"}``, or None.
        image_output_path: Where ZEN writes its CZI files, as ZEN names the folder.
        zen_images: Where this computer sees that folder, when ZEN runs elsewhere.
        data_location: This microscope's data location, or None when never set up.
        driven_to: Where the stage was last read, in the frame, with the objective.
        unconfirmed: The changes sent and accepted but never seen, newest last.
        unsaved: The captures taken whose files are not yet saved.
        closed: Set by ``close()``; every call refuses a closed session.
    """

    client: Any
    connection: dict[str, Any]
    hash6: str
    origin: dict[str, Any] = field(default_factory=lambda: dict(NO_ORIGIN))
    translations: dict | None = None
    orientation: Any = None
    orientation_info: dict | None = None
    calibration_info: dict | None = None
    frame: dict | None = None
    last_frame: dict | None = None
    experiment: dict | None = None
    image_output_path: str | None = None
    zen_images: str | None = None
    data_location: Path | None = None
    driven_to: dict | None = None
    position_counter: int = 0
    closed: bool = False
    unconfirmed: list[dict[str, Any]] = field(default_factory=list)
    unsaved: list[Any] = field(default_factory=list)

    def require_open(self) -> None:
        """Refuse to work with a closed session."""
        if self.closed:
            raise RuntimeError("the session is closed; connect again")

    def note_unconfirmed(self, what: str, result: Any) -> None:
        """Record a change that was sent and accepted but never confirmed, and carry on.

        The change is not known to have failed, only not known to have
        happened, so it is written down here, said in a warning, and the
        command goes on. The workflow sees the list in ``state()``.
        """
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
        """The folder this session saves under: its own ``output_root``, else the set-up data location.

        Raises ``RuntimeError`` saying how to set one up when neither is
        there: data saved somewhere nobody chose is data lost.
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


def start(folder, logger_name, *, level=logging.INFO) -> Path | None:
    """Begin copying the driver's messages into ``<folder>/running_log.txt``; the file's path, or None.

    Calling this again for the same file does nothing, so each connect can
    call it. A file that cannot be opened is noted once and never stops the
    experiment.
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
        log.warning("the running log could not be opened at %s: %s", path, why)
        return None
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(LINE_FORMAT))
    package_logger.addHandler(handler)
    if package_logger.level == logging.NOTSET or package_logger.level > level:
        package_logger.setLevel(level)
    return path
