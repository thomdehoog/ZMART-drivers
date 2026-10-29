"""The readings the bridge can answer: one function each, asking NIS one question and reading its values.

Each function here asks NIS through the bridge and hands back a plain value:
where the stage is, which optical configurations exist, which objective is
in. None of them changes anything. A reading NIS cannot give (the stage is
not connected, so ``StgGetPosXY`` answers ``DR_NOTAVAILABLE``) raises
:class:`NisRefused` with NIS's code, and the read loop records the reason.

NIS's device functions answer a ``DR_`` code: ``1`` means it worked, and a
negative number says why not (:data:`DR_CODES`). A negative code becomes an
error with its name, so a message reads "StgGetPosXY: DR_NOTAVAILABLE (-4)"
rather than a bare number. A few readings are several calls read together
(the list of optical configurations, the nosepiece), because NIS answers one
item a call.

The function names and arguments follow NIS's macro reference, the NIS 6.10
bridge, and what step 0 of the NIS 7 study called on NIS-Elements AR 7.01.03
(25 October 2026, microscope off). Which of ``GetSelectedOptConf``'s texts
holds the name, ``Get_ImageInfo`` with no image open, and ``Get_Calibration``'s
unit codes are still to be checked on a microscope that is on.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import math
from typing import Any

from . import devices
from .client import NisRefused, ptr

#: NIS's own return codes of its device functions, by name.
DR_CODES = {
    1: "DR_OK",
    2: "DR_PARTIALLYOK",
    0: "DR_CANCELED",
    -1: "DR_UNKNOWNERROR",
    -2: "DR_BADPARAMETER",
    -3: "DR_NOTSUPPORTED",
    -4: "DR_NOTAVAILABLE",
    -5: "DR_NOTAUTOMATIC",
    -6: "DR_NOTCALIBRATED",
    -7: "DR_NOTINITIALIZED",
}
#: The codes that will not change by asking again: a wrong request, or a device that is not there.
LASTING_CODES = frozenset({-2, -3, -4, -5, -7})

#: How many characters a text NIS writes may have; what step 0 used.
TEXT_SIZE = 256
#: ``Get_InfoStr``'s question for the NIS-Elements version.
INFO_VERSION = 1
#: ``Get_Calibration``'s unit code for micrometres. Taken from the NIS 6.10
#: simulator (2026-09-18) and still to be confirmed on NIS 7; every other code
#: is read as "not known to be micrometres", and the pixel size as unknown.
CALIBRATION_UNIT_UM = 2

#: What ``Stg_GetPFSStatus`` answers, in words (NIS normalises these across microscopes).
PFS_STATUS = {
    -1: "error or not connected",
    0: "off, in range",
    1: "on, focused",
    3: "off, out of range",
    4: "off, PFS optics not set",
    5: "on, searching",
    6: "on, search stopped (cannot find focus)",
    7: "disabled, objective not supported",
}
PFS_ON = frozenset({1, 5, 6})


def dr_name(code: Any) -> str:
    """A DR code with its meaning beside it: ``"-4 (DR_NOTAVAILABLE)"``."""
    return f"{code} ({DR_CODES.get(code, 'not a DR code')})"


def is_failure_code(code: Any) -> bool:
    """Whether a function's return value is a DR code that says it failed (0 or below)."""
    return isinstance(code, int) and not isinstance(code, bool) and code < 1


def checked(answer: dict, what: str) -> dict:
    """*answer* when its DR code means it worked; :class:`NisRefused` with the code otherwise."""
    code = answer.get("return")
    if is_failure_code(code):
        raise NisRefused(f"{what}: {dr_name(code)}", code=code)
    return answer


def _number(value: Any) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"not a finite number: {value!r}")
    return number


# =============================================================================
# The readings
# =============================================================================


def get_version(client) -> str:
    """The NIS-Elements version, such as ``"7.01.03 (Build 2233)"``."""
    answer = client.read("Get_InfoStr", [INFO_VERSION, ptr("char")])
    return str(answer["values"][1])


def get_xy(client) -> dict:
    """Where the stage is: ``{"x_um", "y_um"}`` in micrometres, as NIS reports it."""
    answer = checked(
        client.read("StgGetPosXY", [ptr("double"), ptr("double")]), "StgGetPosXY"
    )
    x, y = answer["values"][:2]
    return {"x_um": _number(x), "y_um": _number(y)}


def get_z(client) -> dict:
    """Where the focus drive is: ``{"z_um"}`` in micrometres. The second argument, 0, names the main z drive."""
    answer = checked(client.read("StgGetPosZ", [ptr("double"), 0]), "StgGetPosZ")
    return {"z_um": _number(answer["values"][0])}


def get_stage_limits(client) -> dict:
    """The travel NIS itself allows: ``{"x_um": [low, high], "y_um": [...], "z_um": [...]}``.

    These are the limits set in NIS-Elements. The driver's own limits are cut
    to them (``driver_setup/limits``), and the bridge refuses any move beyond them.
    """
    xy = checked(client.read("StgXY_GetLimits", [ptr("double")] * 4), "StgXY_GetLimits")
    z = checked(client.read("StgZ_GetLimits", [ptr("double")] * 2), "StgZ_GetLimits")
    x0, y0, x1, y1 = (_number(v) for v in xy["values"][:4])
    z0, z1 = (_number(v) for v in z["values"][:2])
    return {
        "x_um": [min(x0, x1), max(x0, x1)],
        "y_um": [min(y0, y1), max(y0, y1)],
        "z_um": [min(z0, z1), max(z0, z1)],
    }


def get_optical_configurations(client) -> list[str]:
    """The names of the optical configurations defined in NIS, in NIS's order.

    Step 1 and step 2 counted none when NIS was started normally with the
    microscope off, and three when it was started with ``-passive`` (step 0).
    """
    count = client.read("GetOptConfCount", [])["return"]
    names = []
    for index in range(max(int(count or 0), 0)):
        answer = client.read("GetOptConfName", [index, ptr("char"), TEXT_SIZE])
        names.append(str(answer["values"][1]))
    return names


def get_selected_optical_configuration(client) -> dict:
    """The selected optical configuration: ``{"texts": [three texts]}``.

    NIS writes three texts; which one holds the name is not documented, and
    with the microscope off all three were empty (step 0, 25 October 2026).
    The change that selects a configuration counts it as selected when its
    name is one of these texts, so it does not have to know which.
    """
    answer = client.read(
        "GetSelectedOptConf", [ptr("char"), ptr("char"), ptr("char"), TEXT_SIZE]
    )
    return {"texts": [str(t) for t in answer["values"][:3]], "return": answer["return"]}


def selected_name(selected: dict, known: list[str]) -> str | None:
    """The selected configuration's name: the first of its texts that NIS lists as a configuration, else None."""
    texts = (selected or {}).get("texts") or []
    return next((t for t in texts if t and t in known), None)


def get_nosepiece(client) -> dict:
    """The objective turret: ``{"present", "position", "objectives": {position: name}}``.

    Positions count from 1, as NIS counts them. Without a motorised nosepiece,
    ``present`` is False and the rest is empty.
    """
    present = bool(client.read("Stg_IsNosepiecePresent", [])["return"])
    if not present:
        return {"present": False, "position": None, "objectives": {}}
    count = client.read("Stg_GetNosepiecePositions", [])["return"]
    position = client.read("Stg_GetNosepiecePosition", [])["return"]
    if is_failure_code(count) or is_failure_code(position):
        raise NisRefused(
            f"the nosepiece answered {dr_name(count)} positions, at {dr_name(position)}"
        )
    objectives = {}
    for slot in range(1, int(count) + 1):
        answer = client.read(
            "Stg_GetNosepieceObjectiveName", [slot, ptr("char"), TEXT_SIZE]
        )
        if is_failure_code(answer["return"]):
            continue
        name = str(answer["values"][1])
        if name:
            objectives[slot] = name
    return {"present": True, "position": int(position), "objectives": objectives}


def get_pfs(client) -> dict:
    """The Perfect Focus System: ``{"present", "status", "meaning", "on"}``."""
    present = bool(client.read("Stg_IsPFSPresent", [])["return"])
    if not present:
        return {"present": False, "status": None, "meaning": "not present", "on": False}
    status = int(client.read("Stg_GetPFSStatus", [])["return"])
    return {
        "present": True,
        "status": status,
        "meaning": PFS_STATUS.get(status, "unknown"),
        "on": status in PFS_ON,
    }


def get_image_info(client) -> dict:
    """The image open in NIS now: ``{"open": True, "width", "height", "bits", "components"}``, or ``{"open": False}``.

    Asked of the current document, by an empty file name, as the NIS 6.10
    bridge did. When no image is open, NIS answers an error or a size of
    zero; either reads as ``{"open": False}``.
    """
    try:
        answer = client.read(
            "Get_ImageInfo", ["", ptr("int"), ptr("int"), ptr("int"), ptr("int")]
        )
    except NisRefused as refused:
        if refused.kind in ("not_allowed", "bad_request"):
            raise
        return {"open": False, "why": str(refused)}
    width, height, bits, components = (int(v) for v in answer["values"][1:5])
    if width <= 0 or height <= 0:
        return {"open": False, "why": f"NIS answered a size of {width} x {height}"}
    return {
        "open": True,
        "width": width,
        "height": height,
        "bits": bits,
        "components": components,
    }


def calibration_from(objective: Any, pixel: Any, aspect: Any, unit: Any) -> dict:
    """``Get_Calibration``'s four values in words: the pixel size in micrometres, or None with the reason.

    Shared by the reading below and by the capture, whose answer carries the same four values.
    """
    size = _number(pixel) if pixel is not None else 0.0
    unit = int(unit) if unit is not None else 0
    if size <= 0:
        pixel_size_um, why = None, "the image is not calibrated in NIS"
    elif unit != CALIBRATION_UNIT_UM:
        pixel_size_um, why = (
            None,
            f"NIS gives the pixel size in unit {unit}, not known to be micrometres",
        )
    else:
        pixel_size_um, why = size, None
    return {
        "objective": str(objective or ""),
        "pixel_size_um": pixel_size_um,
        "aspect": _number(aspect) if aspect else 1.0,
        "unit": unit,
        "calibrated": pixel_size_um is not None,
        "why": why,
    }


def get_calibration(client) -> dict:
    """The pixel size of the image open in NIS: see :func:`calibration_from`."""
    answer = client.read(
        "Get_Calibration", [ptr("char"), ptr("double"), ptr("double"), ptr("int")]
    )
    return calibration_from(*answer["values"][:4])


#: Every reading by name: what the profile's table of readings is built from.
def get_open_documents(client) -> list[str]:
    """The names of the documents open in NIS now, as the snap sees them before and after a capture.

    Not one of the named readings below: the driver itself never needs it,
    but the first checks at a microscope use it to show that a capture left
    the operator's own images open and untouched.
    """
    from .server import DOCUMENT_LIST_SIZE, document_names

    return document_names(
        client.read("GetOpenedDocumentList", [ptr("char"), DOCUMENT_LIST_SIZE])[
            "values"
        ][0]
    )


def nis_call(client):
    """How the table of hardware settings reaches NIS from the driver: through the client's readings."""
    return lambda fn, args: client.read(fn, args)


def get_devices(client) -> dict:
    """Every hardware setting of the table, as NIS has it now (see ``devices.discover``)."""
    return devices.discover(nis_call(client))


def get_device(client, key: str, instance: str | None = None):
    """One hardware setting's value now, for one of its devices; ``NisRefused`` when NIS gives none."""
    return devices.read(nis_call(client), devices.setting(key), instance)


def device_reading(name: str):
    """The reading for ``device/<key>`` or ``device/<key>/<device>``: a function of the client alone."""
    _, key, *rest = name.split("/", 2)
    instance = rest[0] if rest else None
    devices.tokens_for(
        devices.setting(key), instance
    )  # a wrong name is said now, not at every try
    return lambda client: get_device(client, key, instance)


READS = {
    "version": get_version,
    "xy": get_xy,
    "z": get_z,
    "stage_limits": get_stage_limits,
    "optical_configurations": get_optical_configurations,
    "selected_optical_configuration": get_selected_optical_configuration,
    "nosepiece": get_nosepiece,
    "pfs": get_pfs,
    "image_info": get_image_info,
    "calibration": get_calibration,
    "devices": get_devices,
}
