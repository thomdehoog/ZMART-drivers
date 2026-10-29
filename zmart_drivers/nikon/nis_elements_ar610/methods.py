"""The Nikon driven directly, from a notebook or a script: connect(), the Microscope, its Session and its running log.

``connect()`` reaches NIS-Elements through the bridge, loads this
microscope's settings and hands back a :class:`Microscope`. Its methods are
everything the driver can do: read and move the stage in the sample's frame,
select a channel, set the exposure, change the objective, focus, set the
lights, lasers, filters, shutters, light path and camera, edit the saved
optical configurations, take a picture, save it, and describe the
instrument::

    import zmart_drivers.nikon.nis_elements_ar610 as nis_elements

    scope = nis_elements.connect()
    scope.move_to(100.0, 250.0, 0.0)
    scope.select_channel("DAPI")
    scope.set_exposure(50.0)
    scope.acquire("A1")
    scope.save()
    scope.close()

Every method goes through the same parts as the controller's commands do: the
limits are asked before anything moves, every change is read back where
NIS-Elements can report it, and a change that was sent but never seen is
recorded (``state()`` lists it) while the work goes on. A request that is
wrong raises ``ValueError``; a change NIS-Elements refused or that failed
raises ``RuntimeError`` with the reason.

The :class:`Session` is made when the driver connects, and the ``Microscope``
keeps it. It holds the open bridge connection and everything the driver
remembers about this session: the origin, the objective calibration, the
orientation, the data location, the channel it selected (NIS cannot report
it), the captures not yet saved, and the changes it could not confirm.

The running log is one file in the settings folder that records what the
driver sent, what came back and what it decided, for finding out later what
went wrong. It is renamed at about five megabytes, and the three newest old
files are kept, so it can never fill the disk.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
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
from .driver_setup import data_location
from .driver_setup.limits import handshake
from .driver_setup.optical_calibration.model import load_objective_calibration
from .driver_setup.orientation import load_rig_orientation
from .driver_setup.origin import saved_origin
from .driver_setup.origin import set_origin as origin_step
from .driver_setup.store import MACHINE, use_configuration
from .primitives.dll import client as bridge_client
from .procedures import absolute_coordinate_system, changes, keep_in_view, readings
from .procedures import acquire as acquire_step
from .procedures import devices as devices_step
from .procedures import focus as focus_step
from .procedures import state as state_step
from .procedures.save_data import save_waiting as save_step
from .procedures.save_data.naming import run_hash

log = logging.getLogger(__name__)

#: The driver's package: every part of the driver logs under this name.
DRIVER_PACKAGE = __package__

#: The connection settings used for any key the caller leaves out.
#: The first seven are the ZMART Controller's connection words, which every
#: client shows when connecting. ``microscope`` names this instrument and
#: ``api_type`` how NIS-Elements is reached; neither is needed to connect.
#: ``host`` is where the bridge listens: it answers on this computer only,
#: so any other host is refused. The bridge takes no ``client`` name and no
#: ``password``. ``config`` names the config to work with, a
#: ``config_YYYYMMDD_HHMM`` folder kept on this computer; empty, the most
#: recent. ``output_root`` is where a capture is saved when it is not told
#: a folder. Then the driver's own: ``port``, where the bridge listens;
#: ``request_timeout_s``, how long an ordinary request may take;
#: ``load_limits``, ``load_calibration`` and ``load_origin`` load this
#: microscope's limits, objective calibration and origin at connect (leave
#: all three on for normal work; ``load_origin=False`` starts in plain stage
#: coordinates, for example to save a new origin when the saved one is damaged).
DEFAULT_CONNECTION = {
    "microscope": "ti2",
    "api_type": "NIS-Elements AR 6.10, through the nis-bridge (DLL and macro)",
    "host": bridge_client.DEFAULT_HOST,
    "client": None,
    "password": None,
    "config": None,
    "output_root": None,
    "port": bridge_client.DEFAULT_PORT,
    "request_timeout_s": bridge_client.DEFAULT_TIMEOUT_S,
    "load_limits": True,
    "load_calibration": True,
    "load_origin": True,
}

#: The hosts that name this computer, besides its own name.
THIS_COMPUTER = ("localhost", "127.0.0.1", "::1")

#: What to run when no data location was set up, said in every refusal.
SET_UP = (
    "set this microscope's data location once with "
    "driver_setup/data_location/notebooks/set_data_location.ipynb, or connect with an output_root"
)


def connect(**connection: Any) -> Microscope:
    """Reach NIS-Elements through the bridge and hand back the connected microscope; see :data:`DEFAULT_CONNECTION`."""
    return Microscope(connection)


def named_configurations() -> list[str]:
    """The configs kept on this computer, by folder name, the most recent first."""
    return MACHINE.configurations()


def checked_connection(connection: dict[str, Any] | None) -> dict[str, Any]:
    """*connection* over :data:`DEFAULT_CONNECTION`, refused before the bridge is touched when it cannot be honoured.

    A field left empty takes the default. An empty ``config`` is the most
    recent config kept on this computer (none when there is none). A host
    that is not this computer and a config that is not kept here are refused
    rather than ignored, so a connection never silently goes somewhere other
    than what was typed.
    """
    given = {
        key: value
        for key, value in (connection or {}).items()
        if value not in (None, "")
    }
    connection = {**DEFAULT_CONNECTION, **given}
    host = str(connection["host"]).strip()
    if host.lower() not in {*THIS_COMPUTER, socket.gethostname().lower()}:
        raise ValueError(
            f"host {host!r}: the bridge answers on this computer only; leave host empty or use 127.0.0.1"
        )
    connection["host"] = host
    connection["port"] = int(connection["port"])
    kept = named_configurations()
    if connection["config"] is None:
        connection["config"] = kept[0] if kept else None
    else:
        connection["config"] = str(connection["config"]).strip()
        if connection["config"] not in kept:
            raise ValueError(
                f"config {connection['config']!r} is not kept on this computer; choose one of {kept} or "
                "leave config empty for the most recent"
                if kept
                else f"config {connection['config']!r}: no configs are kept on this computer yet; leave config empty"
            )
    return connection


class Microscope:
    """One connected Nikon: the bridge session and everything loaded for it."""

    def __init__(self, connection: dict[str, Any] | None = None) -> None:
        """Reach NIS-Elements and load this microscope's settings.

        Making the microscope is the connection: it connects to the bridge,
        reads what NIS-Elements allows, loads the limits (which switch on the
        limits gate), the saved origin, the orientation and the objective
        calibration. Raises when any of these fail, and closes the connection
        behind it.

        The saved origin is read before the bridge is contacted. A saved
        origin that exists but cannot be read stops the connection, because
        quietly falling back to stage coordinates would put every later move
        somewhere other than intended.
        """
        connection = checked_connection(connection)
        use_configuration(connection["config"])
        origin = saved_origin.load_saved_origin() if connection["load_origin"] else None
        location = saved_data_location()
        MACHINE.ensure_layout()
        start_running_log(MACHINE.snapshot_root() / "logs", DRIVER_PACKAGE)
        client = bridge_client.connect_bridge(
            connection["host"], connection["port"], connection["request_timeout_s"]
        )
        try:
            nis_limits = readings.get_limits(client)
            if not nis_limits:
                raise RuntimeError(
                    "NIS-Elements did not report its stage limits; the bridge answers but NIS does not"
                )
            # The limits are loaded first, so nothing can move before they govern.
            handshake.connect_handshake(
                client, nis_limits, load=connection["load_limits"]
            )
            orientation, orientation_info = load_rig_orientation()
            translations, calibration_info = load_objective_calibration(
                enabled=connection["load_calibration"]
            )
        except BaseException:
            gate.uninstall(client)
            client.close()
            raise
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
        )
        if origin is not None:
            handle.origin = origin
        handle.data_location = location
        self.handle = handle

    @property
    def client(self):
        """The bridge connection itself, for the setup notebooks."""
        return self.handle.client

    def close(self) -> None:
        """Close the session; every later call is refused. A second call does nothing.

        The bridge keeps running inside NIS-Elements. The driver forgets the
        limits it installed for this connection and closes its socket.
        """
        gate.uninstall(self.handle.client)
        self.handle.client.close()
        self.handle.closed = True

    def reconnect(self) -> None:
        """Open the bridge connection again, after the macro was restarted or a request timed out.

        What the session loaded stays; the limits are installed again for
        the connection, from the same files.
        """
        self.handle.require_open()
        self.handle.client.reconnect()
        nis_limits = readings.get_limits(self.handle.client)
        handshake.connect_handshake(
            self.handle.client, nis_limits, load=self.handle.connection["load_limits"]
        )

    # --- the stage ----------------------------------------------------------------------

    def position(self, with_actuators: dict | None = None) -> dict:
        """Where the stage is, per axis, in micrometres from the saved origin.

        Each of ``x``, ``y`` and ``z`` holds its ``position``, its ``unit``,
        the motor's own raw reading (``actuator_readings``) and the range the
        stage may travel (``canvas``).
        """
        return absolute_coordinate_system.get_xyz(
            self.handle, with_actuators=with_actuators
        )

    def move_to(
        self, x: float, y: float, z: float, with_actuators: dict | None = None
    ) -> dict:
        """Move to ``x``, ``y``, ``z`` (micrometres from the origin) and answer as :meth:`position`.

        The limits refuse a position outside the travel before anything
        moves. The answer is read back from NIS-Elements once the stage has
        arrived.
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

    # --- channels, exposure, objectives, focus ------------------------------------------

    def channels(self) -> dict:
        """The optical configurations of NIS-Elements: ``{"selected", "channels"}``.

        ``selected`` is the one this session selected last, or None: NIS
        cannot report which configuration is selected.
        """
        self.handle.require_open()
        return {
            "selected": self.handle.channel,
            "channels": readings.get_optical_configurations(self.handle.client) or [],
        }

    def select_channel(self, name: str) -> dict:
        """Select the optical configuration *name*; answers ``{"applied", "unconfirmed"}``.

        A name NIS-Elements does not have is refused with ``ValueError``.
        """
        return state_step.set_state(self.handle, {"channel": name})

    def exposure(self) -> float | None:
        """The camera exposure in milliseconds, as NIS-Elements reports it; None when it cannot be read."""
        self.handle.require_open()
        camera = readings.get_camera(self.handle.client)
        return camera["exposure_ms"] if camera else None

    def set_exposure(self, exposure_ms: float) -> dict:
        """Set the camera exposure and read it back; answers ``{"applied", "unconfirmed"}`` with the value NIS applied."""
        return state_step.set_state(self.handle, {"exposure_ms": exposure_ms})

    def objectives(self) -> dict:
        """The nosepiece: ``{"current": slot, "objectives": {slot: name}}``, slots counted from 1."""
        self.handle.require_open()
        return readings.get_objectives(self.handle.client) or {
            "current": None,
            "objectives": {},
        }

    def change_objective(self, slot: int) -> dict:
        """Turn the nosepiece to *slot* and keep the same spot in view.

        The stage and the focus drive move by the measured offset between the
        two objectives, so the frame still points at the same place on the
        sample. A pair the calibration does not cover is refused before the
        nosepiece turns.
        """
        self.handle.require_open()
        result = keep_in_view.set_objective(self.handle, slot)
        return checked(self.handle, result, f"change_objective({slot})")

    def autofocus(
        self, range_um: float | None = None, speed: int | None = None
    ) -> dict:
        """Run NIS's image-based focus sweep around the current focus; answers the position afterwards."""
        return focus_step.autofocus(self.handle, range_um, speed)

    def lock_focus(self, on: bool = True) -> dict:
        """Switch the Perfect Focus System on (waiting for it to lock) or off; answers its state."""
        return focus_step.lock_focus(self.handle, on)

    # --- the devices beyond the stage ------------------------------------------------------

    def devices(self) -> dict:
        """Every device's settings (light path, port, filters, camera, lights, lasers, shutters), as ``set_state`` takes them."""
        self.handle.require_open()
        return devices_step.get_devices(self.handle.client)

    def lights(self) -> dict:
        """Each lamp or LED by name: ``{"on", "voltage", "iris"}``; a value NIS does not control is None."""
        self.handle.require_open()
        return readings.get_lights(self.handle.client) or {}

    def set_light(
        self,
        name: str,
        *,
        on: bool | None = None,
        voltage: float | None = None,
        iris: float | None = None,
    ) -> dict:
        """Switch the light *name* on or off, or set its voltage or iris; only what is given changes."""
        return state_step.set_state(
            self.handle, {"lights": {name: _given(on=on, voltage=voltage, iris=iris)}}
        )

    def lasers(self) -> dict:
        """Each light source's lines, by wavelength in nm: ``{"line", "on", "power", "power_steps"}``."""
        self.handle.require_open()
        return readings.get_lasers(self.handle.client) or {}

    def set_laser(
        self,
        wavelength_nm: int,
        *,
        on: bool | None = None,
        power: float | None = None,
        laser: str | None = None,
    ) -> dict:
        """Switch a laser line on or off, or set its power (in NIS's power steps).

        The line is chosen by its wavelength. When more than one light source
        has that wavelength, name the one you mean with *laser*.
        """
        self.handle.require_open()
        source = devices_step.resolve_laser(self.handle.client, wavelength_nm, laser)
        return state_step.set_state(
            self.handle,
            {"lasers": {source: {str(int(wavelength_nm)): _given(on=on, power=power)}}},
        )

    def filters(self) -> dict:
        """Each filter changer: ``{"position", "filters": {position: name}}``, positions counted from 1."""
        self.handle.require_open()
        return readings.get_filters(self.handle.client) or {}

    def set_filter(
        self,
        changer: str,
        position: int | None = None,
        *,
        filter_name: str | None = None,
    ) -> dict:
        """Turn the filter changer *changer* to *position*, or to the position holding *filter_name*."""
        self.handle.require_open()
        to = devices_step.resolve_filter(
            self.handle.client, changer, position, filter_name
        )
        return state_step.set_state(self.handle, {"filters": {changer: to}})

    def shutters(self) -> dict:
        """Each shutter by name: True when it is open."""
        self.handle.require_open()
        return readings.get_shutters(self.handle.client) or {}

    def set_shutter(self, name: str, open_: bool) -> dict:
        """Open (True) or close (False) the shutter *name*."""
        return state_step.set_state(self.handle, {"shutters": {name: bool(open_)}})

    def light_path(self) -> dict:
        """The condenser, the apertures, the zoom, the analyzer, the port and the spinning disk, as NIS reports them."""
        self.handle.require_open()
        return readings.get_light_path(self.handle.client) or {}

    def set_light_path(
        self,
        *,
        condenser: int | None = None,
        aperture: str | None = None,
        size: float | None = None,
        zoom: float | None = None,
        analyzer: bool | None = None,
    ) -> dict:
        """Set the condenser position, one aperture's size, the zoom or the analyzer; only what is given changes."""
        wanted = _given(condenser=condenser, zoom=zoom, analyzer=analyzer)
        if (aperture is None) != (size is None):
            raise ValueError("an aperture is set with both its name and its size")
        if aperture is not None:
            wanted["apertures"] = {aperture: size}
        return state_step.set_state(self.handle, {"light_path": wanted})

    def set_port(
        self,
        port: int | None = None,
        *,
        disk_shutter: bool | None = None,
        disk_speed_rpm: float | None = None,
    ) -> dict:
        """Send the light to camera port *port*, or open the spinning disk's shutter or set its speed."""
        return state_step.set_state(
            self.handle,
            {
                "port": _given(
                    port=port, disk_shutter=disk_shutter, disk_speed_rpm=disk_speed_rpm
                )
            },
        )

    def set_camera(
        self,
        *,
        format: str | None = None,
        roi: dict | None = None,
        averaging: int | None = None,
        integration: int | None = None,
    ) -> dict:
        """Set the camera's format (binning and bit depth), its region of interest, averaging or integration."""
        return state_step.set_state(
            self.handle,
            {
                "camera": _given(
                    format=format, roi=roi, averaging=averaging, integration=integration
                )
            },
        )

    def set_pfs_offset(
        self, offset: int | None = None, *, dish: str | None = None
    ) -> dict:
        """Set the plane the Perfect Focus System keeps (in NIS's units) and the dish, ``glass`` or ``plastic``."""
        return state_step.set_state(
            self.handle, {"pfs_offset": _given(offset=offset, dish=dish)}
        )

    def environment(self) -> dict:
        """What the stand's sensors report, by name: ``{"temperature", "status"}``."""
        self.handle.require_open()
        return readings.get_environment(self.handle.client) or {}

    def incubator(self, key: str | None = None) -> Any:
        """The incubator's readings (temperature, CO2, humidity), or the one named by *key*."""
        self.handle.require_open()
        return readings.get_incubator(self.handle.client, key)

    # --- saved optical configurations -------------------------------------------------------

    def channel_settings(self, name: str) -> dict | None:
        """What the optical configuration *name* has saved, such as its exposure."""
        self.handle.require_open()
        return readings.get_optical_configuration(self.handle.client, name)

    def edit_channel(self, name: str, exposure_ms: float) -> dict:
        """Save a new exposure into the optical configuration *name*, and read it back."""
        self.handle.require_open()
        return checked(
            self.handle,
            changes.edit_optical_configuration(self.handle.client, name, exposure_ms),
            f"edit_channel({name!r})",
        )

    def copy_channel(
        self, name: str, new_name: str, exposure_ms: float | None = None
    ) -> dict:
        """Save a copy of the optical configuration *name* as *new_name*, with its own exposure when given."""
        self.handle.require_open()
        return checked(
            self.handle,
            changes.copy_optical_configuration(
                self.handle.client, name, new_name, exposure_ms
            ),
            f"copy_channel({name!r}, {new_name!r})",
        )

    def delete_channel(self, name: str) -> dict:
        """Delete the optical configuration *name* from NIS-Elements. This cannot be undone from the driver."""
        self.handle.require_open()
        result = checked(
            self.handle,
            changes.delete_optical_configuration(self.handle.client, name),
            f"delete_channel({name!r})",
        )
        if self.handle.channel == name:
            self.handle.channel = None
        return result

    # --- live view, exposure, piezo, safety -------------------------------------------------

    def live(self, on: bool = True) -> dict:
        """Start (True) or stop (False) NIS's live view."""
        self.handle.require_open()
        return checked(self.handle, changes.live(self.handle.client, on), f"live({on})")

    def auto_exposure(self) -> dict:
        """Let NIS choose the exposure for what is in view; answers the exposure it chose."""
        self.handle.require_open()
        checked(
            self.handle, changes.auto_exposure(self.handle.client), "auto_exposure()"
        )
        return {"exposure_ms": self.exposure()}

    def move_piezo_z(self, distance_um: float) -> dict:
        """Move the piezo focus drive by *distance_um* (a relative step, within the limits)."""
        self.handle.require_open()
        return checked(
            self.handle,
            changes.move_piezo_z(self.handle.client, distance_um),
            f"move_piezo_z({distance_um})",
        )

    def center_piezo_z(self) -> dict:
        """Move the piezo focus drive back to the middle of its travel."""
        self.handle.require_open()
        return checked(
            self.handle, changes.center_piezo_z(self.handle.client), "center_piezo_z()"
        )

    def escape(self) -> dict:
        """Lower the objective away from the sample, for changing the dish. :meth:`refocus` brings it back."""
        self.handle.require_open()
        return checked(
            self.handle, changes.escape(self.handle.client, True), "escape()"
        )

    def refocus(self) -> dict:
        """Bring the objective back to where it was before :meth:`escape`."""
        self.handle.require_open()
        return checked(
            self.handle, changes.escape(self.handle.client, False), "refocus()"
        )

    def safe_state(self) -> dict:
        """Switch off everything that sends light to the sample: every lamp and laser line, every shutter, the disk shutter."""
        self.handle.require_open()
        return checked(
            self.handle, changes.safe_state(self.handle.client), "safe_state()"
        )

    # --- pictures -------------------------------------------------------------------------

    def acquisition_settings(self) -> dict:
        """How a capture can be triggered: ``snap``, one picture with the settings as they are."""
        return acquire_step.get_acquisition_settings(self.handle)

    def acquire(
        self,
        position_label: str | None = None,
        *,
        acquisition_type: str = "acquisition",
        output_folder: str | None = None,
        acquisition_settings: dict | None = None,
    ) -> dict:
        """Take one picture and stage it, with a note of how it was taken; :meth:`save` writes the store.

        The store will be named after *acquisition_type* and *position_label*,
        in *output_folder*, or else under this microscope's data location.
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

    # --- the state ------------------------------------------------------------------------

    def state(self) -> dict:
        """``{"changeable", "observed"}``: what ``set_state`` can apply, and what can only be read."""
        return state_step.get_state(self.handle)

    def set_state(self, changeable: dict) -> dict:
        """Apply the changeable settings, each read back; only what differs from the microscope now is changed.

        The settings are ``objective``, ``channel``, the devices
        (``light_path``, ``port``, ``filters``, ``camera``, ``lights``,
        ``lasers``, ``shutters``), ``exposure_ms``, ``pfs_offset`` and ``pfs``.
        """
        return state_step.set_state(self.handle, changeable)


def _given(**values) -> dict:
    """The values that were given: a setting left at None is not changed."""
    return {key: value for key, value in values.items() if value is not None}


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


@dataclass
class Session:
    """One connection's session: the bridge and everything the driver remembers about it.

    Attributes:
        client: The bridge connection.
        connection: The connection this session was opened with, without the password.
        hash6: The session's short name, minted at connect; it names the staging folder.
        origin: The zero point of the frame (stage x, y, z and the objective it
            was saved under). Without a saved origin it is all zero: the frame
            is then the plain stage coordinates.
        driven_to: Where ``get_xyz`` last read the stage, in the frame, with
            the objective; ``acquire`` compares against it.
        translations: The objective offsets per nosepiece slot, loaded at
            connect; ``{}`` when the calibration was wanted but cannot be used
            (objective changes are refused), None when it was declined.
        orientation: The turn applied to the pictures this session saves.
        orientation_info, calibration_info: What was loaded at connect, as reported.
        data_location: This microscope's data location, or None.
        channel: The optical configuration this session selected last, or None.
        pixel_size_um: NIS's pixel size for the objective in use, from the last capture.
        closed: Set by ``close``; every later call is refused.
        unconfirmed: Every change this session sent that was accepted but
            could not be confirmed by a read-back, newest last.
        unsaved: The captures taken whose stores are not written yet.
    """

    client: Any
    connection: dict[str, Any]
    hash6: str
    origin: dict[str, Any] = field(default_factory=saved_origin.empty_origin)
    driven_to: dict[str, Any] | None = None
    position_counter: int = 0
    translations: dict[int, tuple[float, float, float]] | None = None
    orientation: Any = None
    orientation_info: dict | None = None
    calibration_info: dict | None = None
    data_location: Path | None = None
    channel: str | None = None
    pixel_size_um: float | None = None
    closed: bool = False
    unconfirmed: list[dict[str, Any]] = field(default_factory=list)
    unsaved: list[Any] = field(default_factory=list)

    def require_open(self) -> None:
        """Refuse to drive a closed session."""
        if self.closed:
            raise RuntimeError("session is disconnected")

    def note_unconfirmed(self, what: str, result: Any) -> None:
        """Record a change that was sent and accepted but never confirmed, and carry on.

        The driver confirms what it can. When a read-back never matched in
        time, the change is not known to have failed, only not known to have
        happened, so it is written down here, said in a warning, and the
        command goes on. The workflow sees the list in ``get_state``.
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

        Raises ``RuntimeError`` saying how to set one up when neither is there:
        data saved somewhere nobody chose is data lost.
        """
        root = self.connection.get("output_root") or self.data_location
        if not root:
            raise RuntimeError(
                f"this microscope does not know where to save its data: {SET_UP}"
            )
        return Path(root).expanduser().resolve()


# --- the running log ----------------------------------------------------------------

FILE_NAME = "running_log.txt"
MAX_BYTES = 5_000_000
OLD_FILES_KEPT = 3
LINE_FORMAT = "%(asctime)s  %(levelname)-7s  %(name)s  %(message)s"


def start_running_log(folder, logger_name, *, level=logging.INFO) -> Path | None:
    """Begin copying the driver's messages into ``<folder>/running_log.txt``.

    Calling this again for the same file does nothing, so each connect can
    call it safely. Answers the path of the file, or None when it could not
    be opened; a problem with the log never stops the experiment.
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


__all__ = [
    "DEFAULT_CONNECTION",
    "Microscope",
    "Session",
    "changes",
    "connect",
    "named_configurations",
]
