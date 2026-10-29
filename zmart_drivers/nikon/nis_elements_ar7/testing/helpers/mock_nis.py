"""A stand-in for NIS-Elements' own ``nis`` module, with a pretend microscope behind it.

Inside NIS-Elements, the bridge (``primitives/nis_python/server.py``) calls
``nis.mac.<function>(...)`` and makes arguments NIS fills in with
``nis.ptr.double()`` and the others. This file offers the same three things
(``mac``, ``ptr`` and ``log``) over a :class:`PretendMicroscope`, so the real
bridge, the real client and the whole driver run on any computer, without
NIS. Only the last step into NIS is pretended.

The pretend microscope behaves as the NIS 7 study and the NIS 6.10 simulator
showed NIS to behave, where they showed it:

- With ``hardware_on=False``, ``StgGetPosXY`` answers ``DR_NOTAVAILABLE`` (-4)
  and ``Capture`` answers -9, as steps 0 and 2 saw on NIS-Elements AR 7.01.03
  with the microscope off.
- A move takes a moment (``TRAVEL_S``) before the stage reads its target, so
  the driver's read-back windows are exercised.
- ``Capture`` makes a new document and makes it the current one; with
  ``capture_into_current`` it captures into the current document instead, as
  NIS does when its "capture creates a new document" option is off.
  ``CloseCurrentDocument`` closes only the current document.
- A captured picture is a pattern that depends on where the stage stood, and
  is different in every direction, so a test can tell where a saved picture
  was taken and whether it was turned.
- Each function checks how many arguments it was given and raises
  "Bad number of parameters" otherwise, as NIS does.

What is not known about NIS 7 yet (the plan, section 11) is pretended in the
way the plan expects; ``testing/hardware/check_hardware.py`` checks it on a
real microscope.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
Modelled on the stand-in ``nis`` module of the step 1 offline test of
thomdehoog/nis-useq-aiagent, and on the NIS 6.10 driver's ``fake_nis_api.py``.
"""

from __future__ import annotations

import inspect
import threading
import time
from types import SimpleNamespace

import numpy as np

#: The size of a pretend picture: not square, so a quarter turn shows.
WIDTH, HEIGHT = 64, 48

#: The pretend objectives, by nosepiece position, and their pixel sizes in micrometres.
OBJECTIVES = {1: "Plan Apo 10x", 2: "Plan Apo 20x", 3: "Plan Apo 60x Oil", 4: ""}
PIXEL_SIZE_UM = {1: 0.65, 2: 0.325, 3: 0.108}


class Ptr:
    """An argument NIS fills in: ``get()`` reads what was written."""

    def __init__(self, value):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class PtrTypes:
    """``nis.ptr``: one maker per type, each an empty argument for NIS to fill."""

    byte = staticmethod(lambda: Ptr(0))
    char = staticmethod(lambda: Ptr(""))
    char8 = staticmethod(lambda: Ptr(""))
    double = staticmethod(lambda: Ptr(0.0))
    dword = staticmethod(lambda: Ptr(0))
    int = staticmethod(lambda: Ptr(0))
    int64 = staticmethod(lambda: Ptr(0))
    long = staticmethod(lambda: Ptr(0))
    short = staticmethod(lambda: Ptr(0))
    word = staticmethod(lambda: Ptr(0))


class Document:
    """One image document open in the pretend NIS: its name, its pixels, how it was taken."""

    def __init__(
        self, name: str, pixels: np.ndarray, bits: int, objective: int, calibrated: bool
    ):
        self.name = name
        self.pixels = pixels
        self.bits = bits
        self.objective = objective
        self.calibrated = calibrated


class PretendMicroscope:
    """A Nikon microscope as NIS-Elements would show it: stage, focus, configurations, nosepiece, PFS, documents."""

    TRAVEL_S = 0.03

    def __init__(
        self,
        *,
        hardware_on: bool = True,
        calibrated: bool = True,
        channels: str = "mono",
        bits: int = 12,
        pfs: bool = True,
        capture_into_current: bool = False,
        saves_as_displayed: bool = False,
        optical_configurations: tuple = ("DAPI", "GFP", "Brightfield"),
    ):
        self.lock = threading.RLock()
        self.hardware_on = hardware_on
        self.calibrated = calibrated
        self.channels = channels  # "mono", "rgb" or "pages"
        self.bits = bits
        self.capture_into_current = capture_into_current
        self.saves_as_displayed = (
            saves_as_displayed  # writes 8-bit pixels, as a display would
        )
        self.xy_limits = (-50000.0, -30000.0, 50000.0, 30000.0)  # x0, y0, x1, y1
        self.z_limits = (0.0, 10000.0)
        self.x_from, self.y_from, self.z_from = 1000.0, 2000.0, 3000.0
        self.x_to, self.y_to, self.z_to = self.x_from, self.y_from, self.z_from
        self.moved_at = 0.0
        self.optical_configurations = list(optical_configurations)
        self.selected = None
        self.nosepiece = 1
        self.has_pfs = pfs
        self.pfs_on = False
        self.documents: list[Document] = []
        self.current: Document | None = None
        self.captures = 0
        self.calls: list[str] = []
        #: Function name -> the value it answers next time instead of doing its work.
        self.answer_next: dict[str, object] = {}
        #: The hardware of the table in devices.py that this pretend microscope has.
        #: What is left out here (an ND filter, an aperture, a zoom, a spinning
        #: disk, TIRF, a heating stage) answers as NIS does for absent hardware.
        self.shutters = {"EPI": 0, "DIA": 0}
        self.lamps = {"DIA LED": {"state": 0, "voltage": 3.0, "iris": 1.0, "nd": 1}}
        self.lamp_nd_steps = 4
        self.filters = {"Filter Turret 1": 1, "Emission Wheel": 1}
        self.filter_positions = 6
        self.light_path = 1
        self.light_path_positions = 4
        self.condenser = 1
        self.condenser_positions = 7
        self.pfs_offset = 100
        self.laser_lines = {
            "405 nm": [0, 0.0],
            "488 nm": [0, 0.0],
            "561 nm": [0, 0.0],
            "640 nm": [0, 0.0],
        }
        self.incubator = {"gas": 5.0, "humidity": 90.0}

    # -- the stage ---------------------------------------------------------------------------

    def _share(self) -> float:
        return min(max((time.monotonic() - self.moved_at) / self.TRAVEL_S, 0.0), 1.0)

    def position(self) -> tuple[float, float, float]:
        share = self._share()
        return (
            self.x_from + (self.x_to - self.x_from) * share,
            self.y_from + (self.y_to - self.y_from) * share,
            self.z_from + (self.z_to - self.z_from) * share,
        )

    def _start_move(self, x=None, y=None, z=None):
        # An axis that is not asked keeps going to where it was going: the
        # stage's axes move independently, as on a real microscope.
        self.x_from, self.y_from, self.z_from = self.position()
        self.x_to = self.x_to if x is None else x
        self.y_to = self.y_to if y is None else y
        self.z_to = self.z_to if z is None else z
        self.moved_at = time.monotonic()

    def place(self, x: float, y: float, z: float) -> None:
        """Put the stage at (x, y, z) at once, as an operator would with the joystick."""
        self.x_from = self.x_to = x
        self.y_from = self.y_to = y
        self.z_from = self.z_to = z

    # -- a picture -------------------------------------------------------------------------

    def picture(self) -> np.ndarray:
        """A pattern that depends on where the stage stands, different in every direction."""
        x, y, _ = self.position()
        rows, cols = np.mgrid[0:HEIGHT, 0:WIDTH]
        top = (1 << self.bits) - 1
        base = (rows * 7 + cols * 3 + int(abs(x)) // 10 + int(abs(y)) // 100) % (
            top + 1
        )
        if self.channels == "mono":
            return base.astype(np.uint16 if self.bits > 8 else np.uint8)
        layers = [(base + shift * 101) % (top + 1) for shift in range(3)]
        dtype = np.uint16 if self.bits > 8 else np.uint8
        if self.channels == "rgb":
            return np.stack(layers, axis=-1).astype(np.uint8)
        return np.stack(layers, axis=0).astype(dtype)


def _function(fn):
    """Mark a pretend NIS function: it checks its number of arguments as NIS does, and is logged."""
    count = len(inspect.signature(fn).parameters) - 1  # without the microscope itself

    def call(scope, *args):
        with scope.lock:
            scope.calls.append(fn.__name__)
            if len(args) != count:
                raise RuntimeError("Bad number of parameters")
            if fn.__name__ in scope.answer_next:
                return scope.answer_next.pop(fn.__name__)
            return fn(scope, *args)

    call.__name__ = fn.__name__
    return call


# =============================================================================
# The NIS functions the driver uses
# =============================================================================


@_function
def Get_InfoStr(scope, what, text):
    text.set("7.01.03 (Build 2233)" if what == 1 else "")
    return 1


@_function
def StgGetPosXY(scope, x, y):
    if not scope.hardware_on:
        return -4
    px, py, _ = scope.position()
    x.set(px)
    y.set(py)
    return 1


@_function
def StgGetPosZ(scope, z, device):
    z.set(scope.position()[2])
    return 1


@_function
def StgXY_GetLimits(scope, x0, y0, x1, y1):
    for ptr, value in zip((x0, y0, x1, y1), scope.xy_limits):
        ptr.set(value)
    return 1


@_function
def StgZ_GetLimits(scope, low, high):
    low.set(scope.z_limits[0])
    high.set(scope.z_limits[1])
    return 1


@_function
def StgMoveXY(scope, x, y, relative):
    if not scope.hardware_on:
        return -4
    if relative != 0:
        return -2
    x0, y0, x1, y1 = scope.xy_limits
    if not (x0 <= x <= x1 and y0 <= y <= y1):
        return -2
    scope._start_move(x=float(x), y=float(y))
    return 1


@_function
def StgMoveZ(scope, z, relative):
    if relative != 0:
        return -2
    if not scope.z_limits[0] <= z <= scope.z_limits[1]:
        return -2
    scope._start_move(z=float(z))
    return 1


@_function
def GetOptConfCount(scope):
    return len(scope.optical_configurations)


@_function
def GetOptConfName(scope, index, text, size):
    text.set(scope.optical_configurations[index])
    return 1


@_function
def GetSelectedOptConf(scope, group, name, other, size):
    group.set("")
    name.set(scope.selected or "")
    other.set("")
    return 1 if scope.selected else 0


@_function
def SelectOptConf(scope, name):
    if name not in scope.optical_configurations:
        raise RuntimeError(
            f"Cannot Evaluate the Expression: no optical configuration {name!r}"
        )
    scope.selected = name
    return 1


@_function
def Stg_IsNosepiecePresent(scope):
    return 1


@_function
def Stg_GetNosepiecePositions(scope):
    return len(OBJECTIVES)


@_function
def Stg_GetNosepiecePosition(scope):
    return scope.nosepiece


@_function
def Stg_GetNosepieceObjectiveName(scope, position, text, size):
    if position not in OBJECTIVES:
        return -2
    text.set(OBJECTIVES[position])
    return 1


@_function
def Stg_SetNosepiecePosition(scope, position):
    if position not in OBJECTIVES or not OBJECTIVES[position]:
        return -2
    scope.nosepiece = position
    return 1


@_function
def Stg_IsPFSPresent(scope):
    return 1 if scope.has_pfs else 0


@_function
def Stg_GetPFSStatus(scope):
    return 1 if scope.pfs_on else 0


@_function
def Get_ImageInfo(scope, name, width, height, bits, components):
    document = scope.current
    if document is None:
        return 0
    pixels = document.pixels
    width.set(WIDTH)
    height.set(HEIGHT)
    bits.set(document.bits)
    components.set(3 if pixels.ndim == 3 else 1)
    return 1


@_function
def Get_Calibration(scope, objective, pixel, aspect, unit):
    document = scope.current
    if document is None or not document.calibrated:
        objective.set("Uncalibrated")
        pixel.set(0.0)
        aspect.set(1.0)
        unit.set(0)
        return 0.0
    objective.set(OBJECTIVES[document.objective])
    pixel.set(PIXEL_SIZE_UM[document.objective])
    aspect.set(1.0)
    unit.set(2)
    return PIXEL_SIZE_UM[document.objective]


@_function
def GetOpenedDocumentList(scope, text, size):
    text.set(";".join(document.name for document in scope.documents))
    return len(scope.documents)


@_function
def ActivateDocument(scope, name):
    for document in scope.documents:
        if document.name == name:
            scope.current = document
            return 1
    return -1


@_function
def Capture(scope):
    if not scope.hardware_on:
        return -9
    scope.captures += 1
    pixels = scope.picture()
    bits = 8 if scope.channels == "rgb" else scope.bits
    if scope.capture_into_current and scope.current is not None:
        scope.current.pixels = pixels
        return 1
    document = Document(
        f"Capture {scope.captures}", pixels, bits, scope.nosepiece, scope.calibrated
    )
    scope.documents.append(document)
    scope.current = document
    return 1


@_function
def ImageSaveAs(scope, path, file_type, compression):
    import tifffile

    document = scope.current
    if document is None:
        return -1
    pixels = document.pixels
    if scope.saves_as_displayed and pixels.dtype != np.uint8:
        pixels = (pixels >> max(document.bits - 8, 0)).astype(np.uint8)
    description = (
        f"NIS-Elements 7.01.03 (pretend); objective {OBJECTIVES[document.objective]}"
    )
    if pixels.ndim == 3 and pixels.shape[-1] == 3:
        tifffile.imwrite(
            path, pixels, photometric="rgb", description=description, metadata=None
        )
    elif pixels.ndim == 3:
        tifffile.imwrite(
            path, pixels, photometric="minisblack", metadata={"axes": "CYX"}
        )
    else:
        tifffile.imwrite(
            path,
            pixels,
            photometric="minisblack",
            description=description,
            metadata=None,
        )
    return 1


@_function
def SaveImageFullDescription(scope, path):
    """The document's whole description, as NIS writes it: one setting per line (UTF-16, as Windows tools often do)."""
    document = scope.current
    if document is None:
        return -1
    lines = [
        f"Name: {document.name}",
        "Camera: Pretend sCMOS",
        "Exposure: 100 ms",
        "Binning: 1x1",
        f"Objective: {OBJECTIVES[document.objective]}",
        f"Bit depth: {document.bits}",
    ]
    with open(path, "w", encoding="utf-16") as handle:
        handle.write("\n".join(lines))
    return 1


@_function
def CloseCurrentDocument(scope, mode):
    document = scope.current
    if document is None:
        return -1
    scope.documents.remove(document)
    scope.current = scope.documents[-1] if scope.documents else None
    return 1


# =============================================================================
# The hardware of the table in devices.py
# =============================================================================
#
# These follow the arguments the table expects (a count or a position as the
# return value, a name through an index, a text place and its size; the
# setting's device by its name first). That is what is known of NIS 7 so far;
# the hardware check confirms it at a microscope. A device the pretend
# microscope does not have answers DR_NOTAVAILABLE (-4), or 0 when asked
# whether it is there.

NOT_AVAILABLE = -4


def _named(table, index, text):
    names = list(table)
    if not 0 <= index < len(names):
        return -2
    text.set(names[index])
    return 1


def _set_from(table, name, value, key=None):
    if name not in table:
        return -2
    if key is None:
        table[name] = value
    else:
        table[name][key] = value
    return 1


@_function
def Stg_GetShutterCount(scope):
    return len(scope.shutters)


@_function
def Stg_GetShutterName(scope, index, text, size):
    return _named(scope.shutters, index, text)


@_function
def Stg_GetShutterStateEx(scope, name, state):
    if name not in scope.shutters:
        return -2
    state.set(scope.shutters[name])
    return 1


@_function
def Stg_SetShutterStateEx(scope, name, state):
    return _set_from(scope.shutters, name, int(state))


@_function
def Stg_Light_GetCount(scope):
    return len(scope.lamps)


@_function
def Stg_Light_GetName(scope, index, text, size):
    return _named(scope.lamps, index, text)


def _lamp_reader(key):
    def read(scope, name, out):
        if name not in scope.lamps:
            return -2
        out.set(scope.lamps[name][key])
        return 1

    return read


def _lamp_writer(key, kind):
    def write(scope, name, value):
        return _set_from(scope.lamps, name, kind(value), key)

    return write


def _with_name(fn, name):
    fn.__name__ = name
    return _function(fn)


Stg_Light_GetState = _with_name(_lamp_reader("state"), "Stg_Light_GetState")
Stg_Light_SetState = _with_name(_lamp_writer("state", int), "Stg_Light_SetState")
Stg_Light_GetVoltage = _with_name(_lamp_reader("voltage"), "Stg_Light_GetVoltage")
Stg_Light_SetVoltage = _with_name(
    _lamp_writer("voltage", float), "Stg_Light_SetVoltage"
)
Stg_Light_GetIrisIntensity = _with_name(
    _lamp_reader("iris"), "Stg_Light_GetIrisIntensity"
)
Stg_Light_SetIrisIntensity = _with_name(
    _lamp_writer("iris", float), "Stg_Light_SetIrisIntensity"
)
Stg_Light_GetNDFilterPosition = _with_name(
    _lamp_reader("nd"), "Stg_Light_GetNDFilterPosition"
)
Stg_Light_SetNDFilterPosition = _with_name(
    _lamp_writer("nd", int), "Stg_Light_SetNDFilterPosition"
)


@_function
def Stg_Light_GetNDFilterSteps(scope, name):
    return scope.lamp_nd_steps if name in scope.lamps else -2


@_function
def Stg_Filter_GetCount(scope):
    return len(scope.filters)


@_function
def Stg_Filter_GetName(scope, index, text, size):
    return _named(scope.filters, index, text)


@_function
def Stg_Filter_GetPosition(scope, name, position):
    if name not in scope.filters:
        return -2
    position.set(scope.filters[name])
    return 1


@_function
def Stg_Filter_SetPosition(scope, name, position):
    if not 1 <= int(position) <= scope.filter_positions:
        return -2
    return _set_from(scope.filters, name, int(position))


@_function
def Stg_Filter_GetPositionCount(scope, name):
    return scope.filter_positions if name in scope.filters else -2


@_function
def Stg_IsLightPathPresent(scope):
    return 1


@_function
def Stg_GetLightPathPosition(scope):
    return scope.light_path


@_function
def Stg_SetLightPathPosition(scope, position):
    if not 1 <= int(position) <= scope.light_path_positions:
        return -2
    scope.light_path = int(position)
    return 1


@_function
def Stg_GetLightPathPositions(scope):
    return scope.light_path_positions


@_function
def Stg_IsCondenserPresent(scope):
    return 1


@_function
def Stg_GetCondenserPosition(scope):
    return scope.condenser


@_function
def Stg_SetCondenserPosition(scope, position):
    if not 1 <= int(position) <= scope.condenser_positions:
        return -2
    scope.condenser = int(position)
    return 1


@_function
def Stg_GetCondenserPositions(scope):
    return scope.condenser_positions


@_function
def Stg_SetPFSStatus(scope, on):
    if not scope.has_pfs:
        return NOT_AVAILABLE
    scope.pfs_on = bool(on)
    return 1


@_function
def Stg_GetPFSLensOffsetPosition(scope):
    return scope.pfs_offset if scope.has_pfs else NOT_AVAILABLE


@_function
def Stg_SetPFSLensOffsetPosition(scope, offset):
    if not scope.has_pfs:
        return NOT_AVAILABLE
    scope.pfs_offset = int(offset)
    return 1


@_function
def Stg_GetMultiLaserCount(scope):
    return 1 if scope.laser_lines else 0


@_function
def Stg_GetMultiLaserNumLines(scope, laser):
    return len(scope.laser_lines) if laser == 0 else -2


def _line(scope, laser, line):
    names = list(scope.laser_lines)
    if laser != 0 or not 0 <= line < len(names):
        return None
    return scope.laser_lines[names[line]]


@_function
def Stg_GetMultiLaserLineName(scope, laser, line, text, size):
    names = list(scope.laser_lines)
    if laser != 0 or not 0 <= line < len(names):
        return -2
    text.set(names[line])
    return 1


@_function
def Stg_GetMultiLaserLineUsed(scope, laser, line, used):
    found = _line(scope, laser, line)
    if found is None:
        return -2
    used.set(found[0])
    return 1


@_function
def Stg_SetMultiLaserLineUsed(scope, laser, line, used):
    found = _line(scope, laser, line)
    if found is None:
        return -2
    found[0] = int(used)
    return 1


@_function
def Stg_GetMultiLaserPower(scope, laser, line, power):
    found = _line(scope, laser, line)
    if found is None:
        return -2
    power.set(found[1])
    return 1


@_function
def Stg_SetMultiLaserPower(scope, laser, line, power):
    found = _line(scope, laser, line)
    if found is None or not 0 <= float(power) <= 100:
        return -2
    found[1] = float(power)
    return 1


@_function
def Stg_Incubator_GetTargetGas(scope, gas):
    gas.set(scope.incubator["gas"])
    return 1


@_function
def Stg_Incubator_SetTargetGas(scope, gas):
    scope.incubator["gas"] = float(gas)
    return 1


@_function
def Stg_Incubator_GetTargetHumidity(scope, humidity):
    humidity.set(scope.incubator["humidity"])
    return 1


@_function
def Stg_Incubator_SetTargetHumidity(scope, humidity):
    scope.incubator["humidity"] = float(humidity)
    return 1


def _absent_single(name):
    """A device this pretend microscope does not have: asked whether it is there, it says no."""
    return _with_name(lambda scope: 0, name)


def _not_available(name, count):
    """A function of absent hardware: it answers DR_NOTAVAILABLE, with the number of arguments the table gives it."""
    parameters = ", ".join(f"a{i}" for i in range(count))
    namespace = {}
    exec(  # noqa: S102 -- a stand-in function with the number of arguments the table gives
        f"def {name}(scope{', ' if count else ''}{parameters}):\n    return {NOT_AVAILABLE}",
        namespace,
    )
    return _function(namespace[name])


ABSENT = [
    _absent_single(name)
    for name in (
        "Stg_IsNDFilterPresent",
        "Stg_IsAperturePresent",
        "Stg_IsAnalyzerPresent",
        "Stg_IsZoomPresent",
    )
] + [
    _not_available(name, count)
    for name, count in (
        ("Stg_GetNDFilterPosition", 0),
        ("Stg_SetNDFilterPosition", 1),
        ("Stg_GetNDFilterSteps", 0),
        ("Stg_GetAperturePosition", 1),
        ("Stg_SetAperturePosition", 1),
        ("Stg_GetAnalyzerState", 0),
        ("Stg_SetAnalyzerState", 1),
        ("Stg_GetZoomPosition", 1),
        ("Stg_SetZoomPosition", 1),
        ("Stg_GetRelayLensPosition", 0),
        ("Stg_SetRelayLensPosition", 1),
        ("Stg_CorrectionCollar_GetPosition", 1),
        ("Stg_CorrectionCollar_SetPosition", 1),
        ("Stg_GetCSUShutterState", 0),
        ("Stg_SetCSUShutterState", 1),
        ("Stg_GetDiskSpeed", 0),
        ("Stg_SetDiskSpeed", 1),
        ("Stg_GetTIRFPosition", 1),
        ("Stg_SetTIRFPosition", 1),
        ("Stg_HeatStage_GetCount", 0),
        ("Stg_HeatStage_GetName", 3),
        ("Stg_HeatStage_GetCurrentTemperature", 2),
        ("Stg_HeatStage_SetTemperature", 2),
    )
]

FUNCTIONS = {
    function.__name__: function
    for function in (
        Get_InfoStr,
        StgGetPosXY,
        StgGetPosZ,
        StgXY_GetLimits,
        StgZ_GetLimits,
        StgMoveXY,
        StgMoveZ,
        GetOptConfCount,
        GetOptConfName,
        GetSelectedOptConf,
        SelectOptConf,
        Stg_IsNosepiecePresent,
        Stg_GetNosepiecePositions,
        Stg_GetNosepiecePosition,
        Stg_GetNosepieceObjectiveName,
        Stg_SetNosepiecePosition,
        Stg_IsPFSPresent,
        Stg_GetPFSStatus,
        Get_ImageInfo,
        Get_Calibration,
        GetOpenedDocumentList,
        ActivateDocument,
        Capture,
        ImageSaveAs,
        CloseCurrentDocument,
        SaveImageFullDescription,
        Stg_GetShutterCount,
        Stg_GetShutterName,
        Stg_GetShutterStateEx,
        Stg_SetShutterStateEx,
        Stg_Light_GetCount,
        Stg_Light_GetName,
        Stg_Light_GetState,
        Stg_Light_SetState,
        Stg_Light_GetVoltage,
        Stg_Light_SetVoltage,
        Stg_Light_GetIrisIntensity,
        Stg_Light_SetIrisIntensity,
        Stg_Light_GetNDFilterPosition,
        Stg_Light_SetNDFilterPosition,
        Stg_Light_GetNDFilterSteps,
        Stg_Filter_GetCount,
        Stg_Filter_GetName,
        Stg_Filter_GetPosition,
        Stg_Filter_SetPosition,
        Stg_Filter_GetPositionCount,
        Stg_IsLightPathPresent,
        Stg_GetLightPathPosition,
        Stg_SetLightPathPosition,
        Stg_GetLightPathPositions,
        Stg_IsCondenserPresent,
        Stg_GetCondenserPosition,
        Stg_SetCondenserPosition,
        Stg_GetCondenserPositions,
        Stg_SetPFSStatus,
        Stg_GetPFSLensOffsetPosition,
        Stg_SetPFSLensOffsetPosition,
        Stg_GetMultiLaserCount,
        Stg_GetMultiLaserNumLines,
        Stg_GetMultiLaserLineName,
        Stg_GetMultiLaserLineUsed,
        Stg_SetMultiLaserLineUsed,
        Stg_GetMultiLaserPower,
        Stg_SetMultiLaserPower,
        Stg_Incubator_GetTargetGas,
        Stg_Incubator_SetTargetGas,
        Stg_Incubator_GetTargetHumidity,
        Stg_Incubator_SetTargetHumidity,
        *ABSENT,
    )
}


class Mac:
    """``nis.mac``: every pretend function by name; an unknown name raises ``AttributeError``, as NIS's does."""

    def __init__(self, scope: PretendMicroscope):
        self._scope = scope

    def __getattr__(self, name):
        function = FUNCTIONS.get(name)
        if function is None:
            raise AttributeError(f"'Macro' object has no attribute '{name}'")
        return lambda *args: function(self._scope, *args)


def make_nis(scope: PretendMicroscope | None = None) -> SimpleNamespace:
    """A stand-in ``nis`` module over *scope* (a new pretend microscope when None)."""
    scope = scope or PretendMicroscope()
    logged: list[str] = []
    return SimpleNamespace(
        mac=Mac(scope), ptr=PtrTypes, log=logged.append, logged=logged, scope=scope
    )


def open_operator_image(
    scope: PretendMicroscope, name: str = "operator.nd2"
) -> Document:
    """Open an image of the operator's own in the pretend NIS, and make it current."""
    document = Document(
        name,
        np.full((HEIGHT, WIDTH), 7, dtype=np.uint16),
        scope.bits,
        scope.nosepiece,
        scope.calibrated,
    )
    scope.documents.append(document)
    scope.current = document
    return document
