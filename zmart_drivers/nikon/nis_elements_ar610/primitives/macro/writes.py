"""The changes the ``macro`` class sends: the exposure, the lasers, the camera, live view, auto exposure, the saved optical configurations.

Each is slow inside NIS-Elements, or read back through the slow route. The
exposure: ``Camera_ExposureSet`` is not exported by the DLL, and reading it
back takes a macro. The laser power is set through a macro, because on NIS
6.10 the DLL's power functions answer but change nothing. The camera's
format, region of interest and frame counts, live view and auto exposure are
macro functions. A saved optical configuration is changed through NIS's own
backup file.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from ..dll.writes import Expect, Write

#: How close the exposure read back must come, in milliseconds. The camera
#: applies whole milliseconds on the Ti2 simulator, so a request of 20.4 ms
#: reads back as 20 ms and still counts.
EXPOSURE_TOLERANCE_MS = 1.0


def exposure_within(exposure_ms: float, tolerance_ms: float):
    """The camera reading shows an exposure within ``tolerance_ms`` of the target."""

    def check(camera) -> bool:
        try:
            return abs(float(camera["exposure_ms"]) - exposure_ms) <= tolerance_ms
        except (KeyError, TypeError, ValueError):
            return False

    return check


def set_exposure(exposure_ms: float, *, tolerance_ms: float | None = None) -> Write:
    """Set the camera exposure to *exposure_ms* and read it back from the camera report.

    Setting the same exposure again is harmless, so an unconfirmed change is
    sent again between the windows.
    """
    tolerance = EXPOSURE_TOLERANCE_MS if tolerance_ms is None else tolerance_ms

    def fill(args):
        args.update(exposure_ms=float(exposure_ms))

    return Write(
        command="set_exposure",
        values={"exposure_ms": exposure_ms},
        request="set_exposure",
        fill=fill,
        description=f"exposure to {exposure_ms:g} ms",
        confirm=Expect("camera", exposure_within(exposure_ms, tolerance)),
    )


# =============================================================================
# The lasers and the camera
# =============================================================================


def laser_is(laser, wavelength_nm, on=None, power=None):
    """The lasers reading shows the line switched and set as asked (only what was asked)."""

    def check(lasers) -> bool:
        try:
            line = lasers[laser][int(wavelength_nm)]
            return (on is None or line["on"] is on) and (
                power is None or line["power"] == power
            )
        except (KeyError, TypeError, ValueError):
            return False

    return check


def set_laser(
    laser: str, wavelength_nm: int, *, on: bool | None = None, power: int | None = None
) -> Write:
    """Switch the line *wavelength_nm* of the light source *laser* on or off, and set its power step.

    The power goes through a macro; on the Ti2 simulator NIS reads 0 back
    whatever was set, so the change is then recorded as unconfirmed.
    """

    def fill(args):
        args.update(
            laser=laser,
            wavelength_nm=int(wavelength_nm),
            **{k: v for k, v in (("on", on), ("power", power)) if v is not None},
        )

    return Write(
        command="set_laser",
        values={
            "laser": laser,
            "wavelength_nm": wavelength_nm,
            "on": on,
            "power": power,
        },
        request="set_laser",
        fill=fill,
        description=f"laser {laser} {wavelength_nm} nm: on {on}, power {power}",
        confirm=Expect("lasers", laser_is(laser, wavelength_nm, on, power)),
    )


def camera_is(format=None, roi=None, averaging=None, integration=None):
    def check(camera) -> bool:
        try:
            return (
                (format is None or camera["format"] == format)
                and (roi is None or {k: camera["roi"][k] for k in roi} == dict(roi))
                and (averaging is None or camera["averaging"] == averaging)
                and (integration is None or camera["integration"] == integration)
            )
        except (KeyError, TypeError):
            return False

    return check


def set_camera(
    *,
    format: str | None = None,
    roi: dict | None = None,
    averaging: int | None = None,
    integration: int | None = None,
) -> Write:
    """Set the camera's format (which holds the binning, as in ``"FMT 2x2 16"``), its region of interest, and the frames averaged or summed."""
    given = {
        "format": format,
        "roi": roi,
        "averaging": averaging,
        "integration": integration,
    }

    def fill(args):
        args.update({key: value for key, value in given.items() if value is not None})

    return Write(
        command="set_camera",
        values=dict(given),
        request="set_camera",
        fill=fill,
        description=f"camera: {', '.join(f'{k} {v}' for k, v in given.items() if v is not None)}",
        confirm=Expect("camera", camera_is(format, roi, averaging, integration)),
    )


def live(on: bool) -> Write:
    """Start NIS's live view (``on`` True) or freeze it. Nothing is saved, and NIS cannot report which it is."""

    def fill(args):
        args.update(on=bool(on))

    return Write(
        command="live",
        values={"on": on},
        request="live",
        fill=fill,
        description="live view on" if on else "live view frozen",
    )


def auto_exposure() -> Write:
    """Let NIS choose the exposure from what the camera sees now; the bridge answers the exposure it chose."""
    return Write(
        command="auto_exposure",
        values={},
        request="auto_exposure",
        fill=lambda args: None,
        description="auto exposure",
    )


# =============================================================================
# Saved optical configurations
# =============================================================================


def saved_exposure_within(
    exposure_ms: float, tolerance_ms: float = EXPOSURE_TOLERANCE_MS
):
    def check(configuration) -> bool:
        try:
            return (
                abs(float(configuration["exposure_ms"]) - exposure_ms) <= tolerance_ms
            )
        except (KeyError, TypeError, ValueError):
            return False

    return check


def short_name(name: str) -> str:
    """A configuration's name without the light path NIS puts in front of it ("Spinning Disk (2):BF" is "BF")."""
    return name.rsplit(":", 1)[-1]


def edit_optical_configuration(name: str, exposure_ms: float) -> Write:
    """Change the exposure saved in the optical configuration *name*; read back from NIS's saved configuration.

    NIS 6.10 has no function that changes a saved configuration, so the
    bridge goes through NIS's own backup file and keeps a copy from before;
    the bridge's README says how.
    """

    def fill(args):
        args.update(name=name, exposure_ms=float(exposure_ms))

    return Write(
        command="edit_optical_configuration",
        values={"name": name, "exposure_ms": exposure_ms},
        request="edit_optical_configuration",
        fill=fill,
        description=f"optical configuration {name!r}: saved exposure {exposure_ms:g} ms",
        confirm=Expect(
            "optical_configuration", saved_exposure_within(exposure_ms), about=name
        ),
    )


def configuration_listed(name: str, listed: bool = True):
    def check(names) -> bool:
        try:
            found = any(n == name or short_name(n) == short_name(name) for n in names)
            return found is listed
        except TypeError:
            return False

    return check


def copy_optical_configuration(
    name: str, new_name: str, exposure_ms: float | None = None
) -> Write:
    """Save a copy of the optical configuration *name* as *new_name*, with another exposure when given.

    Never sent twice: the second copy would find the name taken.
    """

    def fill(args):
        args.update(
            name=name,
            new_name=new_name,
            **({"exposure_ms": float(exposure_ms)} if exposure_ms is not None else {}),
        )

    return Write(
        command="copy_optical_configuration",
        values={"name": name, "new_name": new_name, "exposure_ms": exposure_ms},
        request="copy_optical_configuration",
        fill=fill,
        description=f"optical configuration {name!r} copied as {new_name!r}",
        confirm=Expect("optical_configurations", configuration_listed(new_name)),
        never_resend=True,
    )


def delete_optical_configuration(name: str) -> Write:
    """Delete the optical configuration *name*; the bridge keeps a backup holding it. Never sent twice."""

    def fill(args):
        args.update(name=name)

    return Write(
        command="delete_optical_configuration",
        values={"name": name},
        request="delete_optical_configuration",
        fill=fill,
        description=f"optical configuration {name!r} deleted",
        confirm=Expect(
            "optical_configurations", configuration_listed(name, listed=False)
        ),
        never_resend=True,
    )
