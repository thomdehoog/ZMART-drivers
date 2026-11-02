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
from .primitives.direct.writes import NAMED_SETTINGS, SETTINGS
from .procedures import (
    absolute_coordinate_system,
    answer_dialogs,
    changes,
    dye_library,
    keep_in_view,
    readings,
    setting_files,
)
from .procedures import acquire as acquire_step
from .procedures import lasx_window
from .procedures import pan as pan_step
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
        """What LAS X is set to now: ``zoom``, ``scan_speed``, ``scan_field_rotation``, ``averaging``, ``pinhole``, ``format``, ``scan_mode``, ``objective``, ``z_stack``, ``autofocus``,
        ``bidirectional``, ``phase_x``, ``system_optimized_step``, ``collar``, ``smartcorr``, ``setting``, ``sequential_mode``, ``autofocus_settings``, ``autofocus_scan`` and ``bit_depth``."""
        self.handle.require_open()
        found = acquire_step.current_settings(self.handle)
        return {name: found[name] for name in found if name != "fov_m"}

    def change_setting(self, name: str, value) -> dict:
        """Change the setting *name* to *value*, and read it back.

        *name* is one of the settings the driver can change: the numbers
        (``zoom``, ``scan_speed``, ``scan_field_rotation``, ``line_average``,
        ``pinhole_airy``, ``frame_average``, ``line_accumulation``,
        ``frame_accumulation``), the scan mode (``scan_mode``, LAS X's name,
        such as ``"xyz"``), ``autofocus`` and ``resonant_scanner`` (True or
        False), ``bidirectional`` (True or False), ``format`` (``(x, y)`` pixels),
        ``phase_x``, ``pinhole_um`` (micrometres), ``pinhole_unit`` (``"um"`` or ``"airy"``)
        ``emission_wavelength`` (whole nanometres, for the Airy unit), ``bit_depth``
        (8, 12 or 16), ``system_optimized_step`` (True or False) and ``collar``
        (the objective's correction collar, 0 to 100 percent) and ``smartcorr_range``
        (``(begin, end, steps)``, the collar positions SmartCORR scans through) and
        ``setting_name`` (the current setting's name, plain text) and ``sequential_mode``
        (``"line"``, ``"frame"`` or ``"stack"``: how several settings are taken in turn). The limits are asked
        first. Answers the change's result, with ``confirmed`` True when the
        read-back showed the new value; a change that was never seen is
        recorded and does not raise.
        """
        self.handle.require_open()
        if name not in SETTINGS and name not in NAMED_SETTINGS:
            raise ValueError(f"no setting {name!r}; the settings are {sorted([*SETTINGS, *NAMED_SETTINGS])}")
        result = changes.change(self.handle.client, name, value)
        return checked(self.handle, result, f"change_setting({name!r}, {value!r})")

    def optimize_format(self) -> dict:
        """Set the format to the one LAS X computes as optimal, as its "Optimize XY Format" button does; read back.

        Refused when LAS X has no optimal format now.
        """
        self.handle.require_open()
        optimal = readings.get_optimal_format(self.handle.client)
        if optimal is None:
            raise RuntimeError("LAS X has no optimal format now, so the format is not changed")
        return self.change_setting("format", optimal)

    def set_detector(
        self, name: str, *, on: bool | None = None, gain: float | None = None, band=None,
        operating_mode: str | None = None, tau_mode: str | None = None, tau_contrast: bool | None = None, dye: str | None = None,
    ) -> dict:
        """Change the detector *name*, any of its fields at once, and read it back.

        *on*, *gain*, *band* ``(begin_nm, end_nm)``, *operating_mode* (Fast
        Mode, Counting, Analog, Reflection), *tau_mode* (Intensity, TauGating,
        TauScan, TauSeparation, TauInteraction; with a pulsed WLL line),
        *tau_contrast* and *dye* (LAS X's name, such as ``"Leica/ALEXA 488"``).

        LAS X takes these through a whole setting: the current setting is
        saved, only Setting 1 is changed in a copy, its laser powers and gains
        are checked against the limits, and the copy is loaded. The result's
        ``changed`` lists everything else LAS X changed with it. Refused when
        LAS X has several sequential settings. In photon counting LAS X keeps
        the gain fixed.
        """
        self.handle.require_open()
        result = setting_files.set_detector(
            self.handle.client, setting_files.folder_for(self.handle), name, on=on, gain=gain, band=band,
            operating_mode=operating_mode, tau_mode=tau_mode, tau_contrast=tau_contrast, dye=dye,
        )
        return checked(self.handle, result, f"set_detector({name!r})")

    def dyes(self, namespace: str = "Leica") -> list[str]:
        """The names of the dyes in LAS X's dye library (``Leica``, or a user's ``User:<name>``)."""
        return dye_library.dye_names(namespace)

    def dye(self, name: str) -> dict:
        """One dye of LAS X's library: its peaks (nm) and its excitation, emission and multiphoton spectra.

        *name* as the library lists it (``"ALEXA 488"``) or as a setting names it
        (``"Leica/ALEXA 488"``); ``setting_name`` is what :meth:`set_detector` takes as *dye*.
        """
        return dye_library.dye(name)

    def set_excitation_line(self, line) -> dict:
        """Choose the excitation line: a wavelength in nm of a line that is on, or ``"auto"``; read back. With one detector on."""
        self.handle.require_open()
        result = setting_files.set_excitation_line(self.handle.client, setting_files.folder_for(self.handle), line)
        return checked(self.handle, result, f"set_excitation_line({line!r})")

    def show_spectra(self, *, excitation: bool | None = None, emission: bool | None = None) -> dict:
        """Show or hide the dyes' excitation and emission spectra in LAS X's setting panel; read back."""
        self.handle.require_open()
        result = setting_files.show_spectra(self.handle.client, setting_files.folder_for(self.handle), excitation=excitation, emission=emission)
        return checked(self.handle, result, "show_spectra")

    def set_tau_channel_colour(self, detector: str, channel: int, colour: str) -> dict:
        """Give TauSeparation's channel 1 or 2 of *detector* a colour (LAS X's name, such as ``"Red"``), as the window's colour pick.

        Only while the detector is in TauSeparation. Unlike the window, the
        other tau channels keep their colours.
        """
        self.handle.require_open()
        result = changes.set_tau_channel_colour(self.handle.client, detector, channel, colour)
        return checked(self.handle, result, f"set_tau_channel_colour({detector!r}, {channel!r}, {colour!r})")

    def find_tau_lifetimes(self, detector: str) -> dict:
        """Press TauSeparation's Find for *detector*: LAS X looks for its two lifetimes; answers ``found`` and ``lifetimes_ns``.

        Only while the detector is in TauSeparation. LAS X looks in the data it
        has, so take a picture first. When it finds none, the lifetimes stay as they were.
        """
        self.handle.require_open()
        result = changes.find_tau_lifetimes(self.handle.client, detector)
        checked(self.handle, result, f"find_tau_lifetimes({detector!r})")
        return {"found": result["found"], "lifetimes_ns": result["lifetimes_ns"]}

    def set_tau_channel_lifetime(self, detector: str, channel: int, ns: float) -> dict:
        """Set the lifetime of TauSeparation's channel 1 or 2 of *detector*, in nanoseconds, as the window's slider.

        Within the window's scale, 0.07 to 9.37 ns; only while the detector is in TauSeparation.
        """
        self.handle.require_open()
        result = changes.set_tau_channel_lifetime(self.handle.client, detector, channel, ns)
        return checked(self.handle, result, f"set_tau_channel_lifetime({detector!r}, {channel!r}, {ns!r})")

    def set_channel_colour(self, detector: str, colour: str) -> dict:
        """Give the channel of *detector* (``"HyD S 3"``) the colour *colour* (LAS X's name, ``"Green"``); read back.

        It changes how the channel is shown, and the colour saved with its
        pictures, not the light. Only the detectors of the spectral detection.
        """
        self.handle.require_open()
        result = changes.set_channel_colour(self.handle.client, detector, colour)
        return checked(self.handle, result, f"set_channel_colour({detector!r}, {colour!r})")

    def add_setting(self, copy: bool = False) -> dict:
        """Add a setting to the current job, new or a copy of the current one, as the window's "Add new Setting" does.

        LAS X makes it the current setting, and the changes after go into it. With
        more than one setting, the changes through a saved setting (``set_detector``
        and the like) are refused.
        """
        self.handle.require_open()
        return checked(self.handle, changes.add_setting(self.handle.client, copy), f"add_setting({copy!r})")

    def activate_setting(self, name: str) -> dict:
        """Make the setting called *name* the current one, as clicking it in the window does; read back.

        The changes after go into the current setting.
        """
        self.handle.require_open()
        return checked(self.handle, changes.activate_setting(self.handle.client, name), f"activate_setting({name!r})")

    def delete_setting(self, name: str) -> dict:
        """Delete the setting called *name* from the current job; the last one is never deleted."""
        self.handle.require_open()
        return checked(self.handle, changes.delete_setting(self.handle.client, name), f"delete_setting({name!r})")

    def enable_setting(self, on: bool) -> dict:
        """Switch the current setting on or off for scanning, as the window's checkbox does; read back.

        Off, LAS X takes no picture with it.
        """
        self.handle.require_open()
        return checked(self.handle, changes.enable_setting(self.handle.client, on), f"enable_setting({on!r})")

    def lock_detector(self, name: str, locked: bool = True) -> dict:
        """Keep the detector *name* and its excitation line from LAS X's Dye Assistant, or free them, as the window's detector lock does."""
        self.handle.require_open()
        result = changes.lock_detector(self.handle.client, setting_files.folder_for(self.handle), name, locked)
        return checked(self.handle, result, f"lock_detector({name!r}, {locked!r})")

    def lock_channel_colour(self, locked: bool = True) -> dict:
        """Lock or free the colour of the channel LAS X's detector panel shows, as the window's colour lock does.

        The lock sends LCS nothing, so it is pressed in LAS X's window and read
        back from there; it holds the window, not the driver's ``set_channel_colour``.
        """
        self.handle.require_open()
        return checked(self.handle, lasx_window.lock_channel_colour(locked), f"lock_channel_colour({locked!r})")

    def autofocus(self, project=None) -> dict:
        """Run LAS X's autofocus once, with the settings in its Best Focus Scan Options, as the window's Autofocus button does.

        The answer's ``found`` says whether LAS X found a focus (it keeps z
        where it was when it did not), with ``z_before_um`` and
        ``z_after_um`` on the autofocus's drive and the ``settings`` used.
        The search's two ends are checked against the limits first.
        """
        self.handle.require_open()
        return checked(self.handle, changes.autofocus(self.handle.client, project), "autofocus()")

    def set_autofocus_optics(self, laser_nm: float, percent: float, detector: str, band, dye: str) -> dict:
        """Give the Best Focus Setting a laser line with its power, and a detector with its band ``(begin_nm, end_nm)`` and dye; read back.

        As the window's drag-in for the autofocus, one command. The power is
        checked against the laser limits (at most 10 % unless they say
        otherwise). A line already there keeps its power: change that with
        ``set_autofocus_laser_power``.
        """
        self.handle.require_open()
        result = changes.set_autofocus_optics(self.handle.client, laser_nm, percent, detector, band, dye)
        return checked(self.handle, result, f"set_autofocus_optics({laser_nm!r}, {percent!r}, {detector!r})")

    def set_autofocus_laser_power(self, laser_nm: float, percent: float) -> dict:
        """Change the power of the Best Focus Setting's line at *laser_nm*, as the window does; read back.

        The line must be in the Best Focus Setting already (``set_autofocus_optics``);
        the power is checked against the laser limits.
        """
        self.handle.require_open()
        return checked(self.handle, changes.set_autofocus_laser_power(self.handle.client, laser_nm, percent), f"set_autofocus_laser_power({laser_nm!r}, {percent!r})")

    def smartcorr(self, project=None) -> dict:
        """Run SmartCORR, as the window's Start SmartCORR: a scan through the collar positions of its range.

        LAS X leaves the collar where the picture was best; the answer's
        ``collar`` says where. The range is set with
        ``change_setting("smartcorr_range", (begin, end, steps))``.
        """
        self.handle.require_open()
        return checked(self.handle, changes.smartcorr(self.handle.client, project), "smartcorr()")

    def set_collar_reference(self) -> dict:
        """Store where the collar stands as this objective's reference, as the window's Set does; read back."""
        self.handle.require_open()
        return checked(self.handle, changes.set_collar_reference(self.handle.client), "set_collar_reference()")

    def reset_collar_reference(self) -> dict:
        """Clear this objective's collar reference, as the window's Reset does; read back."""
        self.handle.require_open()
        return checked(self.handle, changes.reset_collar_reference(self.handle.client), "reset_collar_reference()")

    def collar_to_reference(self) -> dict:
        """Turn the collar to the position stored for this objective, as the window's GoTo does; read back."""
        self.handle.require_open()
        return checked(self.handle, changes.collar_to_reference(self.handle.client), "collar_to_reference()")

    def pan(self) -> dict:
        """Where the galvo has moved the scan field: ``x`` and ``y`` as LAS X holds them, ``x_um`` and ``y_um`` in micrometres.

        Read from LAS X's setting, saved now; the pan has no value in LCS.
        """
        self.handle.require_open()
        return pan_step.read(self.handle.client, setting_files.folder_for(self.handle))

    def move_pan(self, x_um: float, y_um: float) -> dict:
        """Move the pan by *x_um* to the right and *y_um* up (negative: left, down), and read it back.

        Done as the operator does it, with the arrows of LAS X's pan pad,
        at a zoom raised just enough that whole presses land on the
        distance; the zoom is set back afterwards. Refused before anything
        is pressed when the pan would go beyond LAS X's largest.
        """
        self.handle.require_open()
        result = pan_step.move(self.handle.client, setting_files.folder_for(self.handle), x_um, y_um)
        return checked(self.handle, result, f"move_pan({x_um!r}, {y_um!r})")

    def center_pan(self) -> dict:
        """Set the pan back to the centre, through LAS X's smallest zoom and back to the zoom it had; read back."""
        self.handle.require_open()
        result = pan_step.center(self.handle.client, setting_files.folder_for(self.handle))
        return checked(self.handle, result, "center_pan()")

    def save_setting(self, path) -> dict:
        """Save LAS X's current setting to the file *path*, as its Setting panel does."""
        self.handle.require_open()
        return checked(self.handle, setting_files.save(self.handle.client, path), f"save_setting({str(path)!r})")

    def load_setting(self, path) -> dict:
        """Load the setting in the file *path* (one saved in LAS X, say); its laser powers and gains are checked against the limits first.

        The result's ``changed`` lists every reading that changed with it.
        """
        self.handle.require_open()
        result = setting_files.load(self.handle.client, path)
        if result.get("success"):
            result["confirmed"] = True
        return checked(self.handle, result, f"load_setting({str(path)!r})")

    def set_filter_wheel(self, wheel: int, filter_name: str) -> dict:
        """Turn the filter wheel of type *wheel* (LAS X's number, as :meth:`state` lists the wheels) to *filter_name*; read back.

        LAS X's own galvo and scanner wheels are never turned.
        """
        self.handle.require_open()
        result = changes.set_filter_wheel(self.handle.client, wheel, filter_name)
        return checked(self.handle, result, f"set_filter_wheel({wheel!r}, {filter_name!r})")

    def set_laser_power(self, wavelength_nm: float, percent: float) -> dict:
        """Set the power of the laser line at *wavelength_nm* to *percent*, as LAS X's slider shows it, and read it back.

        Above 0 the line is switched on in the current setting; 0 switches it
        off. The limits are asked first (at most 10 % by default). LAS X sets
        the power in steps of 1/255. The white-light laser's tunable lines are
        not set here.
        """
        self.handle.require_open()
        result = changes.set_laser_power(self.handle.client, wavelength_nm, percent)
        return checked(self.handle, result, f"set_laser_power({wavelength_nm!r}, {percent!r})")

    def add_laser_line(self, wavelength_nm: float, percent: float = 2.0) -> dict:
        """Switch on a white-light line at *wavelength_nm* with *percent* power (the window's 2 % unless given); read back.

        As the window's Add Laser: the lines in use are sent with the new one,
        then its power is set, through the limits. Refused before anything is sent outside the
        laser's range, within 8 nm of its lines that are on, without a free
        line, or while a line is locked for the Dye Assistant.
        """
        self.handle.require_open()
        result = changes.add_laser_line(self.handle.client, wavelength_nm, percent)
        return checked(self.handle, result, f"add_laser_line({wavelength_nm!r}, {percent!r})")

    def remove_laser_line(self, wavelength_nm: float) -> dict:
        """Remove the laser line that is on at *wavelength_nm* from the setting, as the window does; read back.

        ``set_laser_power(wavelength_nm, 0)`` switches a line off but leaves it
        listed; this takes it away. Refused while a line is locked for the Dye Assistant.
        """
        self.handle.require_open()
        return checked(self.handle, changes.remove_laser_line(self.handle.client, wavelength_nm), f"remove_laser_line({wavelength_nm!r})")

    def tune_laser_line(self, from_nm: float, to_nm: float) -> dict:
        """Tune the white-light laser's line that is on at *from_nm* to *to_nm*, as the window does; read back.

        Refused before anything is sent outside the laser's range, or within
        8 nm of another of its lines that is on (LAS X would hold a warning open).
        """
        self.handle.require_open()
        return checked(self.handle, changes.tune_laser_line(self.handle.client, from_nm, to_nm), f"tune_laser_line({from_nm!r}, {to_nm!r})")

    def set_laser_shutter(self, wavelength_nm: float, open_: bool) -> dict:
        """Open (True) or close (False) the shutter of the light source the line at *wavelength_nm* comes from; read back."""
        self.handle.require_open()
        result = changes.set_laser_shutter(self.handle.client, wavelength_nm, open_)
        return checked(self.handle, result, f"set_laser_shutter({wavelength_nm!r}, {open_!r})")

    def set_aobs_line(self, wavelength_nm: float, *, reflection: bool | None = None, enhanced_dynamics: bool | None = None) -> dict:
        """Set a white-light line's AOBS, as its Configuration window: Fluorescence or Reflection, and Enhanced Dynamics.

        *reflection* True for Reflection, False for Fluorescence; *enhanced_dynamics*
        on or off. LAS X has no Enhanced Dynamics in Reflection: Reflection
        switches it off, and switching it on brings Fluorescence back. Only the
        white-light laser's lines go through the AOBS. Answers the line as LAS X holds it.
        """
        self.handle.require_open()
        result = changes.set_aobs_line(self.handle.client, wavelength_nm, reflection=reflection, enhanced_dynamics=enhanced_dynamics)
        checked(self.handle, result, f"set_aobs_line({wavelength_nm!r}, reflection={reflection!r}, enhanced_dynamics={enhanced_dynamics!r})")
        lines = readings.get_aobs_lines(self.handle.client) or []
        return next(line for line in lines if abs(line["wavelength"] - float(wavelength_nm)) < 0.5)

    def apply_to_all_settings(self, name: str) -> dict:
        """Give every setting the value *name* has in the current one, as the "apply to all settings" box beside
        ``line_average``, ``line_accumulation``, ``frame_average``, ``frame_accumulation`` and ``pinhole_um`` does."""
        self.handle.require_open()
        return checked(self.handle, changes.apply_to_all_settings(self.handle.client, name), f"apply_to_all_settings({name!r})")

    def set_laser_output_power(self, percent: float, source: str | None = None) -> dict:
        """Set a laser's own output power in the current setting, in percent, as LAS X's Laser Overview slider: the
        white-light laser's, unless *source* names another. Each line's power is a share of it. Within the limits
        (by default up to 85 %, LAS X's own value). Answers each source's output power."""
        self.handle.require_open()
        result = changes.set_laser_output_power(self.handle.client, percent, source=source)
        checked(self.handle, result, f"set_laser_output_power({percent!r})")
        return result["output_power"]

    def set_z_drive(self, drive: str) -> str:
        """Choose the z drive the z-stack uses, ``"z-galvo"`` or ``"z-wide"``, as the z-stack panel's drive list.

        With z-galvo, LAS X also offers the xz scan modes (``xzy``, ``xzt``, ``xzλ`` ...), set with
        ``change_setting("scan_mode", ...)``. Answers the drive as LAS X holds it.
        """
        self.handle.require_open()
        result = changes.set_z_drive(self.handle.client, drive)
        checked(self.handle, result, f"set_z_drive({drive!r})")
        return result["z_drive"]

    def set_lambda_scan(self, begin_nm: int | None = None, end_nm: int | None = None, step_nm: int | None = None,
                        bandwidth_nm: int | None = None) -> dict:
        """Set the λ scan's detection as LAS X's λ panel: begin and end (380 to 800 nm), step and bandwidth, in whole
        nanometres. Answers the λ scan with its number of steps. The scan mode is set apart (``xyλ``, ...)."""
        self.handle.require_open()
        result = changes.set_lambda_scan(self.handle.client, begin_nm=begin_nm, end_nm=end_nm, step_nm=step_nm, bandwidth_nm=bandwidth_nm)
        checked(self.handle, result, "set_lambda_scan()")
        return result["lambda_scan"]

    def set_coverslip_offset(self, um: float) -> float:
        """Set the Reflection autofocus's coverslip offset, in micrometres, as its window; answers it as LAS X holds it."""
        self.handle.require_open()
        result = changes.set_coverslip_offset(self.handle.client, um)
        checked(self.handle, result, f"set_coverslip_offset({um!r})")
        return result["coverslip_offset_um"]

    def find_coverslip_reflection(self) -> dict:
        """Press "Find Coverslip Reflection", as the Reflection autofocus's window: LAS X searches for the coverslip over
        the autofocus's capture range (checked against the z limits first). Answers ``found`` and ``coverslip_offset_um``."""
        self.handle.require_open()
        result = changes.find_coverslip_reflection(self.handle.client)
        checked(self.handle, result, "find_coverslip_reflection()")
        return {"found": result["found"], "coverslip_offset_um": result["coverslip_offset_um"]}

    def set_auto_sync(self, on: bool) -> dict:
        """Auto Sync on or off: the autofocus then uses the current setting's light and detectors. On, Series Scan
        Autofocus goes off (as closing the Best Focus Setting in the window does); off, LAS X copies the current
        setting into the Best Focus Setting. Answers Auto Sync and its options."""
        self.handle.require_open()
        result = changes.set_auto_sync(self.handle.client, on)
        checked(self.handle, result, f"set_auto_sync({on!r})")
        return result["auto_sync"]

    def set_auto_sync_options(self, scanner: str = "fov", **options) -> dict:
        """Auto Sync's scan options for ``"fov"`` or ``"resonant"``, as the Best Focus Scan Options window, while Auto
        Sync is on: ``format`` (16 to 512), ``scan_frequency``, ``overview_zoom`` (a number ticks the box, False
        unticks it), ``bidirectional``, ``no_average``. Each is checked against LAS X's range first."""
        self.handle.require_open()
        result = changes.set_auto_sync_options(self.handle.client, scanner, **options)
        checked(self.handle, result, f"set_auto_sync_options({scanner!r}, {options!r})")
        return result["auto_sync"]

    def set_autofocus_in_time_series(self, when) -> str | int:
        """When LAS X's autofocus runs in a time series, as its Timelapse tab: ``"first"`` (the first cycle only),
        ``"every"`` (every cycle), or n (every n-th cycle). Answers it as LAS X holds it."""
        self.handle.require_open()
        result = changes.set_autofocus_in_time_series(self.handle.client, when)
        checked(self.handle, result, f"set_autofocus_in_time_series({when!r})")
        return result["in_time_series"]

    def set_z_compensation(self, excitation: bool | None = None, detector: bool | None = None, motcorr: bool | None = None,
                           reference_um: float | None = None) -> dict:
        """Switch linear Z-compensation's modes (laser power, detector gain, collar along z) on or off, and set its
        reference position, as LAS X's Linear Z-Compensation window. Answers ``modes``, ``reference_um``, ``points``.

        Switched on with no support points, LAS X puts one at each end of the
        z-stack. Change the powers and gains at a point with the usual setters
        after moving there, then :meth:`add_z_compensation_point`.
        """
        self.handle.require_open()
        result = changes.set_z_compensation(self.handle.client, excitation=excitation, detector=detector, motcorr=motcorr, reference_um=reference_um)
        checked(self.handle, result, "set_z_compensation()")
        return result["z_compensation"]

    def add_z_compensation_point(self) -> dict:
        """Add a support point where the focus is, holding the current powers and gains, as the window's Add."""
        self.handle.require_open()
        result = changes.add_z_compensation_point(self.handle.client)
        checked(self.handle, result, "add_z_compensation_point()")
        return result["z_compensation"]

    def remove_z_compensation_point(self, z_um: float | None) -> dict:
        """Remove the support point at *z_um* (micrometres, as listed), or every point with None (Remove All)."""
        self.handle.require_open()
        result = changes.remove_z_compensation_point(self.handle.client, z_um)
        checked(self.handle, result, f"remove_z_compensation_point({z_um!r})")
        return result["z_compensation"]

    def move_to_z_compensation_point(self, z_um: float) -> dict:
        """Move the focus to the support point at *z_um*, as the window's Move To; checked against the z limits."""
        self.handle.require_open()
        return checked(self.handle, changes.move_to_z_compensation_point(self.handle.client, z_um), f"move_to_z_compensation_point({z_um!r})")

    def set_roi_scan(self, on: bool, sections: tuple[int, int] | None = None) -> dict:
        """ROI scan on or off, as the Acquisition Mode panel's ROI button; *sections* ``(first, last)`` limits it to those
        sections of the z-stack. Answers ``on``, ``sections``, ``bleach_mode`` as LAS X holds them.

        The regions are drawn in LAS X's viewer; the driver does not draw them.
        """
        self.handle.require_open()
        result = changes.set_roi_scan(self.handle.client, on, sections=sections)
        checked(self.handle, result, f"set_roi_scan({on!r}, sections={sections!r})")
        return result["roi"]

    def set_bleach_mode(self, on: bool) -> dict:
        """The bleach mode on or off, as the Acquisition Mode panel's bleach button. Answers as :meth:`set_roi_scan`."""
        self.handle.require_open()
        result = changes.set_bleach_mode(self.handle.client, on)
        checked(self.handle, result, f"set_bleach_mode({on!r})")
        return result["roi"]

    def set_time_series(self, frames: int | None = None, interval_s: float | None = None, until_stopped: bool | None = None) -> dict:
        """Set the time series as LAS X's t panel does: the number of *frames*, the *interval_s* between them,
        or *until_stopped* (Acquire Until Stopped). Answers the time series as LAS X holds it.

        The scan mode must have time (``change_setting("scan_mode", "xyt")`` first).
        The interval is at least one frame (``shortest_interval_s``). Saving a
        series taken with :meth:`acquire` is not yet possible: the files are
        written for x, y and z only.
        """
        self.handle.require_open()
        result = changes.set_time_series(self.handle.client, frames=frames, interval_s=interval_s, until_stopped=until_stopped)
        checked(self.handle, result, f"set_time_series(frames={frames!r}, interval_s={interval_s!r}, until_stopped={until_stopped!r})")
        return result["time_series"]

    def start_live(self) -> None:
        """Start Live, as LAS X's Live button: the microscope scans on, the lasers on, until :meth:`stop_live`.

        Stop it before :meth:`acquire` and the other pictures, which wait for the scanner to be idle.
        """
        self.handle.require_open()
        checked(self.handle, changes.start_live(self.handle.client), "start_live()")

    def start_fast_live(self) -> None:
        """Start Fast Live, as LAS X's Fast Live button, with its own Scan Settings (:meth:`set_fast_live`); stop it with :meth:`stop_live`."""
        self.handle.require_open()
        checked(self.handle, changes.start_live(self.handle.client, fast=True), "start_fast_live()")

    def set_fast_live(self, scanner: str = "fov", **settings) -> dict:
        """Set Fast Live's Scan Settings for ``"fov"`` (the FOV scanner) or ``"resonant"``, as its window does.

        *settings*: ``format`` (Maximal Format: 16, 32, ... 512), ``scan_frequency``
        (Minimal Scan Frequency, Hz), ``overview_zoom`` (a zoom, or False),
        ``bidirectional``, ``no_average``, ``no_dynamic_signal_enhancement``,
        ``selected_setting_only``, ``no_tau_sted_xtend``. Each is checked against
        LAS X's range first. Answers all of Fast Live's settings as LAS X holds them.
        """
        self.handle.require_open()
        result = changes.set_fast_live(self.handle.client, scanner, **settings)
        checked(self.handle, result, f"set_fast_live({scanner!r}, {settings!r})")
        return result["fast_live"]

    def stop_live(self) -> None:
        """Stop Live, as LAS X's Live button; the scanner is idle afterwards."""
        self.handle.require_open()
        checked(self.handle, changes.stop_live(self.handle.client), "stop_live()")

    def set_z_stack(self, drive: str, begin_um: float, end_um: float, step_um: float) -> dict:
        """Set LAS X's z-stack and answer it as LAS X holds it, as :meth:`settings` gives ``z_stack``.

        *drive* is ``"z-galvo"`` or ``"z-wide"``; *begin_um* and *end_um* are
        that drive's own positions in micrometres, as LAS X's z-stack panel
        shows them (begin above end runs the stack downwards); *step_um* is
        the distance between sections, whose number follows. Each end is
        checked like a z move from where the drive stands, so a stack far
        from the focus is refused. LAS X may stretch the end to fit whole
        steps. From then on :meth:`acquire` takes the whole stack.
        """
        self.handle.require_open()
        result = changes.set_z_stack(self.handle.client, drive, begin_um, end_um, step_um)
        checked(self.handle, result, f"set_z_stack({drive!r}, {begin_um!r}, {end_um!r}, {step_um!r})")
        return result["z_stack"]

    def set_z_stack_size(self, size_um: float) -> dict:
        """Set the z-stack's size around its middle, as the window's Z Size; answers the stack. Its new ends are checked first."""
        self.handle.require_open()
        result = changes.set_z_stack_size(self.handle.client, size_um)
        checked(self.handle, result, f"set_z_stack_size({size_um!r})")
        return result["z_stack"]

    def set_z_stack_begin_here(self) -> dict:
        """Set the z-stack's begin where its drive stands, as the window's Begin button; answers the stack."""
        self.handle.require_open()
        result = changes.set_z_stack_here(self.handle.client, "begin")
        checked(self.handle, result, "set_z_stack_begin_here()")
        return result["z_stack"]

    def set_z_stack_end_here(self) -> dict:
        """Set the z-stack's end where its drive stands, as the window's End button; answers the stack."""
        self.handle.require_open()
        result = changes.set_z_stack_here(self.handle.client, "end")
        checked(self.handle, result, "set_z_stack_end_here()")
        return result["z_stack"]

    def recenter_z_stack(self) -> dict:
        """Move the z-stack, its size kept, around where its drive stands, as the window's Re-Center; answers the stack."""
        self.handle.require_open()
        result = changes.recenter_z_stack(self.handle.client)
        checked(self.handle, result, "recenter_z_stack()")
        return result["z_stack"]

    def move_to_z_stack(self, place: str) -> dict:
        """Move the focus to the z-stack's ``"begin"``, ``"end"`` or ``"center"``, or to its ``"focus"`` plane, as the window's Move to buttons.

        The focus plane is the one set with Set Focus in LAS X's z-stack panel.
        The move is checked against the z limits like any z move. Answers the
        change's result, as the other changes do.
        """
        self.handle.require_open()
        result = changes.move_to_z_stack(self.handle.client, place)
        return checked(self.handle, result, f"move_to_z_stack({place!r})")

    def clear_z_stack(self) -> None:
        """Leave no z-stack, as the window's bin: its begin and end both go where the focus is."""
        self.handle.require_open()
        result = changes.clear_z_stack(self.handle.client)
        checked(self.handle, result, "clear_z_stack()")

    def set_z_stack_sections(self, sections: int) -> dict:
        """Set the z-stack's number of sections over its size (by the step that gives it); answers the stack."""
        self.handle.require_open()
        result = changes.set_z_stack_sections(self.handle.client, sections)
        checked(self.handle, result, f"set_z_stack_sections({sections!r})")
        return result["z_stack"]

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
