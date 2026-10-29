"""The readings the ``macro`` class answers: the camera, the lasers, a saved optical configuration, the incubator.

What they share is that NIS-Elements answers them slowly. The camera report
runs several macro calls inside NIS (about 0.2 s on the Ti2 simulator, NIS
6.10, by the bridge's README), the laser power is read through a macro, a
saved optical configuration is read from NIS's own backup file, and the
incubator through a macro.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations


def get_camera(client) -> dict:
    """The camera as NIS reports it: name, driver, sensor and pixel size, format, ROI and exposure.

    ``width_px`` and ``height_px`` are the whole sensor; ``roi`` (``left``,
    ``top``, ``right``, ``bottom``, in pixels) is the part a picture covers.
    ``pixel_um`` is the camera's own pixel, on the sensor, not on the sample.
    ``exposure_ms`` is what NIS applied, which it may have rounded.
    """
    camera = dict(client.request("get_camera"))
    roi = dict(camera.get("roi") or {})
    return {
        "name": str(camera.get("name", "")),
        "driver": str(camera.get("driver", "")),
        "width_px": int(camera["width_px"]),
        "height_px": int(camera["height_px"]),
        "pixel_um": float(camera["pixel_um"]),
        "format": str(camera.get("format", "")),
        "roi": {
            key: int(roi[key])
            for key in ("left", "top", "right", "bottom")
            if key in roi
        },
        "exposure_ms": float(camera["exposure_ms"]),
        "averaging": int(camera.get("averaging") or 1),
        "integration": int(camera.get("integration") or 1),
    }


def get_lasers(client) -> dict:
    """Each multi-laser light source, by name, and its lines by wavelength: ``{"line", "on", "power", "power_steps"}``.

    ``line`` is the line's number, counted from 1 as NIS-Elements shows it.
    ``power`` is a step from 0 to ``power_steps - 1`` (0 to 100 on the Ti2
    simulator's light source). The power is read through a macro: on NIS
    6.10 the DLL's power functions answer but change nothing.
    """
    return {
        laser["name"]: {
            int(line["wavelength_nm"]): {
                "line": int(line["line"]),
                "on": bool(line["on"]),
                "power": line["power"],
                "power_steps": line["power_steps"],
            }
            for line in laser["lines"]
        }
        for laser in client.request("get_lasers")
    }


def get_optical_configuration(client, name) -> dict:
    """What the optical configuration *name* holds, as NIS-Elements saved it.

    NIS has no function that reads one configuration, so the bridge writes
    all of them to NIS's own backup file and reads the one asked for: its
    objective, camera, format, binning, ``exposure_ms`` and
    ``max_exposure_ms``, and its light path. Values NIS did not save are
    None.
    """
    return dict(client.request("get_optical_configuration", name=name))


def get_incubator(client, key=None) -> dict:
    """An incubator's gas (%) and pressure (mbar), now and as set; *key* names one, None the one there is.

    NIS answers zeros when there is no incubator. Reads only.
    """
    return dict(client.request("get_incubator", key=key or ""))


#: The readings of this class, by the name the dispatcher is asked for.
READS = {
    "camera": get_camera,
    "lasers": get_lasers,
    "optical_configuration": get_optical_configuration,
    "incubator": get_incubator,
}

#: The readings that are about one item: the dispatcher hands them ``about``.
ABOUT = {"optical_configuration", "incubator"}
