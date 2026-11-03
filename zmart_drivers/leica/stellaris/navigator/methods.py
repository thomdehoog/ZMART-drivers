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
from .procedures import focus_map as focus_map_step
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
        (``"line"``, ``"frame"`` or ``"stack"``: how several settings are taken in turn) and ``z_stack_mode``
        (``"steps"``, ``"step_size"`` or ``"system_optimized"``: the z-stack panel's Number of Steps, Z-Step
        Size or System Optimized). A lower zoom that pulls the pan in says so (``pan_pulled_in``). The limits are asked
        first. Answers the change's result, with ``confirmed`` True when the
        read-back showed the new value; a change that was never seen is
        recorded and does not raise.
        """
        self.handle.require_open()
        if name not in SETTINGS and name not in NAMED_SETTINGS:
            raise ValueError(f"no setting {name!r}; the settings are {sorted([*SETTINGS, *NAMED_SETTINGS])}")
        if name == "zoom":
            result = pan_step.change_zoom(self.handle.client, setting_files.folder_for(self.handle), value)
        else:
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
        Mode, Counting, Analog, Reflection), *tau_mode* (None, Intensity, TauGating,
        TauScan, TauSeparation, TauInteraction; with a pulsed WLL line),
        *tau_contrast* and *dye* (LAS X's name, such as ``"Leica/ALEXA 488"``).

        Every field goes into the current setting as LAS X's window sends it, in
        any setting; the dye is checked against LAS X's dye library first. To
        set a setting's lines, bands and dyes at once, as LAS X's dye drop does,
        use :meth:`set_dyes`. In photon counting LAS X keeps the gain fixed.
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

    def set_excitation_line(self, line, detector: str | None = None) -> dict:
        """Choose a detector's excitation line, as the detector panel's list: a wavelength in nm of a line that is on in the
        current setting, or ``"auto"``; read back. *detector* may be left out with one detector on. Works in any setting."""
        self.handle.require_open()
        return checked(self.handle, changes.set_excitation_line(self.handle.client, line, detector=detector), f"set_excitation_line({line!r})")

    def show_spectra(self, *, excitation: bool | None = None, emission: bool | None = None) -> dict:
        """Show or hide the dyes' excitation and emission spectra in LAS X's setting panel; read back."""
        self.handle.require_open()
        result = changes.show_spectra(self.handle.client, excitation=excitation, emission=emission)
        return checked(self.handle, result, "show_spectra")

    def set_tau_channel_colour(self, detector: str, channel: int, colour: str, locked: bool = True) -> dict:
        """Give a TauSeparation channel of *detector* a colour (LAS X's name, such as ``"Red"``), as the window's colour pick.

        Only while the detector is in TauSeparation. *locked*: the window's lock
        beside it. Unlike the window, the other tau channels keep their colours.
        """
        self.handle.require_open()
        result = changes.set_tau_channel_colour(self.handle.client, detector, channel, colour, locked=locked)
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

    def set_tau_donor_lifetime(self, detector: str, ns: float) -> dict:
        """Set TauInteraction's average donor lifetime, in nanoseconds (0.1 to 11.5), as its window; only in TauInteraction."""
        self.handle.require_open()
        return checked(self.handle, changes.set_tau_donor_lifetime(self.handle.client, detector, ns), f"set_tau_donor_lifetime({detector!r}, {ns!r})")

    def add_tau_channel(self, detector: str) -> dict:
        """Add a TauSeparation channel, as its window's + (at most seven); set its lifetime and colour after."""
        self.handle.require_open()
        return checked(self.handle, changes.add_tau_channel(self.handle.client, detector), f"add_tau_channel({detector!r})")

    def remove_tau_channel(self, detector: str, channel: int) -> dict:
        """Remove TauSeparation's *channel* (1 the first), as its window's - (two always stay)."""
        self.handle.require_open()
        return checked(self.handle, changes.remove_tau_channel(self.handle.client, detector, channel), f"remove_tau_channel({detector!r}, {channel!r})")

    def set_tau_scan(self, detector: str, images: int | None = None, range_ns: tuple[float, float] | None = None) -> dict:
        """Set TauScan's number of images (1 to the detector's most, 7 on the simulator) and its lifetime range
        ``(begin_ns, end_ns)`` (0.05 to 11.5 ns), as its window; only while the detector is in TauScan."""
        self.handle.require_open()
        return checked(self.handle, changes.set_tau_scan(self.handle.client, detector, images=images, range_ns=range_ns), f"set_tau_scan({detector!r})")

    def set_tau_gate(self, detector: str, channel: int, gate: int, begin_ns: float, end_ns: float) -> dict:
        """Move TauGating's *gate* of *channel* (both counted from 1) to *begin_ns* .. *end_ns*, as its window's handles;
        within -1 to 11.5 ns. Only while the detector is in TauGating."""
        self.handle.require_open()
        return checked(self.handle, changes.set_tau_gate(self.handle.client, detector, channel, gate, begin_ns, end_ns),
                       f"set_tau_gate({detector!r}, {channel!r}, {gate!r})")

    def add_tau_gate(self, detector: str, channel: int, begin_ns: float, end_ns: float) -> dict:
        """Add a gate to TauGating's *channel*, as its window's + (at most three per channel)."""
        self.handle.require_open()
        return checked(self.handle, changes.add_tau_gate(self.handle.client, detector, channel, begin_ns, end_ns), f"add_tau_gate({detector!r})")

    def remove_tau_gate(self, detector: str, channel: int, gate: int) -> dict:
        """Remove TauGating's *gate* of *channel*, as its window's - (a channel keeps one)."""
        self.handle.require_open()
        return checked(self.handle, changes.remove_tau_gate(self.handle.client, detector, channel, gate), f"remove_tau_gate({detector!r})")

    def add_tau_gate_channel(self, detector: str, begin_ns: float, end_ns: float) -> dict:
        """Add a TauGating channel with one gate, as its window's Add Channel."""
        self.handle.require_open()
        return checked(self.handle, changes.add_tau_gate_channel(self.handle.client, detector, begin_ns, end_ns), f"add_tau_gate_channel({detector!r})")

    def remove_tau_gate_channel(self, detector: str, channel: int) -> dict:
        """Remove TauGating's *channel*, as its window's Remove Channel (one channel always stays)."""
        self.handle.require_open()
        return checked(self.handle, changes.remove_tau_gate_channel(self.handle.client, detector, channel), f"remove_tau_gate_channel({detector!r})")

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

        With *copy*, it is the setting's Duplicate (its context menu), which
        sends the same command.

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

    def enable_setting(self, on: bool, all_settings: bool = False) -> dict:
        """Switch the current setting on or off for scanning, as the window's checkbox does; read back.

        With *all_settings*, every setting is switched, as Bulk Options'
        Disable All Settings does. Off, LAS X takes no picture with it.
        """
        self.handle.require_open()
        result = changes.enable_setting(self.handle.client, on, all_settings=all_settings)
        return checked(self.handle, result, f"enable_setting({on!r}, all_settings={all_settings!r})")

    def apply_to_hardware(self, on: bool = True) -> dict:
        """Let a change to a setting move the hardware, or keep it in LAS X's settings only, as the settings toolbar's apply-to-hardware switch does.

        With *on* False, laser powers, gains and the other setting values are
        changed in LAS X's settings, and the lasers, detectors and scanner stay
        as they are until it is switched on again. The limits check each change
        either way. Read back.
        """
        self.handle.require_open()
        return checked(self.handle, changes.apply_to_hardware(self.handle.client, on), f"apply_to_hardware({on!r})")

    def switch_laser(self, name: str, on: bool = True) -> dict:
        """Switch the laser *name* on or off, as the Laser Overview's ON/OFF does; read back.

        LAS X's help: switch on the lasers you need before you start, and off
        again when you are done. Only a laser LAS X lets be switched (on the
        STELLARIS the white-light laser; the others' switches are greyed out);
        a laser just switched on may need time before it is ready.
        """
        self.handle.require_open()
        return checked(self.handle, changes.switch_laser(self.handle.client, name, on), f"switch_laser({name!r}, {on!r})")

    def reset_system(self) -> dict:
        """Put the ImageCompass and the scan back to LAS X's start, as Bulk Options' Reset System does.

        Following LAS X's help: the scan mode xyz, format 512 x 512, 400 Hz,
        zoom 1, rotation 0, pinhole 1 Airy, no autofocus, ROI or bleach point,
        the z-stack's begin and end cleared and the filters and beam splitters
        at their start. The lasers stay on. LAS X asks first in its window; the
        driver does not. Save the setting first (:meth:`save_setting`) to come back.
        """
        self.handle.require_open()
        return checked(self.handle, changes.reset_system(self.handle.client), "reset_system()")

    def reset_setting(self) -> dict:
        """Put the current setting back to LAS X's start, as the setting's Reset (its context menu) does.

        Its laser lines are switched off and its detectors go back to the
        start; the other settings stay as they are. Choose the setting first
        with :meth:`activate_setting`.
        """
        self.handle.require_open()
        return checked(self.handle, changes.reset_setting(self.handle.client), "reset_setting()")

    def reset_imagecompass(self) -> dict:
        """Put only the ImageCompass back to LAS X's start, as Bulk Options' Reset ImageCompass does.

        The setting's laser lines are switched off and its detectors, dyes and
        name go back to the start; the scan stays as it is. Save the setting
        first (:meth:`save_setting`) to come back.
        """
        self.handle.require_open()
        return checked(self.handle, changes.reset_imagecompass(self.handle.client), "reset_imagecompass()")

    def lock_all_settings(self, locked: bool = True) -> dict:
        """Keep every setting's detectors and lines from LAS X's Dye Assistant, as Bulk Options' Lock All Settings
        does, or free them all with *locked* False; read back."""
        self.handle.require_open()
        return checked(self.handle, changes.lock_all_settings(self.handle.client, locked), f"lock_all_settings({locked!r})")

    def lock_detector(self, name: str, locked: bool = True) -> dict:
        """Keep the detector *name* and its excitation line from LAS X's Dye Assistant, or free them, as the window's detector lock does."""
        self.handle.require_open()
        result = changes.lock_detector(self.handle.client, name, locked)
        return checked(self.handle, result, f"lock_detector({name!r}, {locked!r})")

    def lock_laser_line(self, wavelength_nm: float, locked: bool = True) -> dict:
        """Keep the laser line at *wavelength_nm* from LAS X's Dye Assistant, or free it, as the lock on its row in the laser panel does.

        Only a line that is on in the current setting. The result's
        ``locked_lines`` lists the lines locked now, read back from LAS X.
        """
        self.handle.require_open()
        result = changes.lock_laser_line(self.handle.client, wavelength_nm, locked)
        return checked(self.handle, result, f"lock_laser_line({wavelength_nm!r}, {locked!r})")

    def lock_channel_colour(self, locked: bool = True) -> dict:
        """Lock or free the colour of the channel LAS X's detector panel shows, as the window's colour lock does.

        The lock sends LCS nothing, so it is pressed in LAS X's window and read
        back from there; it holds the window, not the driver's ``set_channel_colour``.
        """
        self.handle.require_open()
        return checked(self.handle, lasx_window.lock_channel_colour(locked), f"lock_channel_colour({locked!r})")

    def set_z_stack_focus(self, on: bool = True) -> dict:
        """Set the z-stack's focus plane where the focus is now, or clear it with *on* False, as the z-stack panel's Set Focus.

        Written into LCS's state tree, as the button's effect: the plane goes
        where the stack's drive stands. The result's ``focus`` is the plane as
        LAS X holds it (``z_um``, ``set``); :meth:`move_to_z_stack` moves back
        to it.
        """
        self.handle.require_open()
        return checked(self.handle, changes.set_z_stack_focus(self.handle.client, on), f"set_z_stack_focus({on!r})")

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

    def draw_bleach_point(self, x_px: int, y_px: int) -> dict:
        """Draw a bleach point at the pixel (*x_px*, *y_px*) of the picture LAS X's viewer shows, as Draw point does.

        LAS X keeps a bleach point outside its hardware server, so the driver
        draws it in LAS X's viewer, with mouse moves and clicks sent to LAS X's
        window (the computer's pointer is not moved): it first finds the pixel
        by the viewer's own read-out of where the mouse is, and clicks only when
        that shows the pixel, within half a pixel. Pixels count from the
        picture's top-left corner, x to the right, y down. A point is kept in
        the scan field, not on the sample, so the picture must have been taken
        with the field as it is now (objective, zoom, stage, pan, rotation);
        else it is refused, and a new picture is taken first. The bleach mode must
        be on (:meth:`set_bleach_mode`); the point takes the setting's lines,
        whose power :meth:`set_bleach_laser_power` changes. Read back from LAS X.
        """
        self.handle.require_open()
        result = changes.draw_bleach_point(self.handle.client, x_px, y_px, folder=setting_files.folder_for(self.handle))
        return checked(self.handle, result, f"draw_bleach_point({x_px!r}, {y_px!r})")

    def draw_roi_region(self, shape: str, corner: tuple[int, int], opposite: tuple[int, int]) -> dict:
        """Draw an ROI region, a ``"rectangle"`` or an ``"ellipse"`` in the box from pixel *corner* to pixel *opposite*.

        As LAS X's Draw rectangle and Draw ellipse with a click at each corner, in its viewer,
        each corner found as for :meth:`draw_bleach_point`. ROI scan must be on
        (:meth:`set_roi_scan`). LAS X shows no count of the regions, so the
        result says only that there are some.
        """
        self.handle.require_open()
        result = changes.draw_roi_region(self.handle.client, shape, corner, opposite, folder=setting_files.folder_for(self.handle))
        return checked(self.handle, result, f"draw_roi_region({shape!r}, {corner!r}, {opposite!r})")

    def draw_roi_polygon(self, vertices) -> dict:
        """Draw an ROI polygon through the pixels *vertices* (at least three) of the picture LAS X's viewer shows.

        As LAS X's Draw polygon: a click at each corner and a right click at
        the last, which closes it; each corner found as for
        :meth:`draw_bleach_point`. ROI scan must be on.
        """
        self.handle.require_open()
        result = changes.draw_roi_polygon(self.handle.client, vertices, folder=setting_files.folder_for(self.handle))
        return checked(self.handle, result, f"draw_roi_polygon({vertices!r})")

    def draw_roi_range(self, first_x: int, last_x: int) -> dict:
        """Draw an ROI range: the columns *first_x* to *last_x* of the picture LAS X's viewer shows, over its whole height.

        As LAS X's Draw range: a click at one column and one at the other.
        ROI scan must be on.
        """
        self.handle.require_open()
        result = changes.draw_roi_range(self.handle.client, first_x, last_x, folder=setting_files.folder_for(self.handle))
        return checked(self.handle, result, f"draw_roi_range({first_x!r}, {last_x!r})")

    def set_bleach_time(self, seconds: float) -> dict:
        """Set the bleaching time of the bleach point the Bleach Point panel shows, in seconds; read back.

        Typed into the panel's boxes (days to milliseconds), as the operator
        does; LAS X keeps the time with the point, not in its hardware
        server. The bleach mode must be on and a point drawn.
        """
        self.handle.require_open()
        return checked(self.handle, changes.set_bleach_time(self.handle.client, seconds), f"set_bleach_time({seconds!r})")

    def clear_drawings(self) -> dict:
        """Clear every bleach point and ROI region drawn in LAS X's viewer, as its Select all ROIs and Clear selected ROIs do."""
        self.handle.require_open()
        return checked(self.handle, changes.clear_drawings(self.handle.client), "clear_drawings()")

    def set_bleach_laser_power(self, laser_nm: float, percent: float) -> dict:
        """Change the power of the bleach point's line at *laser_nm*, as the Bleach Point panel's Intensity does; read back.

        The bleach mode must be on (:meth:`set_bleach_mode`) and a point drawn in
        LAS X's viewer: where the point is, and how long it bleaches, LAS X keeps
        outside its hardware server, so they are set in its window. The power is
        checked against the laser limits.
        """
        self.handle.require_open()
        result = changes.set_bleach_laser_power(self.handle.client, laser_nm, percent)
        return checked(self.handle, result, f"set_bleach_laser_power({laser_nm!r}, {percent!r})")

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

        Stepped through LCS's COM scanner, 1 % of the field shown a step, at a
        zoom raised just enough that whole steps land on the distance; the
        zoom is set back afterwards. A pan beyond LAS X's window goes to the
        closest place within it, and the result's ``clipped`` says how far
        over it was. The first step is checked against the formula, and on a
        wrong one the pan is centred and stepped no further. Refused while
        LAS X scans, and while bleach points or ROI regions are drawn: they
        stay in the field, so a pan would move them across the sample.
        """
        self.handle.require_open()
        refuse_with_drawings(self.handle, "the pan", "move_pan")
        pad = scanner_pad(self.handle, "move_pan")
        result = pan_step.move(self.handle.client, setting_files.folder_for(self.handle), x_um, y_um, pad=pad)
        return checked(self.handle, result, f"move_pan({x_um!r}, {y_um!r})")

    def set_pan(self, x_um: float, y_um: float) -> dict:
        """Set the pan to *x_um* right and *y_um* up of the centre (negative: left, down), and read it back.

        The pan is where the galvo has moved the scan field, in micrometres on
        the sample from the centre. It is read now and moved by the difference,
        as :meth:`move_pan` moves it; refused in the same cases.
        """
        self.handle.require_open()
        refuse_with_drawings(self.handle, "the pan", "set_pan")
        pad = scanner_pad(self.handle, "set_pan")
        result = pan_step.set_to(self.handle.client, setting_files.folder_for(self.handle), x_um, y_um, pad=pad)
        return checked(self.handle, result, f"set_pan({x_um!r}, {y_um!r})")

    def step_pan(self, direction: str, count: int = 1, percent: int = 3) -> dict:
        """Step the pan *count* times as one of the pan pad's buttons: one of its eight directions, or its middle, the ``"centre"``.

        *direction* is ``"up"``, ``"up-right"``, ``"right"``, ``"down-right"``,
        ``"down"``, ``"down-left"``, ``"left"``, ``"up-left"`` or ``"centre"``.
        Through LCS's COM scanner, each step *percent* (1, 2 or 3) of the field
        shown, LCS's own step put back afterwards. A pan that would end beyond
        LAS X's window stops at its edge, and the result's ``clipped`` says how
        far over it was; the first step is checked against the formula, and on
        a wrong one the pan is centred. Refused while LAS X scans, and while
        points or regions are drawn. Read back.
        """
        self.handle.require_open()
        refuse_with_drawings(self.handle, "the pan", "step_pan")
        pad = scanner_pad(self.handle, "step_pan", percent)
        result = pan_step.step(self.handle.client, setting_files.folder_for(self.handle), direction, count, pad)
        return checked(self.handle, result, f"step_pan({direction!r}, {count!r}, percent={percent!r})")

    def pan_up(self, count: int = 1, percent: int = 3) -> dict:
        """The pan pad's up button, *count* times (:meth:`step_pan`)."""
        return self.step_pan("up", count, percent=percent)

    def pan_down(self, count: int = 1, percent: int = 3) -> dict:
        """The pan pad's down button, *count* times (:meth:`step_pan`)."""
        return self.step_pan("down", count, percent=percent)

    def pan_left(self, count: int = 1, percent: int = 3) -> dict:
        """The pan pad's left button, *count* times (:meth:`step_pan`)."""
        return self.step_pan("left", count, percent=percent)

    def pan_right(self, count: int = 1, percent: int = 3) -> dict:
        """The pan pad's right button, *count* times (:meth:`step_pan`)."""
        return self.step_pan("right", count, percent=percent)

    def pan_up_left(self, count: int = 1, percent: int = 3) -> dict:
        """The pan pad's up-left button, *count* times (:meth:`step_pan`)."""
        return self.step_pan("up-left", count, percent=percent)

    def pan_up_right(self, count: int = 1, percent: int = 3) -> dict:
        """The pan pad's up-right button, *count* times (:meth:`step_pan`)."""
        return self.step_pan("up-right", count, percent=percent)

    def pan_down_left(self, count: int = 1, percent: int = 3) -> dict:
        """The pan pad's down-left button, *count* times (:meth:`step_pan`)."""
        return self.step_pan("down-left", count, percent=percent)

    def pan_down_right(self, count: int = 1, percent: int = 3) -> dict:
        """The pan pad's down-right button, *count* times (:meth:`step_pan`)."""
        return self.step_pan("down-right", count, percent=percent)

    def pan_centre(self) -> dict:
        """The pan pad's middle button: the pan back to the centre, the zoom untouched (:meth:`step_pan`)."""
        return self.step_pan("centre")

    def measure_pan_window(self, zooms=(1.0, 2.0)) -> dict:
        """Measure where LAS X stops the pan, and the step, and compare them with the numbers in the driver.

        At each zoom the pan is stepped right through the scanner, 3 % a step,
        until LAS X stops it. The answer gives the largest pan and the field
        fraction this machine's LAS X uses (0.00775 and 0.667 in the driver),
        each zoom's measurement, and ``matches_the_code``. The pan is centred
        and the zoom set back afterwards. The field fraction is how LAS X
        defines its steps; on a sample it is checked with pictures.
        """
        self.handle.require_open()
        refuse_with_drawings(self.handle, "the pan", "measure_pan_window")
        pad = scanner_pad(self.handle, "measure_pan_window", 3)
        result = pan_step.measure_window(self.handle.client, setting_files.folder_for(self.handle), pad, zooms=tuple(zooms))
        return checked(self.handle, result, f"measure_pan_window({tuple(zooms)!r})")

    def zoom_in(self, corner: tuple[int, int], opposite: tuple[int, int]) -> dict:
        """Zoom in on the box from pixel *corner* to pixel *opposite* of the field now, as LAS X's Zoom In rectangle does.

        Pixels count from the field's top-left corner, x to the right, y down.
        The zoom is raised until the box fills the field, then the pan brings
        its middle to the centre (as :meth:`set_pan`), clipped to LAS X's
        window; read back. Refused while points or regions are drawn.
        """
        self.handle.require_open()
        refuse_with_drawings(self.handle, "the pan", "zoom_in")
        pad = scanner_pad(self.handle, "zoom_in")
        try:
            result = pan_step.zoom_in(self.handle.client, setting_files.folder_for(self.handle), corner, opposite, pad=pad)
        except ValueError as why:
            result = {"success": False, "confirmed": False, "message": str(why)}
        return checked(self.handle, result, f"zoom_in({corner!r}, {opposite!r})")

    def pan_window(self) -> dict:
        """How far LAS X lets the pan go from the centre at the zoom now, on each axis, in micrometres.

        LAS X keeps the galvo inside a window that grows with the zoom (none at
        its smallest) and clips any pan beyond it. The pan functions go to the
        closest place within it and say so; this answers ``zoom``,
        ``half_width_um``, and ``x_um`` and ``y_um`` as (low, high). With the
        resonant scanner the window is not known: all three are None.
        """
        self.handle.require_open()
        half = pan_step.window_um(self.handle.client)
        span = (-half, half) if half is not None else None
        return {"zoom": readings.get_zoom(self.handle.client)["current"], "half_width_um": half, "x_um": span, "y_um": span}

    def pan_step(self) -> dict:
        """The step LCS's COM scanner holds: ``percent`` of the field shown, and ``um``, how far one step moves the pan at
        the zoom now."""
        self.handle.require_open()
        return pan_step.step_size(self.handle.client, scanner_pad(self.handle, "pan_step", 3))

    def select_bleach_point(self, point: int) -> dict:
        """Show the bleach point at *point* (0 for the first, as :meth:`draw_bleach_point` answers it) in the Bleach
        Point panel, as its dropdown does; :meth:`set_bleach_time`, :meth:`set_bleach_laser_power` and
        :meth:`add_bleach_laser_line` then act on it. Answers the point as LCS shows it."""
        self.handle.require_open()
        result = checked(self.handle, changes.select_bleach_point(self.handle.client, point), f"select_bleach_point({point!r})")
        return result["bleach_point"]

    def add_bleach_laser_line(self, wavelength_nm: float, percent: float | None = None) -> dict:
        """Add a line of the white-light laser at *wavelength_nm* to the bleach point the panel shows, as its Add Laser
        drag-in does: at the panel's 2 %, or at *percent* (through the limits). Answers the point's lines."""
        self.handle.require_open()
        result = changes.add_bleach_laser_line(self.handle.client, wavelength_nm, percent)
        checked(self.handle, result, f"add_bleach_laser_line({wavelength_nm!r}, {percent!r})")
        return result["bleach_point"]

    def add_roi_regions(self, regions: list[dict]) -> list[dict]:
        """Add regions to the ROI scan, with no window: each a ``rectangle`` or ``ellipse`` (``centre_um``, ``size_um``),
        a ``polygon`` (``vertices_um``) or a ``range`` (``from_um``, ``to_um``, a band of columns), in micrometres from
        the field's centre, x to the right and y up. A circle is an ellipse, a square a rectangle, of equal sides.

        Written into LAS X's data container, where its window keeps them, and read back; LAS X shows them at once and
        gives each the setting's laser lines. ROI scan must be on, and every region within the field. Answers every
        region the ROI scan holds now (``name``, ``shape``, ``points_um``, ``centre_um``, ``laser_lines``).
        """
        self.handle.require_open()
        return checked(self.handle, changes.add_roi_regions(self.handle.client, regions), "add_roi_regions()")["regions"]

    def clear_roi_regions(self) -> list[dict]:
        """Take every region of the ROI scan away, with no window; answers the regions left (none)."""
        self.handle.require_open()
        return checked(self.handle, changes.clear_roi_regions(self.handle.client), "clear_roi_regions()")["regions"]

    def roi_regions(self) -> list[dict]:
        """The ROI scan's regions now, read from LAS X's data container, as :meth:`add_roi_regions` answers them."""
        self.handle.require_open()
        return checked(self.handle, changes.read_roi_set(self.handle.client, "regions"), "roi_regions()")["regions"]

    def add_bleach_points(self, points: list[dict]) -> list[dict]:
        """Add bleach points, with no window: each ``at_um`` (x, y in micrometres from the field's centre, x to the right
        and y up) and ``seconds``, its bleaching time. Written into LAS X's data container and read back; LCS gives each
        its own setting with the setting's lines (:meth:`select_bleach_point`, :meth:`set_bleach_laser_power`). The
        bleach mode must be on, and every point within the field. Answers every bleach point now (``centre_um``,
        ``seconds``)."""
        self.handle.require_open()
        return checked(self.handle, changes.add_bleach_points(self.handle.client, points), "add_bleach_points()")["points"]

    def clear_bleach_points(self) -> list[dict]:
        """Take every bleach point away, with no window; answers the points left (none)."""
        self.handle.require_open()
        return checked(self.handle, changes.clear_bleach_points(self.handle.client), "clear_bleach_points()")["points"]

    def bleach_points(self) -> list[dict]:
        """The bleach points now, read from LAS X's data container, with their places and bleaching times."""
        self.handle.require_open()
        return checked(self.handle, changes.read_roi_set(self.handle.client, "bleach_points"), "bleach_points()")["points"]

    def save_bleach_points(self, path) -> dict:
        """Save the bleach points, with their places, bleaching times and light, to the new ``.bpr`` file *path*, as
        the Bleach Point panel's Save does; an existing file is never written over. The panel's Load is not offered:
        LAS X's own Load failed inside its hardware server on the simulator."""
        self.handle.require_open()
        return checked(self.handle, changes.save_bleach_points(self.handle.client, path), f"save_bleach_points({str(path)!r})")

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

    def set_dyes(self, lines: list, detectors: list) -> dict:
        """Give the current setting its laser lines and its detectors' dyes, as LAS X does when a dye is dropped on a detector.

        *lines*: ``[(laser_nm, percent), ...]`` (a white-light line tuned first with :meth:`tune_laser_line` or added);
        *detectors*: ``[(name, dye, (begin_nm, end_nm), colour), ...]``, the dye as LAS X names it (``"Leica/ALEXA 488"``).
        LAS X rebuilds the setting's light and detection from these: lines and detectors left out are switched off. The
        powers are checked against the limits first. Works in whichever setting is current; the other settings are sent
        again as they stand, as the window does.
        """
        self.handle.require_open()
        return checked(self.handle, changes.set_dyes(self.handle.client, lines=lines, detectors=detectors), "set_dyes()")

    def set_excitation_control(self, mode: str, source: str | None = None) -> dict:
        """Set a tunable laser's Excitation Control, "maximum power" or "constant power", as LAS X's Laser Overview: the
        white-light laser's unless *source* names another. Answers each tunable laser's."""
        self.handle.require_open()
        result = changes.set_excitation_control(self.handle.client, mode, source=source)
        checked(self.handle, result, f"set_excitation_control({mode!r})")
        return result["excitation_control"]

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

    def set_excitation_scan(self, begin_nm: int | None = None, end_nm: int | None = None, step_nm: int | None = None,
                            automatic_sp: bool | None = None, method: str | None = None, min_gap_nm: int | None = None,
                            after_stage: bool | None = None, geometry: str | None = None, max_gap_nm=None) -> dict:
        """Set the Λ (excitation) scan as LAS X's Λ panel: begin and end within the white-light laser's range, step,
        Automatic SP Movement, the method ("right fixed" or "constant"), the minimal gap to the detection, Lambda
        After Stage; and for the Λλ scan the *geometry* ("behind", "ahead", "independent") and the maximal distance
        *max_gap_nm* (False: none); whole nanometres. The scan mode is set apart (``xyΛ``, ``xyΛλ`` ...). Answers the Λ scan."""
        self.handle.require_open()
        result = changes.set_excitation_scan(self.handle.client, begin_nm=begin_nm, end_nm=end_nm, step_nm=step_nm, automatic_sp=automatic_sp,
                                             method=method, min_gap_nm=min_gap_nm, after_stage=after_stage, geometry=geometry,
                                             max_gap_nm=max_gap_nm)
        checked(self.handle, result, "set_excitation_scan()")
        return result["excitation_scan"]

    def set_lambda_scan(self, begin_nm: int | None = None, end_nm: int | None = None, step_nm: int | None = None,
                        bandwidth_nm: int | None = None) -> dict:
        """Set the λ scan's detection as LAS X's λ panel: begin and end (380 to 800 nm), step and bandwidth, in whole
        nanometres. Answers the λ scan with its number of steps. The scan mode is set apart (``xyλ``, ...)."""
        self.handle.require_open()
        result = changes.set_lambda_scan(self.handle.client, begin_nm=begin_nm, end_nm=end_nm, step_nm=step_nm, bandwidth_nm=bandwidth_nm)
        checked(self.handle, result, "set_lambda_scan()")
        return result["lambda_scan"]

    def set_mfp_auto_select(self, on: bool) -> bool:
        """The multi-function port (MFP) following the setting (Autoselect) on or off, as LAS X's beam path; off, choose
        its position with :meth:`set_filter_wheel` (wheel 180). Answers it as LAS X holds it."""
        self.handle.require_open()
        result = changes.set_mfp_auto_select(self.handle.client, on)
        checked(self.handle, result, f"set_mfp_auto_select({on!r})")
        return result["mfp_auto_select"]

    def set_notch_auto_select(self, on: bool) -> dict:
        """The notch filters chosen with the laser lines (Auto Select) on or off, as LAS X's Fluorifier Disc Settings;
        off, choose them with :meth:`set_filter_wheel` (wheels 130 and 131). Answers ``auto_select`` and ``polarizer_deg``."""
        self.handle.require_open()
        result = changes.set_notch_auto_select(self.handle.client, on)
        checked(self.handle, result, f"set_notch_auto_select({on!r})")
        return result["fluorifier"]

    def set_polarizer(self, angle_deg: float | None) -> dict:
        """Turn the Fluorifier disc's left wheel as a polarizer to *angle_deg* (-90 to 90), or with None give it back to
        the notch filters, as LAS X's Fluorifier Disc Settings. Answers ``auto_select`` and ``polarizer_deg``."""
        self.handle.require_open()
        result = changes.set_polarizer(self.handle.client, angle_deg)
        checked(self.handle, result, f"set_polarizer({angle_deg!r})")
        return result["fluorifier"]

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
        unticks it), ``bidirectional``, ``no_average``. Each is checked against LAS X's range first. The resonant scanner's
        ``scan_frequency`` is fixed and the window shows no control for it: refused."""
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

    def set_autofocus_at_stage_positions(self, when) -> str | int:
        """When LAS X's autofocus runs at the stage positions of a tile scan or a position list, as the Navigator's
        Autofocus Stage tab: ``"first"`` (the first position only), ``"every"`` (every position), or n (every n-th
        position). Answers it as LAS X holds it."""
        self.handle.require_open()
        result = changes.set_autofocus_at_stage_positions(self.handle.client, when)
        checked(self.handle, result, f"set_autofocus_at_stage_positions({when!r})")
        return result["at_stage_positions"]

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

    def set_line_scan(self, lines: int | None = None, max_lines_per_page: bool | None = None, lines_per_page: int | None = None,
                      page_interval_s: float | None = None, minimize: bool | None = None) -> dict:
        """Set the xt line scan as LAS X's t panel does in xt: the number of *lines*, MAX (*max_lines_per_page*), the
        *lines_per_page* (with MAX off), the *page_interval_s*, and Minimize (*minimize*: the shortest page interval; a
        page interval needs it off). The scan mode must be xt. Answers the line scan."""
        self.handle.require_open()
        result = changes.set_line_scan(self.handle.client, lines=lines, max_lines_per_page=max_lines_per_page, lines_per_page=lines_per_page,
                                       page_interval_s=page_interval_s, minimize=minimize)
        checked(self.handle, result, "set_line_scan()")
        return result["line_scan"]

    def set_time_series(self, frames: int | None = None, interval_s: float | None = None, until_stopped: bool | None = None,
                        minimize: bool | None = None, duration_s: float | None = None) -> dict:
        """Set the time series as LAS X's t panel does: the number of *frames*, the *interval_s* between them,
        or *until_stopped* (Acquire Until Stopped), or *minimize* (the shortest interval, its Minimize box), or the
        *duration_s* (its Duration: the frames that fill it at the interval, in place of *frames*).
        Answers the time series as LAS X holds it.

        The scan mode must have time (``change_setting("scan_mode", "xyt")`` first).
        The interval is at least one frame (``shortest_interval_s``). Then
        :meth:`acquire` takes every frame with LAS X's Start, and :meth:`save`
        writes one OME-Zarr store with the frames along its t axis, the time
        between frames as its scale. A series "until stopped" is not taken.
        """
        self.handle.require_open()
        result = changes.set_time_series(self.handle.client, frames=frames, interval_s=interval_s, until_stopped=until_stopped, minimize=minimize, duration_s=duration_s)
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

        *settings*: ``format`` (Maximal Format: 16, 24, ... 512), ``scan_frequency``
        (Minimal Scan Frequency, Hz), ``overview_zoom`` (a zoom, or False),
        ``bidirectional``, ``no_average``, ``no_dynamic_signal_enhancement``,
        ``selected_setting_only``, ``no_tau_sted_xtend``. Each is checked against
        LAS X's range first. The resonant scanner's ``scan_frequency`` is fixed and
        the window offers no control for it: refused. Answers all of Fast Live's settings as LAS X holds them.
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

    def apply_optical_section_step(self) -> dict:
        """Set the z-stack's step from LAS X's formula of the optical section, as the formula's Apply to Step Size does.

        The formula uses the emission wavelength set with
        ``change_setting("emission_wavelength", ...)``, and the objective and
        pinhole in use. LAS X fits the stack's end to whole steps. Answers the stack.
        """
        self.handle.require_open()
        result = changes.apply_optical_section_step(self.handle.client)
        checked(self.handle, result, "apply_optical_section_step()")
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
        The move is checked against the z limits like any z move. In an xz scan
        mode (xzy ...) the panel is LAS X's y-stack, and the scan line moves
        instead (:meth:`move_y_line`). Answers the change's result, as the other
        changes do.
        """
        self.handle.require_open()
        result = changes.move_to_z_stack(self.handle.client, place)
        return checked(self.handle, result, f"move_to_z_stack({place!r})")

    def clear_z_stack(self) -> None:
        """Leave no z-stack, as the window's bin: its begin and end both go where the focus is."""
        self.handle.require_open()
        result = changes.clear_z_stack(self.handle.client)
        checked(self.handle, result, "clear_z_stack()")

    def move_y_line(self, y_um: float) -> dict:
        """In an xz scan mode (xzy ...), move the y-stack's scan line to *y_um*, as the y-stack panel's Y-Position box does.

        LAS X's range is checked first; read back. In an xz scan mode the
        z-stack functions work on the y-stack: its begin and end are set where
        the line is, and its Move to buttons move the line. Choosing an xz scan
        mode sets the zoom to its maximum, and it stays there after.
        """
        self.handle.require_open()
        return checked(self.handle, changes.move_y_line(self.handle.client, y_um), f"move_y_line({y_um!r})")

    def set_z_stack_sections(self, sections: int) -> dict:
        """Set the z-stack's number of sections over its size (by the step that gives it); answers the stack."""
        self.handle.require_open()
        result = changes.set_z_stack_sections(self.handle.client, sections)
        checked(self.handle, result, f"set_z_stack_sections({sections!r})")
        return result["z_stack"]

    def change_objective(self, slot: int | None = None, name: str | None = None, magnification: float | None = None) -> dict:
        """Change the objective, by its slot, name or magnification, and keep the same spot in view.

        The stage is moved by the measured offset between the two objectives,
        so the frame still points at the same place on the sample. Refused
        while bleach points or ROI regions are drawn: they are kept in the scan
        field, so with another objective they would land a little elsewhere on
        the sample; clear them first (:meth:`clear_drawings`) and draw them
        again on a picture taken with the new objective.
        """
        self.handle.require_open()
        refuse_with_drawings(self.handle, "the objective", "change_objective")
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
        button: str = "preview",
        autofocus: bool = False,
        focus_map: list[tuple[float, float, float]] | None = None,
    ) -> dict:
        """Take one picture of several fields at *positions* (x, y pairs in micrometres from the origin), at the current focus.

        LAS X scans them in one acquisition, as the Navigator's Preview (one
        plane per field, *button* ``"preview"``) or its Start (``"start"``:
        the setting as it is, its z-stack, time series and channels). With
        Start, *autofocus* True runs LAS X's autofocus at the positions its
        Stage tab says (:meth:`set_autofocus_at_stage_positions`), and
        *focus_map* (focal points ``(x, y, z)``, as :meth:`find_focus_map`
        answers them) takes each field at the focus the map gives its place,
        on z-galvo. :meth:`save` writes one store per field, named
        ``<acquisition_type>_<label>_field<n>``, each placed where its field
        was taken. Every position is shown to the limits first; one outside
        them refuses the whole scan. The stage is put back at the first
        field afterwards.
        """
        return acquire_step.tile_scan(
            self.handle,
            positions,
            position_label=position_label,
            acquisition_type=acquisition_type,
            output_folder=output_folder,
            button=button,
            autofocus=autofocus,
            focus_map=focus_map,
        )

    def row_of_fields(self, count: int) -> list[tuple[float, float]]:
        """*count* positions in a row along x from here, a field and a tenth apart, for :meth:`tile_scan`."""
        return acquire_step.row_of_fields(self.handle, count)

    def spiral_of_fields(self, rings: int) -> list[tuple[float, float]]:
        """Positions in an outward square spiral from here, *rings* rings round it, as LAS X's Spiral lays them out:
        the fields a field less a tenth apart, ``(2 * rings + 1) ** 2`` of them, for :meth:`tile_scan`."""
        return acquire_step.spiral_of_fields(self.handle, rings)

    def find_focus_map(self, places: list[tuple[float, float]]) -> list[tuple[float, float, float]]:
        """Find the focus at each of *places* (x, y in micrometres from the origin) with LAS X's Navigator autofocus,
        as the Navigator's Focus Map does with "Find all by Autofocus"; answers the focal points ``(x, y, z)`` for
        :meth:`tile_scan`.

        The stage goes to each place in turn (checked by the limits), at the
        focus found at the one before (the focus now, for the first), and
        LCS's Navigator autofocus searches there with the autofocus's
        settings, the manual controls held meanwhile. A place where it finds
        no focus refuses the map. Afterwards the manual controls are given
        back and the stage put back where it stood, also when the map was
        refused.
        """
        focus_map_step.checked_points([(x, y, 0.0) for x, y in places])
        here = self.position()
        start = tuple(here[axis]["position"] for axis in ("x", "y", "z"))
        points, z = [], start[2]
        try:
            for x, y in places:
                self.move_to(x, y, z)
                result = checked(self.handle, changes.focus_point(self.handle.client), f"find_focus_map at ({x:g}, {y:g})")
                if not result["found"]:
                    raise RuntimeError(
                        f"the Navigator's autofocus found no focus at ({x:g}, {y:g}) um, so the focus map is not made"
                    )
                z = self.position()["z"]["position"]
                points.append((float(x), float(y), z))
        finally:
            changes.give_back_manual_operation(self.handle.client)
            self.move_to(*start)
        return points

    def shift_focus_map(self, points: list[tuple[float, float, float]]) -> list[tuple[float, float, float]]:
        """The focus map moved by one offset to the focus here, as the Focus Map's "Shift focus map to current Z":
        focus a spot (after a drift, say), then every point moves by the difference between the focus here and the
        map's here; the map's shape is kept. Answers the moved points; nothing moves on the microscope."""
        here = self.position()
        return focus_map_step.shift(points, *(here[axis]["position"] for axis in ("x", "y", "z")))

    def focus_points(self, positions: list[tuple[float, float]], number: int) -> list[tuple[float, float]]:
        """*number* places spread over *positions* (the fields of a scan, x, y in micrometres), for
        :meth:`find_focus_map`, as the Navigator's "Setup Focus Points"; the same fields give the same places."""
        return focus_map_step.spread(positions, number)

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


def scanner_pad(handle, what: str, percent: int = 1):
    """The pad that steps the pan through LCS's COM scanner, *percent* of the field shown a step; raises, as a refused
    change does, while LAS X scans or for a step other than 1, 2 or 3."""
    try:
        return pan_step.ScannerPad(handle.client, percent=percent)
    except (pan_step.ScannerRefused, ValueError) as why:
        checked(handle, {"success": False, "confirmed": False, "message": str(why)}, what)


def refuse_with_drawings(handle, change: str, what: str) -> None:
    """Raise, as a refused change does, when bleach points or ROI regions are drawn and *change* would move them."""
    in_the_way = changes.drawings_in_the_way(handle.client, change)
    if in_the_way:
        checked(handle, {"success": False, "confirmed": False, "message": f"{change} was not changed: {in_the_way}"}, what)


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
