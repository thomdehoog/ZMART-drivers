"""A Nikon microscope driven directly, from a notebook or a script: connect(), the Microscope, its Session and its running log.

``connect()`` reaches NIS-Elements through the bridge, loads this
microscope's settings, and hands back a :class:`Microscope`. Its methods are
everything the driver can do: read and move the stage in the sample's
coordinates, choose the objective and the optical configuration, take a
picture and save it, and describe the microscope::

    import zmart_drivers.nikon.nis_elements_ar7 as nis_elements_ar7

    scope = nis_elements_ar7.connect(microscope="nikon-ti2-zmb")
    scope.move_to(100.0, 250.0, 0.0)
    scope.select_optical_configuration("GFP")
    scope.acquire("A1", output_folder=r"D:\\ZMART-data\\run-1")
    scope.close()

Before connecting, NIS-Elements must be running with ``start_bridge.mac``
started in it (written once by ``driver_setup/nis_bridge``).

Every method goes through the same parts as the controller's commands: the
limits are asked before anything moves, every change is read back, and a
change that was sent but never seen is recorded (``state()`` lists it) while
the work goes on. A request that is wrong raises ``ValueError``; a change
NIS refused, or that failed, raises ``RuntimeError`` with the reason.

The :class:`Session` is what the driver remembers about one connection: the
bridge's client, the origin, the orientation, the data location, and the
changes that were never confirmed. The running log copies every message of
the driver into ``logs/running_log.txt`` in this microscope's settings, for
finding out later what happened; it is renamed at about five megabytes, and
the three newest old files are kept.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
Adapted from the Leica driver's ``methods.py``.
"""

from __future__ import annotations

import logging
import socket
from dataclasses import dataclass, field
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from .dispatcher import limits as gate
from .driver_setup import data_location as data_location_item
from .driver_setup import store
from .driver_setup.limits import handshake
from .driver_setup.nis_bridge import macro
from .driver_setup.orientation import load_rig_orientation
from .driver_setup.origin import saved_origin
from .driver_setup.origin import set_origin as origin_step
from .primitives.nis_python import server
from .primitives.nis_python.client import NisClient
from .procedures import absolute_coordinate_system, changes, readings
from .procedures import acquire as acquire_step
from .procedures import state as state_step
from .procedures.save_data import save as save_step

log = logging.getLogger(__name__)

#: The driver's package: every part of the driver logs under this name.
DRIVER_PACKAGE = __package__

#: The connection settings used for any key the caller leaves out.
#: The first five are the ZMART Controller's connection words. ``microscope``
#: names this instrument; its settings are kept under that name. ``api_type``
#: says how NIS is reached. ``host`` is where NIS runs: the bridge answers on
#: this computer only, so any other host is refused. NIS asks no ``password``.
#: ``config`` names a config kept on this computer (``config_YYYYMMDD_HHMM``);
#: empty, the most recent, or none. ``output_root`` is where a capture is saved
#: when it is not told a folder; empty, the data location set up once.
#: Then the driver's own: ``port`` is where the bridge listens, ``timeout_s``
#: how long a reading may wait for NIS, and ``load_limits`` and ``load_origin``
#: load this microscope's limits and origin (leave both on for normal work).
DEFAULT_CONNECTION = {
    "microscope": store.DEFAULT_MICROSCOPE,
    "api_type": "NIS-Elements AR 7, macro functions through its own Python (the ZMART bridge)",
    "host": "localhost",
    "password": None,
    "config": None,
    "output_root": None,
    "port": server.PORT,
    "timeout_s": 10.0,
    "load_limits": True,
    "load_origin": True,
}

#: The hosts that name this computer, besides its own name.
THIS_COMPUTER = ("localhost", "127.0.0.1", "::1")

#: What to do when no folder to save in is set up, said in every refusal.
SET_UP = (
    "set this microscope's data location once with driver_setup/data_location/notebooks/set_data_location.ipynb, "
    "or connect with an output_root, or give acquire an output_folder"
)


def connect(**connection: Any) -> Microscope:
    """Reach NIS-Elements through the bridge and hand back the connected microscope; see :data:`DEFAULT_CONNECTION`."""
    return Microscope(connection)


def named_configurations(microscope: str | None = None) -> list[str]:
    """The configs kept on this computer for *microscope*, the most recent first."""
    return store.MachineProfile(
        microscope=store.check_microscope_name(microscope or store.DEFAULT_MICROSCOPE)
    ).configurations()


def checked_connection(connection: dict[str, Any] | None) -> dict[str, Any]:
    """*connection* over :data:`DEFAULT_CONNECTION`, refused before NIS is reached when it cannot be honoured.

    An empty field takes the default. A host that is not this computer, a
    microscope name that cannot be a folder's name, and a config that is not
    kept here are refused rather than ignored.
    """
    given = {
        key: value
        for key, value in (connection or {}).items()
        if value not in (None, "")
    }
    unknown = sorted(set(given) - set(DEFAULT_CONNECTION))
    if unknown:
        raise ValueError(
            f"unknown connection settings {unknown}; the settings are {sorted(DEFAULT_CONNECTION)}"
        )
    connection = {**DEFAULT_CONNECTION, **given}
    host = str(connection["host"]).strip()
    if host.lower() not in {*THIS_COMPUTER, socket.gethostname().lower()}:
        raise ValueError(
            f"host {host!r}: the bridge answers on this computer only; leave host empty or use localhost"
        )
    connection["host"] = "127.0.0.1"
    connection["microscope"] = store.check_microscope_name(connection["microscope"])
    kept = named_configurations(connection["microscope"])
    if connection["config"] is None:
        connection["config"] = kept[0] if kept else None
    elif connection["config"] not in kept:
        raise ValueError(
            f"config {connection['config']!r} is not kept on this computer; choose one of {kept}, or leave it empty"
        )
    return connection


class Microscope:
    """One connected Nikon microscope: NIS-Elements through the bridge, and everything loaded for it."""

    def __init__(self, connection: dict[str, Any] | None = None) -> None:
        """Connect: reach the bridge, load this microscope's settings, install its limits.

        Raises ``ValueError`` for a connection that cannot be honoured, and
        ``RuntimeError`` when the bridge cannot be reached, is another version,
        or a saved setting is damaged. A saved origin that cannot be read
        stops the connection, because falling back to stage coordinates would
        put every later move somewhere other than intended.
        """
        connection = checked_connection(connection)
        machine = store.use_microscope(connection["microscope"])
        store.use_configuration(connection["config"])
        origin = (
            saved_origin.load_saved_origin(machine)
            if connection["load_origin"]
            else None
        )
        location = saved_data_location(machine)
        machine.ensure_layout()
        start_running_log(machine.logs_folder(), DRIVER_PACKAGE)

        client = NisClient(
            connection["host"],
            connection["port"],
            key_file=macro.files()["key"],
            timeout_s=float(connection["timeout_s"]),
        )
        client.connect()
        # The limits are installed before anything else, so nothing can move before they govern.
        handshake.connect_handshake(
            client, machine=machine, load=connection["load_limits"]
        )
        orientation, orientation_info = load_rig_orientation(machine)
        self.handle = Session(
            client=client,
            connection={
                key: value for key, value in connection.items() if key != "password"
            },
            hash6=datetime.now(UTC).strftime("%y%m%d%H%M%S"),
            nis_version=readings.version(client),
            origin=dict(origin) if origin else dict(saved_origin.NO_ORIGIN),
            orientation=orientation,
            orientation_info=orientation_info,
            data_location=location,
        )
        log.info(
            "connected to NIS-Elements %s as %s",
            self.handle.nis_version,
            connection["microscope"],
        )

    @property
    def client(self) -> NisClient:
        """The bridge's client, for the setup notebooks. It reads; it cannot send a change around the limits."""
        return self.handle.client

    def close(self) -> None:
        """Close the session: every later call is refused. The bridge keeps running in NIS. A second call does nothing."""
        if self.handle.closed:
            return
        try:
            root = self.handle.output_root()
        except RuntimeError:
            root = None
        if root is not None:
            left = save_step.staged(root)
            if left:
                log.warning(
                    "%d captures are still waiting to be saved in %s: %s",
                    len(left),
                    root,
                    [p.name for p in left],
                )
        gate.uninstall(self.handle.client)
        self.handle.client.close()
        self.handle.closed = True

    def stop_bridge(self) -> None:
        """Ask the bridge in NIS to stop (its macro then ends), and close this session."""
        self.handle.require_open()
        self.handle.client.shutdown()
        self.close()

    # --- the stage -------------------------------------------------------------------------

    def position(self, with_actuators: dict | None = None) -> dict:
        """Where the stage is, per axis, in micrometres from the saved origin, with NIS's own readings and the canvas."""
        return absolute_coordinate_system.get_xyz(
            self.handle, with_actuators=with_actuators
        )

    def move_to(
        self, x: float, y: float, z: float, with_actuators: dict | None = None
    ) -> dict:
        """Move to x, y, z (micrometres from the origin), and answer as :meth:`position`, read back after the move."""
        return absolute_coordinate_system.set_xyz(
            self.handle, x, y, z, with_actuators=with_actuators
        )

    def motors(self) -> dict:
        """The motors of each axis, and the one used to move it."""
        return absolute_coordinate_system.get_actuators(self.handle)

    def set_origin(self) -> dict:
        """Make where the stage stands now the zero point of the coordinates, and save it for later connections."""
        return origin_step.set_origin(self.handle)

    # --- objectives and optical configurations ---------------------------------------------------

    def objectives(self) -> dict:
        """The objectives by nosepiece position, and the one in place: ``{"position", "objectives"}``."""
        self.handle.require_open()
        turret = readings.nosepiece(self.handle.client)
        return {"position": turret["position"], "objectives": turret["objectives"]}

    def change_objective(self, position: int) -> dict:
        """Turn the nosepiece to *position* (counted from 1) and read it back; ``{"applied", "unconfirmed"}``.

        The stage is not moved to keep the sample in view: this driver has no
        objective calibration, so positions shift by the difference between
        the two objectives.
        """
        return state_step.set_state(self.handle, {"objective_position": position})

    def optical_configurations(self) -> dict:
        """The optical configurations NIS has, and the selected one: ``{"selected", "configurations"}``."""
        self.handle.require_open()
        return {
            "selected": readings.selected_optical_configuration(self.handle.client),
            "configurations": readings.optical_configurations(self.handle.client),
        }

    def select_optical_configuration(self, name: str) -> dict:
        """Select the optical configuration *name* and read it back; ``{"applied", "unconfirmed"}``."""
        return state_step.set_state(self.handle, {"optical_configuration": name})

    # --- pictures -------------------------------------------------------------------------

    def acquisition_settings(self) -> dict:
        """How a capture is triggered: ``snap``, one picture with the settings as they are."""
        return acquire_step.get_acquisition_settings(self.handle)

    def acquire(
        self,
        position_label: str | None = None,
        *,
        acquisition_type: str = "acquisition",
        output_folder: str | None = None,
        acquisition_settings: dict | None = None,
    ) -> dict:
        """Take one picture and save it as OME-Zarr; its record: ``files``, ``planes``, ``store``, ``log``."""
        return acquire_step.acquire(
            self.handle,
            position_label=position_label,
            acquisition_settings=acquisition_settings,
            acquisition_type=acquisition_type,
            output_folder=output_folder,
        )

    def save_staged(self, output_folder: str | None = None) -> dict:
        """Save the captures still waiting in *output_folder* (this session's when None); ``{"saved", "failed"}``."""
        self.handle.require_open()
        return save_step.save_staged(
            Path(output_folder) if output_folder else self.handle.output_root()
        )

    # --- the state ------------------------------------------------------------------------

    def state(self) -> dict:
        """``{"changeable", "observed"}``: the objective and optical configuration, then what can only be read."""
        return state_step.get_state(self.handle)

    def set_state(self, changeable: dict) -> dict:
        """Change several settings together, named as ``state()["changeable"]`` names them.

        For example ``{"objective_position": 2, "optical_configuration": "GFP",
        "shutter/EPI": 1}``; any may be left out. The objective is changed
        first, because an optical configuration may bring an objective of its
        own and should have the last word; the hardware settings follow, the
        light last. Answers ``{"applied", "unconfirmed"}``.
        """
        return state_step.set_state(self.handle, changeable)

    # --- the hardware settings -----------------------------------------------------------

    def devices(self) -> dict:
        """Every hardware setting the driver knows, and which of them this microscope has.

        Each entry says what the setting is (``says``), its kind (``switch``:
        1 or 0; ``choice``: a position from 1; ``number``), its unit, whether
        it is ``guarded`` (refused until this microscope's limits allow it),
        and its ``devices``: each one NIS reports, with its value now and, for
        a choice, how many positions it has. A setting with no devices is not
        on this microscope.
        """
        self.handle.require_open()
        return readings.devices(self.handle.client)

    def device(self, name: str):
        """One hardware setting's value now, by its name: ``device("shutter/EPI")``, ``device("light_path")``."""
        self.handle.require_open()
        key, instance = changes.split_device_name(name)
        return readings.device(self.handle.client, key, instance)

    def set_device(self, name: str, value) -> dict:
        """Set one hardware setting and read it back: ``set_device("shutter/EPI", 1)``; ``{"applied", "unconfirmed"}``.

        ``ValueError`` when the setting or its device is not there, or the
        value is not one it takes; ``RuntimeError`` when the limits or NIS
        refused it.
        """
        if value is None:
            raise ValueError(f"set_device({name!r}) needs a value")
        return state_step.set_state(self.handle, {name: value})


def saved_data_location(machine: store.MachineProfile) -> Path | None:
    """This microscope's data location, or None when it was never set up; ``RuntimeError`` when it is damaged."""
    try:
        return data_location_item.data_location(machine)
    except (OSError, ValueError) as why:
        raise RuntimeError(
            f"this microscope's saved data location could not be read ({why}); set it again with "
            "driver_setup/data_location/notebooks/set_data_location.ipynb"
        ) from why


@dataclass
class Session:
    """One connection's session: the bridge's client, and everything the driver remembers about it.

    ``origin`` is the zero point of the coordinates, in NIS's own stage
    micrometres, with the objective it was saved under (all zero, and no
    objective, when none was saved). ``orientation`` is the turn applied to
    every saved picture. ``unconfirmed`` lists every change that was sent and
    accepted but never seen to take effect, newest last; ``get_state``
    reports it.
    """

    client: Any
    connection: dict[str, Any]
    hash6: str
    nis_version: str | None = None
    origin: dict[str, Any] = field(default_factory=lambda: dict(saved_origin.NO_ORIGIN))
    orientation: Any = None
    orientation_info: dict | None = None
    data_location: Path | None = None
    position_counter: int = 0
    closed: bool = False
    unconfirmed: list[dict[str, Any]] = field(default_factory=list)

    def require_open(self) -> None:
        """Refuse to work with a closed session."""
        if self.closed:
            raise RuntimeError("this session is closed; connect again")

    def note_unconfirmed(self, what: str, result: Any) -> None:
        """Record a change that was sent and accepted but never seen to take effect, and carry on."""
        message = result.get("message") if isinstance(result, dict) else str(result)
        note = {
            "what": what,
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "message": message or "not seen to take effect",
        }
        self.unconfirmed.append(note)
        del self.unconfirmed[:-50]  # keep the newest fifty
        log.warning("unconfirmed, carrying on: %s: %s", what, note["message"])

    def output_root(self) -> Path:
        """The folder this session saves in: its own ``output_root``, else the data location; ``RuntimeError`` when neither."""
        root = self.connection.get("output_root") or self.data_location
        if not root:
            raise RuntimeError(
                f"this microscope does not know where to save its pictures: {SET_UP}"
            )
        return Path(root).expanduser().resolve()


RUNNING_LOG = "running_log.txt"
RUNNING_LOG_BYTES = 5_000_000
RUNNING_LOG_OLD_FILES = 3


def start_running_log(
    folder: Path, logger_name: str, *, level: int = logging.INFO
) -> Path | None:
    """Begin copying the driver's messages into ``<folder>/running_log.txt``; its path, or None when it cannot be opened.

    Calling it again for the same file does nothing, so each connection can
    call it. A problem with the file is noted once and never stops the work.
    """
    path = Path(folder) / RUNNING_LOG
    package_logger = logging.getLogger(logger_name)
    for handler in package_logger.handlers:
        if getattr(handler, "baseFilename", None) == str(path.resolve()):
            return path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path,
            maxBytes=RUNNING_LOG_BYTES,
            backupCount=RUNNING_LOG_OLD_FILES,
            encoding="utf-8",
        )
    except OSError as why:
        log.warning("the running log could not be opened at %s: %s", path, why)
        return None
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter("%(asctime)s  %(levelname)-7s  %(name)s  %(message)s")
    )
    package_logger.addHandler(handler)
    if package_logger.level == logging.NOTSET or package_logger.level > level:
        package_logger.setLevel(level)
    return path
