"""The readings the ``macro`` class answers: the camera and its exposure.

One request, ``get_camera``, brings the camera's name and driver, the sensor
size and pixel in micrometres, the capture format, the region of interest
(the part of the sensor a picture covers) and the exposure in milliseconds.
Inside NIS-Elements it runs five macro calls, so it takes about a tenth of a
second (measured on the Ti2 simulator, NIS 6.10, in the bridge's README).

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
    }


#: The readings of this class, by the name the dispatcher is asked for.
READS = {"camera": get_camera}
