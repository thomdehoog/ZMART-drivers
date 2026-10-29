"""The named changes of a session: move the stage, select a channel, set the exposure, change the objective, lock or sweep the focus, take a picture.

Each one checks its arguments, builds its change as plain data
(``primitives/dll/writes.py`` and ``primitives/macro/writes.py``) and hands it
to the dispatcher's ``run()``, which asks the limits once, sends it over the
bridge, and reads it back. Then it adds what the caller needs to the result:
``answered``, what the bridge replied (a move's position as NIS read it
back, a capture's file and pixel size, what the safe state switched off,
the backup kept before a saved configuration changed), and for a move its
target. Beyond the stage there are the lights, lasers, shutters, filters,
light path, camera, piezo Z drive, escape, live view, auto exposure, the safe
state and the saved optical configurations.

Result contract: ``result["success"]`` means the change was accepted, not
that it took effect. A caller that needs proof checks ``result["confirmed"]``.
The one exception is a capture: :func:`take_picture` raises when it failed,
because a workflow must never go on as if a picture had been taken.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging

from ..dispatcher import dispatch
from ..primitives.dll import profile as dll_profile
from ..primitives.dll import writes
from ..primitives.macro import writes as macro_writes

log = logging.getLogger(__name__)


def _run(client, change, *, max_retries=None) -> dict:
    """Run *change* through the dispatcher and add what the bridge answered, when anything was sent."""
    result = dispatch.run(dll_profile, client, change, max_retries=max_retries)
    sent = result["timing"].get("attempts", 0) > 0
    answered = client.reply if sent else None
    result["answered"] = (
        answered.result if answered is not None and answered.error is None else None
    )
    return result


def move_xyz(client, x_um, y_um, z_um, *, tolerance_um=None, max_retries=None) -> dict:
    """Move the stage and the focus drive to an absolute position, in NIS's own micrometres.

    The result carries ``position``, the target, and ``answered``, where NIS
    read the stage back right after the move; ``confirmed`` says whether the
    driver's own read-back saw it there, within ``tolerance_um``.
    """
    for label, value in (("x_um", x_um), ("y_um", y_um), ("z_um", z_um)):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return dispatch.refused(f"{label}={value!r} is not a number", position=None)
    change = writes.move_xyz(
        float(x_um), float(y_um), float(z_um), tolerance_um=tolerance_um
    )
    result = _run(client, change, max_retries=max_retries)
    result["position"] = (
        dict(change.values) if result["timing"].get("attempts", 0) > 0 else None
    )
    return result


def select_optical_configuration(client, name, *, max_retries=None) -> dict:
    """Select the optical configuration *name*; NIS cannot report the selection, so there is no read-back."""
    if not isinstance(name, str) or not name:
        return dispatch.refused(
            f"an optical configuration is named by a text, not {name!r}"
        )
    return _run(
        client, writes.select_optical_configuration(name), max_retries=max_retries
    )


def set_exposure(client, exposure_ms, *, tolerance_ms=None, max_retries=None) -> dict:
    """Set the camera exposure and read it back; ``answered`` holds the value NIS applied (it may round)."""
    if (
        isinstance(exposure_ms, bool)
        or not isinstance(exposure_ms, (int, float))
        or exposure_ms <= 0
    ):
        return dispatch.refused(
            f"the exposure is a positive number of milliseconds, not {exposure_ms!r}"
        )
    return _run(
        client,
        macro_writes.set_exposure(float(exposure_ms), tolerance_ms=tolerance_ms),
        max_retries=max_retries,
    )


def set_objective(client, slot, *, before_fire=None, max_retries=None) -> dict:
    """Turn the nosepiece to *slot* (counted from 1), without moving the stage.

    This is the bare change. The one that also keeps the sample in view is
    ``procedures/keep_in_view.py``; it passes ``before_fire``, called with the
    slot just before anything is sent, which may answer a refusal, and then
    nothing is sent.
    """
    if isinstance(slot, bool) or not isinstance(slot, int) or slot < 1:
        return dispatch.refused(
            f"a nosepiece slot is a whole number from 1, not {slot!r}"
        )
    if before_fire is not None:
        refusal = before_fire(slot)
        if refusal:
            refusal.setdefault("timing", dispatch.make_timing(total_s=0.0, attempts=0))
            return refusal
    return _run(client, writes.set_objective(slot), max_retries=max_retries)


def set_pfs(client, on, *, max_retries=None) -> dict:
    """Switch the Perfect Focus System on (waiting for it to lock) or off, and read its state back."""
    if not isinstance(on, bool):
        return dispatch.refused(f"the PFS is switched with True or False, not {on!r}")
    return _run(client, writes.set_pfs(on), max_retries=max_retries)


def autofocus(client, range_um=None, speed=None) -> dict:
    """Run NIS's image-based focus sweep once; ``answered`` is the position it ended at."""
    range_um = writes.AUTOFOCUS_RANGE_UM if range_um is None else range_um
    speed = writes.AUTOFOCUS_SPEED if speed is None else speed
    if (
        isinstance(range_um, bool)
        or not isinstance(range_um, (int, float))
        or range_um <= 0
    ):
        return dispatch.refused(
            f"the focus sweep's range is a positive number of micrometres, not {range_um!r}"
        )
    if (
        isinstance(speed, bool)
        or not isinstance(speed, int)
        or not 0 <= speed <= writes.AUTOFOCUS_MAX_SPEED
    ):
        return dispatch.refused(
            f"the focus sweep's speed is a whole number from 0 to {writes.AUTOFOCUS_MAX_SPEED}, not {speed!r}"
        )
    return _run(client, writes.autofocus(float(range_um), speed))


def take_picture(client, path) -> dict:
    """Capture one picture and have NIS save it as a TIFF at *path*; answers ``{"path", "pixel_size_um"}``.

    The capture is sent once and never again. It raises ``RuntimeError``
    when the limits refuse it, the bridge fails it, or NIS reports no file.
    """
    result = _run(client, writes.snap(str(path)))
    if not result["success"] or not result["answered"]:
        raise RuntimeError(f"the capture failed: {result.get('message')}")
    answered = dict(result["answered"])
    answered["result"] = result
    return answered


# =============================================================================
# The devices beyond the stage: lights, lasers, shutters, filters, light path, camera
# =============================================================================


def _given(*values) -> bool:
    return any(value is not None for value in values)


def set_pfs_offset(client, *, offset=None, dish=None, max_retries=None) -> dict:
    """Set the plane the PFS keeps (``offset``, a whole number in NIS's units) and the dish (``glass``, ``plastic``)."""
    if not _given(offset, dish):
        return dispatch.refused("nothing to change: give offset or dish")
    if offset is not None and (isinstance(offset, bool) or not isinstance(offset, int)):
        return dispatch.refused(f"the PFS offset is a whole number, not {offset!r}")
    if dish is not None and dish not in ("glass", "plastic"):
        return dispatch.refused(f"the dish is 'glass' or 'plastic', not {dish!r}")
    return _run(client, writes.set_pfs_offset(offset, dish), max_retries=max_retries)


def set_light(
    client, name, *, on=None, voltage=None, iris=None, max_retries=None
) -> dict:
    """Switch the lamp or LED *name* on or off, and set its voltage or iris; read back."""
    if not _given(on, voltage, iris):
        return dispatch.refused("nothing to change: give on, voltage or iris")
    if on is not None and not isinstance(on, bool):
        return dispatch.refused(f"a light is switched with True or False, not {on!r}")
    return _run(
        client,
        writes.set_light(name, on=on, voltage=voltage, iris=iris),
        max_retries=max_retries,
    )


def set_laser(
    client, laser, wavelength_nm, *, on=None, power=None, max_retries=None
) -> dict:
    """Switch one laser line on or off and set its power step; read back."""
    if not _given(on, power):
        return dispatch.refused("nothing to change: give on or power")
    if on is not None and not isinstance(on, bool):
        return dispatch.refused(
            f"a laser line is switched with True or False, not {on!r}"
        )
    if power is not None and (
        isinstance(power, bool) or not isinstance(power, int) or power < 0
    ):
        return dispatch.refused(
            f"the laser power is a whole step from 0, not {power!r}"
        )
    return _run(
        client,
        macro_writes.set_laser(laser, wavelength_nm, on=on, power=power),
        max_retries=max_retries,
    )


def set_shutter(client, name, open_, *, max_retries=None) -> dict:
    """Open (True) or close (False) the shutter *name*; read back."""
    if not isinstance(open_, bool):
        return dispatch.refused(
            f"a shutter is opened with True or closed with False, not {open_!r}"
        )
    return _run(client, writes.set_shutter(name, open_), max_retries=max_retries)


def set_filter(client, changer, position, *, max_retries=None) -> dict:
    """Turn the filter changer *changer* to *position* (from 1); read back."""
    if isinstance(position, bool) or not isinstance(position, int) or position < 1:
        return dispatch.refused(
            f"a filter position is a whole number from 1, not {position!r}"
        )
    return _run(client, writes.set_filter(changer, position), max_retries=max_retries)


def set_light_path(
    client,
    *,
    condenser=None,
    aperture=None,
    size=None,
    zoom=None,
    analyzer=None,
    max_retries=None,
) -> dict:
    """Set the condenser, an aperture's size, the zoom or the analyzer; read back."""
    if not _given(condenser, aperture, zoom, analyzer):
        return dispatch.refused(
            "nothing to change: give condenser, aperture (with size), zoom or analyzer"
        )
    if aperture is not None and size is None:
        return dispatch.refused("an aperture is set with its size")
    return _run(
        client,
        writes.set_light_path(
            condenser=condenser,
            aperture=aperture,
            size=size,
            zoom=zoom,
            analyzer=analyzer,
        ),
        max_retries=max_retries,
    )


def set_port(
    client, *, port=None, disk_shutter=None, disk_speed_rpm=None, max_retries=None
) -> dict:
    """Send the light to a port, and set the spinning disk's shutter and speed; read back."""
    if not _given(port, disk_shutter, disk_speed_rpm):
        return dispatch.refused(
            "nothing to change: give port, disk_shutter or disk_speed_rpm"
        )
    return _run(
        client,
        writes.set_port(
            port=port, disk_shutter=disk_shutter, disk_speed_rpm=disk_speed_rpm
        ),
        max_retries=max_retries,
    )


def set_camera(
    client, *, format=None, roi=None, averaging=None, integration=None, max_retries=None
) -> dict:
    """Set the camera's format (with its binning), region of interest, and frames averaged or summed; read back."""
    if not _given(format, roi, averaging, integration):
        return dispatch.refused(
            "nothing to change: give format, roi, averaging or integration"
        )
    return _run(
        client,
        macro_writes.set_camera(
            format=format, roi=roi, averaging=averaging, integration=integration
        ),
        max_retries=max_retries,
    )


def move_piezo_z(client, distance_um) -> dict:
    """Move the piezo Z drive by *distance_um*, once; NIS cannot read the piezo back."""
    if isinstance(distance_um, bool) or not isinstance(distance_um, (int, float)):
        return dispatch.refused(
            f"a piezo step is a number of micrometres, not {distance_um!r}"
        )
    return _run(client, writes.move_piezo_z(float(distance_um)))


def center_piezo_z(client) -> dict:
    """Bring the piezo Z drive back to the middle of its travel."""
    return _run(client, writes.center_piezo_z())


def escape(client, on) -> dict:
    """Lower the objective away from the sample (True), or bring it back to its focus (False)."""
    if not isinstance(on, bool):
        return dispatch.refused(
            f"escape is True (away) or False (back to focus), not {on!r}"
        )
    return _run(client, writes.escape(on))


def live(client, on) -> dict:
    """Start (True) or freeze (False) NIS's live view."""
    if not isinstance(on, bool):
        return dispatch.refused(f"live is True (live) or False (freeze), not {on!r}")
    return _run(client, macro_writes.live(on))


def auto_exposure(client) -> dict:
    """Let NIS choose the exposure from what the camera sees now; ``answered`` holds the exposure it chose."""
    return _run(client, macro_writes.auto_exposure())


def safe_state(client) -> dict:
    """Switch off every laser line, close every shutter and the disk shutter, switch off every lamp."""
    return _run(client, writes.safe_state())


def edit_optical_configuration(client, name, exposure_ms) -> dict:
    """Change the exposure saved in the optical configuration *name*; read back from NIS's saved copy."""
    if (
        isinstance(exposure_ms, bool)
        or not isinstance(exposure_ms, (int, float))
        or exposure_ms <= 0
    ):
        return dispatch.refused(
            f"the exposure is a positive number of milliseconds, not {exposure_ms!r}"
        )
    return _run(
        client, macro_writes.edit_optical_configuration(name, float(exposure_ms))
    )


def copy_optical_configuration(client, name, new_name, exposure_ms=None) -> dict:
    """Save a copy of the optical configuration *name* as *new_name*, with another exposure when given."""
    if not isinstance(new_name, str) or not new_name.strip() or ":" in new_name:
        return dispatch.refused(
            f"a new configuration's name is a text without ':', not {new_name!r}"
        )
    if exposure_ms is not None and (
        isinstance(exposure_ms, bool)
        or not isinstance(exposure_ms, (int, float))
        or exposure_ms <= 0
    ):
        return dispatch.refused(
            f"the exposure is a positive number of milliseconds, not {exposure_ms!r}"
        )
    return _run(
        client,
        macro_writes.copy_optical_configuration(name, new_name.strip(), exposure_ms),
    )


def delete_optical_configuration(client, name) -> dict:
    """Delete the optical configuration *name*; ``answered`` names the backup that still holds it."""
    return _run(client, macro_writes.delete_optical_configuration(name))
