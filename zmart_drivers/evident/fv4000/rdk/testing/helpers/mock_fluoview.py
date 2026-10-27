"""A pretend FLUOVIEW: the RDK's calls answered in Python, so the driver can be tested without a microscope.

The mock behaves as the manual describes FLUOVIEW: every call answers a
dictionary with a ``result`` code; positions are nanometres; a stage move
takes a moment to arrive; ``startProtocol`` turns the state to ``SCANNING``
for a while, writes a picture into a pretend folder and names its file; the
IDA calls open that file in groups, levels and areas and hand its pixels out
in pieces; a call that is not allowed in the present state answers
``UNAVAILABLE`` (section 3.6 of the manual); and a wrong parameter answers
``INVALID_PARAMETERS``.

The pretend sample is a field of bright beads. A picture shows the part of
the field under the stage, so a move along X or Y shifts the picture by a
known number of pixels, which the orientation measurement can find. The mock
can be told to scan the field turned or mirrored (``orientation``), as a real
scanner may be mounted.

Use it in-process through :class:`MockRdkClient`, which has the same ``call``
as the real client, or serve it as a real XML-RPC server with
:func:`serve`, so the real client and the wire format are tested too.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import itertools
import threading
import time
from dataclasses import dataclass, field
from xmlrpc.server import SimpleXMLRPCRequestHandler, SimpleXMLRPCServer

import numpy as np

from zmart_drivers.evident.fv4000.rdk.primitives.rdk.client import ConnectionLost

OK = {"result": "OK"}
FIELD_SIZE = 2048
IMAGE_SIZE = 256
BIT_COUNT = 12

#: Settings that may change while a scan runs (the manual's section 3.6); the others answer UNAVAILABLE then.
ALLOWED_WHILE_SCANNING = {
    "LSM_CHANNEL_HV_SETTING",
    "LSM_CHANNEL_GAIN_SETTING",
    "LSM_CHANNEL_OFFSET_SETTING",
    "LSM_CHANNEL_DIGITALGAIN_SETTING",
    "LSM_LASER_INTENSITY_SETTING",
    "XY_STAGE_POSITION_SETTING",
    "Z_STAGE_POSITION_SETTING",
}


def _error(code: str) -> dict:
    return {"result": code}


def make_field(seed: int = 7) -> np.ndarray:
    """The pretend sample: a square field of bright beads on a faint background, as 12-bit pixels."""
    rng = np.random.default_rng(seed)
    field = rng.integers(40, 80, size=(FIELD_SIZE, FIELD_SIZE)).astype(np.int32)
    for _ in range(6000):
        r, c = rng.integers(2, FIELD_SIZE - 2, size=2)
        bright = int(rng.integers(1500, 3900))
        field[r - 1 : r + 2, c - 1 : c + 2] += bright // 3
        field[r, c] += bright
    return np.clip(field, 0, (1 << BIT_COUNT) - 1).astype(np.uint16)


@dataclass
class Channel:
    enabled: bool = True
    hv: int = 500
    gain: float = 1.0
    offset: int = 0
    digital_gain: float = 1.0
    lasers: list = field(default_factory=list)
    pmt: bool = True


@dataclass
class PretendFile:
    """A picture FLUOVIEW wrote: its planes by (t, channel id, z, lambda) and what it knows about them."""

    path: str
    channels: list
    width: int
    height: int
    size_t: int
    size_z: int
    z_positions_um: list
    t_step_s: float | None
    pixel_um: float
    planes: dict
    finished_at: float


class MockFluoview:
    """The pretend FLUOVIEW. ``call(method, payload)`` answers as the RDK would."""

    VERSION = "2.1.1.70"

    def __init__(
        self,
        *,
        travel_s: float = 0.05,
        scan_s: float = 0.3,
        latency_s: float = 0.0,
        field_seed: int = 7,
    ):
        self.travel_s = travel_s
        self.scan_s = scan_s
        self.latency_s = latency_s
        self.lock = threading.RLock()
        self.calls: list[tuple[str, dict | None]] = []
        self.connected = True
        self.remote_mode = "LOCAL"
        #: How many of the next setParameter or startProtocol calls answer UNAVAILABLE, to test retries.
        self.busy_answers = 0
        #: When True, startProtocol answers OK and names a file, but no scan ever runs (a FLUOVIEW that silently did nothing).
        self.silent_start = False
        # The stage, in nanometres, with its motion.
        self.x_from = self.x_to = 10_000_000
        self.y_from = self.y_to = 8_000_000
        self.z_from = self.z_to = 2_500_000
        self.moved_at = 0.0
        self.piezo_nm = 0
        self.z_origin_nm = 2_500_000
        self.escape_requests: list[bool] = []
        # The optics.
        self.objectives = [(0, "UPLXAPO10X"), (1, "UPLXAPO20X"), (2, "UPLXAPO60XO")]
        self.pixel_um_by_slot = {0: 1.242, 1: 0.621, 2: 0.207}
        self.objective_slot = 0
        self.channels = {
            "CH1": Channel(lasers=["LASER_488"]),
            "CH2": Channel(lasers=["LASER_561"], hv=550),
            "CH3": Channel(enabled=False, pmt=False, lasers=["LASER_640"]),
        }
        self.lasers = {
            "LASER_488": {"wavelength": 488, "enabled": True, "intensity": 2.0},
            "LASER_561": {"wavelength": 561, "enabled": True, "intensity": 1.5},
            "LASER_640": {"wavelength": 640, "enabled": False, "intensity": 0.0},
        }
        self.laser_range = (0.1, 100.0)
        self.z_stack = {
            "enabled": False,
            "slices": 5,
            "step_nm": 1_000,
            "start_nm": -2_000,
            "end_nm": 2_000,
            "range_nm": 4_000,
            "type": "START_END",
        }
        self.time_lapse = {"enabled": False, "repeat": 3, "interval": 1, "unit": 0}
        self.scanner_type = "GALVANO"
        self.scanner_mode = "ONEWAY"
        self.brightness_adjustment = {"enabled": False, "points": 0}
        self.matl_groups: dict[str, dict] = {}
        self.loaded_parameters: list[str] = []
        # How the scanner is mounted: the picture is the stage-aligned view turned by this.
        self.orientation = (0, False)
        self.field = make_field(field_seed)
        # The scan and the files it writes.
        self.state = "IDLING"
        self.scan_started_at: float | None = None
        self.scan_ends_at: float | None = None
        self.files: dict[str, PretendFile] = {}
        self.acquiring: list[str] = []
        self.image_counter = itertools.count(1)
        self.handles: dict[str, tuple] = {}
        self.handle_counter = itertools.count(1)
        self.body_piece = 1 << 20
        self.next_bodies: dict[str, bytes] = {}
        self.repeat_scanning = False

    # --- time and motion ----------------------------------------------------------------

    def _settle_scan(self) -> None:
        if self.scan_ends_at is not None and time.monotonic() >= self.scan_ends_at:
            self.scan_ends_at = None
            self.scan_started_at = None
            self.state = "IDLING"
            for path in self.acquiring:
                self.files[path].finished_at = time.monotonic()
            self.acquiring = []

    def _now(self, a, b):
        share = (
            min(max((time.monotonic() - self.moved_at) / self.travel_s, 0.0), 1.0)
            if self.travel_s > 0
            else 1.0
        )
        return round(a + (b - a) * share)

    def x_nm(self):
        return self._now(self.x_from, self.x_to)

    def y_nm(self):
        return self._now(self.y_from, self.y_to)

    def z_nm(self):
        return self._now(self.z_from, self.z_to)

    def at_rest(self):
        """Where the stage and focus motor will stand once the current travel is over, as (x, y, z) in nm.

        The driver confirms a move as soon as the reading is within its tolerance,
        which can be a fraction of a micrometre before the pretend motor stops. Tests
        that care about the exact destination ask here instead of the live reading.
        """
        return (self.x_to, self.y_to, self.z_to)

    def _move(self, x=None, y=None, z=None):
        self.x_from, self.y_from, self.z_from = self.x_nm(), self.y_nm(), self.z_nm()
        # An axis that is not named keeps its own destination: the stage and the focus motor are
        # separate drives, so a focus move does not interrupt an XY move already under way.
        self.x_to = self.x_to if x is None else int(x)
        self.y_to = self.y_to if y is None else int(y)
        self.z_to = self.z_to if z is None else int(z)
        self.moved_at = time.monotonic()

    # --- the one door --------------------------------------------------------------------

    def call(self, method: str, payload: dict | None = None) -> dict:
        if not self.connected:
            raise ConnectionLost("the pretend FLUOVIEW is closed")
        if self.latency_s:
            time.sleep(self.latency_s)
        with self.lock:
            self.calls.append((method, dict(payload) if payload else None))
            self._settle_scan()
            handler = getattr(self, "_" + method.replace(".", "_"), None)
            if handler is None:
                raise ValueError(f"the pretend FLUOVIEW has no call {method!r}")
            return handler(dict(payload or {}))

    # --- Maintenance, Remote, Repeat, Protocol ------------------------------------------

    def _Maintenance_getVersion(self, payload):
        return {**OK, "version": self.VERSION}

    def _Remote_setRemoteMode(self, payload):
        if payload.get("mode") not in ("LOCAL", "REMOTE"):
            return _error("INVALID_PARAMETERS")
        self.remote_mode = payload["mode"]
        return dict(OK)

    def _Repeat_start(self, payload):
        if self.state != "IDLING":
            return _error("UNAVAILABLE")
        self.repeat_scanning = True
        # The manual's table allows Repeat.stop only in RESTING or WAITING, during a live
        # scan; the pretend FLUOVIEW therefore shows a live scan as RESTING.
        self.state = "RESTING"
        return dict(OK)

    def _Repeat_stop(self, payload):
        if not self.repeat_scanning or self.state not in ("RESTING", "WAITING"):
            return _error("UNAVAILABLE")
        self.repeat_scanning = False
        self.state = "IDLING"
        return dict(OK)

    def _Protocol_startProtocol(self, payload):
        kind = payload.get("executionType")
        if kind not in ("EXECUTION_TYPE_MANUAL_MAIN", "EXECUTION_TYPE_MATL"):
            return _error("INVALID_PARAMETERS")
        if self.busy_answers > 0:
            self.busy_answers -= 1
            return _error("UNAVAILABLE")
        if self.state != "IDLING":
            return _error("UNAVAILABLE")
        pretend = self._take_picture()
        self.files[pretend.path] = pretend
        if self.silent_start:
            return {**OK, "wait": 0, "targetName": [{"name": pretend.path}]}
        self.acquiring = [pretend.path]
        self.state = "SCANNING"
        self.scan_started_at = time.monotonic()
        self.scan_ends_at = self.scan_started_at + self.scan_s
        return {
            **OK,
            "wait": round(self.scan_s),
            "targetName": [{"name": pretend.path}],
        }

    def _Protocol_stopProtocol(self, payload):
        if self.state == "IDLING":
            return _error("UNAVAILABLE")
        self.scan_ends_at = time.monotonic()
        self._settle_scan()
        return dict(OK)

    def _Protocol_getProtocolProgress(self, payload):
        size_z = self.z_stack["slices"] if self.z_stack["enabled"] else 1
        size_t = self.time_lapse["repeat"] if self.time_lapse["enabled"] else 1
        done = self.state == "IDLING"
        return {
            **OK,
            "state": self.state,
            "series": {
                "totalZ": size_z,
                "completeZ": size_z if done else 0,
                "totalT": size_t,
                "completeT": size_t if done else 0,
                "totalL": 1,
                "completeL": 1 if done else 0,
            },
        }

    def _Protocol_getAcquiringFiles(self, payload):
        return {**OK, "targetName": [{"name": path} for path in self.acquiring]}

    # --- Canvas -------------------------------------------------------------------------------

    def _Canvas_getCanvasIdList(self, payload):
        return {
            **OK,
            "idList": [
                f"canvas-{name}"
                for name, channel in self.channels.items()
                if channel.enabled
            ],
        }

    def _Canvas_getSupportedImageFileFormat(self, payload):
        return {**OK, "formatList": ["BMP", "PNG", "JPG"]}

    def _Canvas_getSupportedCompressType(self, payload):
        return {**OK, "compressionTypeList": ["NONE"]}

    def _Canvas_getSupportedColorType(self, payload):
        return {**OK, "colorType": ["GRAYSCALE", "COLOR"]}

    # --- Parameter --------------------------------------------------------------------------------

    def _Parameter_loadParameter(self, payload):
        if self.state != "IDLING":
            return _error("UNAVAILABLE")
        path = payload.get("targetName")
        if path not in self.files:
            return _error("FILE_NOT_FOUND")
        self.loaded_parameters.append(path)
        return dict(OK)

    def _Parameter_getParameter(self, payload):
        setting = payload.get("settingId")
        getter = getattr(self, "_get_" + str(setting), None)
        if getter is None:
            return _error("INVALID_PARAMETERS")
        return getter(payload)

    def _Parameter_setParameter(self, payload):
        setting = payload.get("settingId")
        setter = getattr(self, "_set_" + str(setting), None)
        if setter is None:
            return _error("INVALID_PARAMETERS")
        if self.busy_answers > 0:
            self.busy_answers -= 1
            return _error("UNAVAILABLE")
        if self.state != "IDLING" and setting not in ALLOWED_WHILE_SCANNING:
            return _error("UNAVAILABLE")
        return setter(payload)

    # the stage and the focus
    def _get_XY_STAGE_POSITION_SETTING(self, payload):
        return {**OK, "x": self.x_nm(), "y": self.y_nm()}

    def _set_XY_STAGE_POSITION_SETTING(self, payload):
        if not all(isinstance(payload.get(k), int) for k in ("x", "y")):
            return _error("INVALID_PARAMETERS")
        self.escape_requests.append(bool(payload.get("escapeEnabled", False)))
        self._move(x=payload["x"], y=payload["y"])
        return dict(OK)

    def _get_Z_STAGE_POSITION_SETTING(self, payload):
        return {**OK, "stagePosition": self.z_nm()}

    def _set_Z_STAGE_POSITION_SETTING(self, payload):
        if not isinstance(payload.get("stagePosition"), int):
            return _error("INVALID_PARAMETERS")
        self._move(z=payload["stagePosition"])
        return dict(OK)

    def _get_LSM_Z_MOTOR_POSITION_SETTING(self, payload):
        return {**OK, "zPosition": self.z_nm() - self.z_origin_nm}

    def _set_LSM_Z_MOTOR_POSITION_SETTING(self, payload):
        self._move(z=self.z_origin_nm + int(payload["zPosition"]))
        return dict(OK)

    def _get_LSM_Z_PIEZO_POSITION_SETTING(self, payload):
        return {**OK, "zPosition": self.piezo_nm}

    def _set_LSM_Z_PIEZO_POSITION_SETTING(self, payload):
        if not isinstance(payload.get("zPosition"), int):
            return _error("INVALID_PARAMETERS")
        self.piezo_nm = payload["zPosition"]
        return dict(OK)

    def _get_LSM_Z_COORDINATE_BASE_SETTING(self, payload):
        return {**OK, "originPosition": self.z_origin_nm}

    def _set_LSM_Z_COORDINATE_BASE_SETTING(self, payload):
        self.z_origin_nm = int(payload["originPosition"])
        return dict(OK)

    # the objectives
    def _get_LSM_MICROSCOPE_OBJECTIVE_LENS_LIST_SETTING(self, payload):
        return {
            **OK,
            "revolvers": [
                {"revolverIndex": slot, "lensName": name}
                for slot, name in self.objectives
            ],
        }

    def _get_LSM_MICROSCOPE_OBJECTIVE_LENS_CURRENT_SETTING(self, payload):
        return {
            **OK,
            "revolverIndex": self.objective_slot,
            "lensName": dict(self.objectives)[self.objective_slot],
        }

    def _set_LSM_MICROSCOPE_OBJECTIVE_LENS_CURRENT_SETTING(self, payload):
        # The manual's set takes the slot as a string.
        index = payload.get("revolverIndex")
        if not isinstance(index, str) or not index.isdigit():
            return _error("INVALID_PARAMETERS")
        slot = int(index)
        if slot not in dict(self.objectives):
            return _error("INVALID_PARAMETERS")
        self.objective_slot = slot
        return dict(OK)

    # the channels
    def _channel(self, payload):
        channel = self.channels.get(payload.get("channelId"))
        return channel

    def _get_LSM_CHANNEL_LIST_SETTING(self, payload):
        return {**OK, "channelList": list(self.channels)}

    def _get_LSM_CHANNEL_ENABLE_SETTING(self, payload):
        channel = self._channel(payload)
        return (
            {**OK, "enable": channel.enabled}
            if channel
            else _error("INVALID_PARAMETERS")
        )

    def _set_LSM_CHANNEL_ENABLE_SETTING(self, payload):
        channel = self._channel(payload)
        if channel is None or not isinstance(payload.get("enable"), bool):
            return _error("INVALID_PARAMETERS")
        channel.enabled = payload["enable"]
        return dict(OK)

    def _pmt_value(self, payload, name):
        channel = self._channel(payload)
        if channel is None:
            return None, _error("INVALID_PARAMETERS")
        if not channel.pmt:
            return None, _error("UNSUPPORTED_PARAMETERS")
        return channel, None

    def _get_LSM_CHANNEL_HV_SETTING(self, payload):
        channel, error = self._pmt_value(payload, "hv")
        return error or {**OK, "hv": channel.hv}

    def _set_LSM_CHANNEL_HV_SETTING(self, payload):
        channel, error = self._pmt_value(payload, "hv")
        if error:
            return error
        if not isinstance(payload.get("hv"), int):
            return _error("INVALID_PARAMETERS")
        channel.hv = payload["hv"]
        return dict(OK)

    def _get_LSM_CHANNEL_GAIN_SETTING(self, payload):
        channel, error = self._pmt_value(payload, "gain")
        return error or {**OK, "gain": channel.gain}

    def _set_LSM_CHANNEL_GAIN_SETTING(self, payload):
        channel, error = self._pmt_value(payload, "gain")
        if error:
            return error
        channel.gain = float(payload["gain"])
        return dict(OK)

    def _get_LSM_CHANNEL_OFFSET_SETTING(self, payload):
        channel, error = self._pmt_value(payload, "offset")
        return error or {**OK, "offset": channel.offset}

    def _set_LSM_CHANNEL_OFFSET_SETTING(self, payload):
        channel, error = self._pmt_value(payload, "offset")
        if error:
            return error
        channel.offset = int(payload["offset"])
        return dict(OK)

    def _get_LSM_CHANNEL_DIGITALGAIN_SETTING(self, payload):
        channel = self._channel(payload)
        return (
            {**OK, "gain": channel.digital_gain}
            if channel
            else _error("INVALID_PARAMETERS")
        )

    def _set_LSM_CHANNEL_DIGITALGAIN_SETTING(self, payload):
        channel = self._channel(payload)
        if channel is None:
            return _error("INVALID_PARAMETERS")
        channel.digital_gain = float(payload["gain"])
        return dict(OK)

    def _get_LSM_CHANNEL_LASER_SETTING(self, payload):
        channel = self._channel(payload)
        if channel is None:
            return _error("INVALID_PARAMETERS")
        return {
            **OK,
            "laser": [
                {
                    "imagingDeviceMode": "IMAGING",
                    "laserScannerMode": "MAIN",
                    "laserId": laser,
                }
                for laser in channel.lasers
            ],
        }

    def _set_LSM_CHANNEL_LASER_SETTING(self, payload):
        channel = self._channel(payload)
        lasers = [entry.get("laserId") for entry in payload.get("laser") or []]
        if (
            channel is None
            or not lasers
            or any(laser not in self.lasers for laser in lasers)
        ):
            return _error("INVALID_PARAMETERS")
        channel.lasers = lasers
        return dict(OK)

    # the lasers
    def _get_LSM_LASER_LIST_SETTING(self, payload):
        return {
            **OK,
            "laserList": [
                {
                    "imagingDeviceMode": "IMAGING",
                    "laserScannerMode": "MAIN",
                    "phaseId": "PHASE_1",
                    "laserType": "SPE",
                    "laserWaveLength": info["wavelength"],
                    "laserId": laser,
                }
                for laser, info in self.lasers.items()
            ],
        }

    def _get_LSM_LASER_ENABLE_SETTING(self, payload):
        info = self.lasers.get(payload.get("laserId"))
        return (
            {**OK, "enable": info["enabled"]} if info else _error("INVALID_PARAMETERS")
        )

    def _set_LSM_LASER_ENABLE_SETTING(self, payload):
        info = self.lasers.get(payload.get("laserId"))
        if info is None or not isinstance(payload.get("enable"), bool):
            return _error("INVALID_PARAMETERS")
        info["enabled"] = payload["enable"]
        return dict(OK)

    def _get_LSM_LASER_INTENSITY_SETTING(self, payload):
        info = self.lasers.get(payload.get("laserId"))
        return (
            {**OK, "laserIntensity": info["intensity"]}
            if info
            else _error("INVALID_PARAMETERS")
        )

    def _set_LSM_LASER_INTENSITY_SETTING(self, payload):
        info = self.lasers.get(payload.get("laserId"))
        if info is None:
            return _error("INVALID_PARAMETERS")
        asked = float(payload["laserIntensity"])
        low, high = self.laser_range
        # The manual: above the upper limit the upper limit is chosen; below the
        # lower limit, the lower limit or 0.
        if asked > high:
            asked = high
        elif 0 < asked < low:
            asked = low if asked >= low / 2 else 0.0
        info["intensity"] = asked
        return dict(OK)

    # the z-stack, the time lapse, the scanner
    def _get_LSM_Z_COORDINATE_ENABLE_SETTING(self, payload):
        return {**OK, "enable": self.z_stack["enabled"]}

    def _set_LSM_Z_COORDINATE_ENABLE_SETTING(self, payload):
        self.z_stack["enabled"] = bool(payload["enable"])
        return dict(OK)

    def _get_LSM_Z_COORDINATE_SLICE_NUM_SETTING(self, payload):
        return {**OK, "sliceNum": self.z_stack["slices"]}

    def _set_LSM_Z_COORDINATE_SLICE_NUM_SETTING(self, payload):
        if not isinstance(payload.get("sliceNum"), int) or payload["sliceNum"] < 1:
            return _error("INVALID_PARAMETERS")
        self.z_stack["slices"] = payload["sliceNum"]
        return dict(OK)

    def _get_LSM_Z_COORDINATE_STEP_SIZE_SETTING(self, payload):
        return {**OK, "stepSize": self.z_stack["step_nm"]}

    def _set_LSM_Z_COORDINATE_STEP_SIZE_SETTING(self, payload):
        self.z_stack["step_nm"] = int(payload["stepSize"])
        return dict(OK)

    def _get_LSM_Z_COORDINATE_START_SETTING(self, payload):
        return {**OK, "startPosition": self.z_stack["start_nm"]}

    def _set_LSM_Z_COORDINATE_START_SETTING(self, payload):
        self.z_stack["start_nm"] = int(payload["startPosition"])
        return dict(OK)

    def _get_LSM_Z_COORDINATE_END_SETTING(self, payload):
        return {**OK, "endPosition": self.z_stack["end_nm"]}

    def _set_LSM_Z_COORDINATE_END_SETTING(self, payload):
        self.z_stack["end_nm"] = int(payload["endPosition"])
        return dict(OK)

    def _get_LSM_Z_COORDINATE_RANGE_SETTING(self, payload):
        return {**OK, "range": self.z_stack["range_nm"]}

    def _set_LSM_Z_COORDINATE_RANGE_SETTING(self, payload):
        self.z_stack["range_nm"] = int(payload["range"])
        return dict(OK)

    def _get_LSM_Z_COORDINATE_TYPE_SETTING(self, payload):
        return {**OK, "zType": self.z_stack["type"]}

    def _set_LSM_Z_COORDINATE_TYPE_SETTING(self, payload):
        if payload.get("zType") not in ("START_END", "RANGE", "PIEZO"):
            return _error("INVALID_PARAMETERS")
        self.z_stack["type"] = payload["zType"]
        return dict(OK)

    def _get_LSM_TIME_LAPS_ENABLE_SETTING(self, payload):
        return {**OK, "enable": self.time_lapse["enabled"]}

    def _set_LSM_TIME_LAPS_ENABLE_SETTING(self, payload):
        self.time_lapse["enabled"] = bool(payload["enable"])
        return dict(OK)

    def _get_LSM_TIME_LAPS_REPEAT_TIMES_SETTING(self, payload):
        return {**OK, "repeatCount": self.time_lapse["repeat"]}

    def _set_LSM_TIME_LAPS_REPEAT_TIMES_SETTING(self, payload):
        if (
            not isinstance(payload.get("repeatCount"), int)
            or payload["repeatCount"] < 1
        ):
            return _error("INVALID_PARAMETERS")
        self.time_lapse["repeat"] = payload["repeatCount"]
        return dict(OK)

    def _get_LSM_TIME_LAPS_INTERVAL_SETTING(self, payload):
        return {
            **OK,
            "interval": self.time_lapse["interval"],
            "unitOfInterval": self.time_lapse["unit"],
        }

    def _set_LSM_TIME_LAPS_INTERVAL_SETTING(self, payload):
        if not all(
            isinstance(payload.get(k), int) for k in ("interval", "unitOfInterval")
        ):
            return _error("INVALID_PARAMETERS")
        self.time_lapse["interval"], self.time_lapse["unit"] = (
            payload["interval"],
            payload["unitOfInterval"],
        )
        return dict(OK)

    def _get_LSM_SCANNER_TYPE_SETTING(self, payload):
        return {**OK, "scannerType": self.scanner_type}

    def _set_LSM_SCANNER_TYPE_SETTING(self, payload):
        if payload.get("scannerType") not in ("GALVANO", "RESONANT"):
            return _error("INVALID_PARAMETERS")
        self.scanner_type = payload["scannerType"]
        return dict(OK)

    def _get_LSM_SCANNER_MODE_SETTING(self, payload):
        return {**OK, "scannerMode": self.scanner_mode}

    def _set_LSM_SCANNER_MODE_SETTING(self, payload):
        if payload.get("scannerMode") not in ("ONEWAY", "ROUNDTRIP"):
            return _error("INVALID_PARAMETERS")
        self.scanner_mode = payload["scannerMode"]
        return dict(OK)

    # brightness adjustment and MATL
    def _set_LSM_BRIGHTNESS_ADJUSTMENT_CREATE_OR_UPDATE_SETTING(self, payload):
        self.brightness_adjustment["points"] += 1
        return dict(OK)

    def _get_LSM_BRIGHTNESS_ADJUSTMENT_ENABLE_SETTING(self, payload):
        return {**OK, "enable": self.brightness_adjustment["enabled"]}

    def _set_LSM_BRIGHTNESS_ADJUSTMENT_ENABLE_SETTING(self, payload):
        self.brightness_adjustment["enabled"] = bool(payload["enable"])
        return dict(OK)

    def _set_LSM_BRIGHTNESS_ADJUSTMENT_DELETE_ALL_SETTING(self, payload):
        self.brightness_adjustment["points"] = 0
        return dict(OK)

    def _set_MATL_ROI_CREATE(self, payload):
        if (
            payload.get("acquisitionType") != "LSM_IMAGING"
            or payload.get("roiType") != "DEFINE_MATRIX"
        ):
            return _error("INVALID_PARAMETERS")
        group_id = f"group-{len(self.matl_groups) + 1}"
        self.matl_groups[group_id] = {
            "well": "A01",
            "enabled": True,
            "x": self.x_nm(),
            "y": self.y_nm(),
            "z": self.z_nm(),
            "z_origin": self.z_origin_nm,
            "columns": int(payload["column"]),
            "rows": int(payload["row"]),
            "areas": {},
        }
        return {**OK, "groupId": group_id}

    def _set_MATL_ROI_WELL_DUPLICATE(self, payload):
        return dict(OK)

    def _get_MATL_ROI_GET_REGISTERD_LIST(self, payload):
        return {
            **OK,
            "groupList": [
                {"groupId": gid, "wellId": group["well"]}
                for gid, group in self.matl_groups.items()
            ],
        }

    def _matl(self, payload):
        return self.matl_groups.get(payload.get("groupId"))

    def _set_MATL_ROI_DELETE(self, payload):
        if self._matl(payload) is None:
            return _error("INVALID_PARAMETERS")
        del self.matl_groups[payload["groupId"]]
        return dict(OK)

    def _get_MATL_ROI_ENABLE_SETTING(self, payload):
        group = self._matl(payload)
        return (
            {**OK, "enable": group["enabled"]}
            if group
            else _error("INVALID_PARAMETERS")
        )

    def _set_MATL_ROI_ENABLE_SETTING(self, payload):
        group = self._matl(payload)
        if group is None:
            return _error("INVALID_PARAMETERS")
        group["enabled"] = bool(payload["enable"])
        return dict(OK)

    def _get_MATL_ROI_XY_POSITION_SETTING(self, payload):
        group = self._matl(payload)
        return (
            {**OK, "x": group["x"], "y": group["y"]}
            if group
            else _error("INVALID_PARAMETERS")
        )

    def _set_MATL_ROI_XY_POSITION_SETTING(self, payload):
        group = self._matl(payload)
        if group is None:
            return _error("INVALID_PARAMETERS")
        group["x"], group["y"] = int(payload["x"]), int(payload["y"])
        return dict(OK)

    def _set_MATL_ROI_Z_POSITION_SETTING(self, payload):
        group = self._matl(payload)
        if group is None:
            return _error("INVALID_PARAMETERS")
        group["z"] = int(payload["z"])
        return dict(OK)

    def _get_MATL_ROI_Z_ORIGIN_SETTING(self, payload):
        group = self._matl(payload)
        return (
            {**OK, "zOrigin": group["z_origin"]}
            if group
            else _error("INVALID_PARAMETERS")
        )

    def _set_MATL_ROI_Z_ORIGIN_SETTING(self, payload):
        group = self._matl(payload)
        if group is None:
            return _error("INVALID_PARAMETERS")
        group["z_origin"] = int(payload["zOrigin"])
        return dict(OK)

    def _set_MATL_ROI_AREA_DELETE_SETTING(self, payload):
        return dict(OK) if self._matl(payload) else _error("INVALID_PARAMETERS")

    def _set_MATL_ROI_AREA_ENABLE_SETTING(self, payload):
        return dict(OK) if self._matl(payload) else _error("INVALID_PARAMETERS")

    def _set_MATL_ROI_AREA_Z_RANGE_SETTING(self, payload):
        return dict(OK) if self._matl(payload) else _error("INVALID_PARAMETERS")

    # --- the pretend picture ---------------------------------------------------------------

    def pixel_um(self) -> float:
        return self.pixel_um_by_slot[self.objective_slot]

    def view(self, channel_index: int, z_index: int = 0, size_z: int = 1) -> np.ndarray:
        """The stage-aligned view of the sample under the stage now: rows grow with +Y, columns with +X.

        The sample rides on the stage, so when the stage moves +X the sample
        moves +X in the picture too; the window into the field moves the other way.
        """
        pixel_nm = self.pixel_um() * 1000
        col = (
            -round((self.x_nm() - 10_000_000) / pixel_nm)
            + FIELD_SIZE // 2
            - IMAGE_SIZE // 2
            + channel_index * 7
        )
        row = (
            -round((self.y_nm() - 8_000_000) / pixel_nm)
            + FIELD_SIZE // 2
            - IMAGE_SIZE // 2
        )
        rows = np.arange(row, row + IMAGE_SIZE) % FIELD_SIZE
        cols = np.arange(col, col + IMAGE_SIZE) % FIELD_SIZE
        plane = self.field[np.ix_(rows, cols)].astype(np.float64)
        # Slices away from the middle of a stack are dimmer, so a max projection differs from any one slice.
        focus = 1.0 - 0.6 * abs(z_index - (size_z - 1) / 2) / max(size_z, 1)
        return np.clip(plane * focus, 0, (1 << BIT_COUNT) - 1).astype(np.uint16)

    def scanned(self, view: np.ndarray) -> np.ndarray:
        """*view* as the scanner delivers it: turned and mirrored the way the scanner is mounted."""
        degrees, mirrored = self.orientation
        picture = np.rot90(view, k=degrees // 90)
        return np.ascontiguousarray(np.fliplr(picture) if mirrored else picture)

    def _take_picture(self) -> PretendFile:
        channels = [name for name, channel in self.channels.items() if channel.enabled]
        size_z = self.z_stack["slices"] if self.z_stack["enabled"] else 1
        size_t = self.time_lapse["repeat"] if self.time_lapse["enabled"] else 1
        z0_nm = self.z_origin_nm + self.z_stack["start_nm"]
        z_positions = (
            [(z0_nm + i * self.z_stack["step_nm"]) / 1000 for i in range(size_z)]
            if size_z > 1
            else [self.z_nm() / 1000]
        )
        t_step = (
            self.time_lapse["interval"] * 10.0 ** (-self.time_lapse["unit"])
            if size_t > 1
            else None
        )
        planes = {}
        for t in range(size_t):
            for ci, name in enumerate(channels):
                for z in range(size_z):
                    planes[(t, name, z, 0)] = self.scanned(self.view(ci, z, size_z))
        path = f"D:/Images/image_{next(self.image_counter):04d}.oir"
        return PretendFile(
            path,
            channels,
            IMAGE_SIZE,
            IMAGE_SIZE,
            size_t,
            size_z,
            z_positions,
            t_step,
            self.pixel_um(),
            planes,
            finished_at=float("inf"),
        )

    # --- IDA ---------------------------------------------------------------------------------

    def _new_handle(self, kind: str, *payload) -> str:
        handle = f"{kind}-{next(self.handle_counter)}"
        self.handles[handle] = (kind, *payload)
        return handle

    def _handle(self, kind: str, value) -> tuple | None:
        found = self.handles.get(value)
        return found if found and found[0] == kind else None

    def _IDA_open(self, payload):
        pretend = self.files.get(payload.get("filename"))
        if pretend is None or pretend.finished_at > time.monotonic():
            return _error("FILE_NOT_FOUND")
        return {**OK, "fileId": self._new_handle("file", pretend)}

    def _IDA_close(self, payload):
        if self._handle("file", payload.get("fileId")) is None:
            return _error("INVALID_PARAMETERS")
        del self.handles[payload["fileId"]]
        return dict(OK)

    def _IDA_getNumOfGroups(self, payload):
        return (
            {**OK, "numOfGroups": 1}
            if self._handle("file", payload.get("fileId"))
            else _error("INVALID_PARAMETERS")
        )

    def _IDA_getGroup(self, payload):
        found = self._handle("file", payload.get("fileId"))
        if found is None or payload.get("group") != 0:
            return _error("INVALID_PARAMETERS")
        return {**OK, "groupId": self._new_handle("group", found[1])}

    def _IDA_releaseGroup(self, payload):
        if self._handle("group", payload.get("groupId")) is None:
            return _error("INVALID_PARAMETERS")
        del self.handles[payload["groupId"]]
        return dict(OK)

    def _IDA_getNumOfLevel(self, payload):
        return (
            {**OK, "numOfLayers": 1}
            if self._handle("group", payload.get("groupId"))
            else _error("INVALID_PARAMETERS")
        )

    def _IDA_getLevelImageSize(self, payload):
        found = self._handle("group", payload.get("groupId"))
        return (
            {**OK, "width": found[1].width, "height": found[1].height}
            if found
            else _error("INVALID_PARAMETERS")
        )

    def _IDA_getNumOfArea(self, payload):
        return (
            {**OK, "numOfArea": 1}
            if self._handle("group", payload.get("groupId"))
            else _error("INVALID_PARAMETERS")
        )

    def _IDA_getArea(self, payload):
        found = self._handle("group", payload.get("groupId"))
        if found is None or payload.get("level") != 0 or payload.get("area") != 0:
            return _error("INVALID_PARAMETERS")
        return {**OK, "areaId": self._new_handle("area", found[1])}

    def _IDA_releaseArea(self, payload):
        if self._handle("area", payload.get("areaId")) is None:
            return _error("INVALID_PARAMETERS")
        del self.handles[payload["areaId"]]
        return dict(OK)

    def _IDA_getProperty(self, payload):
        found = self._handle("area", payload.get("areaId"))
        if found is None:
            return _error("INVALID_PARAMETERS")
        pretend: PretendFile = found[1]
        key = payload.get("key")
        additional = payload.get("additional") or {}
        axes = ([("TIMELAPSE", pretend.size_t)] if pretend.size_t > 1 else []) + (
            [("ZSTACK", pretend.size_z)] if pretend.size_z > 1 else []
        )
        if key == "EnabledChannelIdList":
            return {**OK, "value": [list(pretend.channels)]}
        if key == "ChannelInfo":
            channel = additional.get("channelId")
            if channel not in pretend.channels:
                return _error("INVALID_PARAMETERS")
            # The manual: ChannelInfo answers under "structValue", not "value".
            return {
                **OK,
                "structValue": [
                    {
                        "deviceName": f"PMT {channel}",
                        "name": channel,
                        "bitCount": BIT_COUNT,
                        "colorSpace": "GRAYSCALE",
                        "depth": 2,
                        "order": pretend.channels.index(channel),
                    }
                ],
            }
        if key == "Axes":
            return {**OK, "value": [[name for name, _ in axes]]}
        if key == "PixelLength":
            return {**OK, "value": [[pretend.pixel_um, pretend.pixel_um]]}
        if key in ("AxisStartPosition", "AxisEndPosition", "AxisStep", "AxisMaxSize"):
            axis = additional.get("axisName")
            sizes = dict(axes)
            if axis not in sizes:
                return _error("INVALID_PARAMETERS")
            if axis == "ZSTACK":
                values = {
                    "AxisStartPosition": pretend.z_positions_um[0],
                    "AxisEndPosition": pretend.z_positions_um[-1],
                    # The manual does not say the step carries a sign (it tells the direction of a
                    # stack from start and end), so the pretend FLUOVIEW gives only its size.
                    "AxisStep": abs(
                        pretend.z_positions_um[1] - pretend.z_positions_um[0]
                    ),
                    "AxisMaxSize": pretend.size_z,
                }
            else:
                values = {
                    "AxisStartPosition": 0.0,
                    "AxisEndPosition": pretend.t_step_s * (pretend.size_t - 1),
                    "AxisStep": pretend.t_step_s,
                    "AxisMaxSize": pretend.size_t,
                }
            return {**OK, "value": [[values[key]]]}
        return _error("UNSUPPORTED_PARAMETERS")

    def _IDA_getImage(self, payload):
        found = self._handle("area", payload.get("areaId"))
        if found is None:
            return _error("INVALID_PARAMETERS")
        pretend: PretendFile = found[1]
        channel = payload.get("channelId")
        indices = {
            entry.get("axisName"): int(entry.get("index", 0))
            for entry in payload.get("axisInfo") or []
        }
        t, z, lam = (
            indices.get("TIMELAPSE", 0),
            indices.get("ZSTACK", 0),
            indices.get("LAMBDA", 0),
        )
        if (t, channel, z, lam) not in pretend.planes:
            return _error("NO_FRAME")
        return {
            **OK,
            "imageId": [
                self._new_handle(
                    "image", pretend.planes[(t, channel, z, lam)], (t, z, lam)
                )
            ],
        }

    def _IDA_releaseImage(self, payload):
        if self._handle("image", payload.get("imageId")) is None:
            return _error("INVALID_PARAMETERS")
        del self.handles[payload["imageId"]]
        self.next_bodies.pop(payload["imageId"], None)
        return dict(OK)

    def _IDA_getImageSize(self, payload):
        found = self._handle("image", payload.get("imageId"))
        if found is None:
            return _error("INVALID_PARAMETERS")
        return {**OK, "width": int(found[1].shape[1]), "height": int(found[1].shape[0])}

    def _IDA_getImageAxis(self, payload):
        found = self._handle("image", payload.get("imageId"))
        if found is None:
            return _error("INVALID_PARAMETERS")
        t, z, lam = found[2]
        return {
            **OK,
            "axisInfo": [
                {"axisName": "TIMELAPSE", "index": t},
                {"axisName": "ZSTACK", "index": z},
                {"axisName": "LAMBDA", "index": lam},
            ],
        }

    def _IDA_getImageBody(self, payload):
        found = self._handle("image", payload.get("imageId"))
        if found is None:
            return _error("INVALID_PARAMETERS")
        if payload.get("compressType") != "NONE":
            return _error("UNSUPPORTED_PARAMETERS")
        plane = found[1]
        rect = payload.get("rect") or {}
        if rect.get("width") != plane.shape[1] or rect.get("height") != plane.shape[0]:
            return _error("INVALID_PARAMETERS")
        raw = plane.astype("<u2").tobytes()
        first, rest = raw[: self.body_piece], raw[self.body_piece :]
        if rest:
            self.next_bodies[payload["imageId"]] = rest
        return {**OK, "continue": bool(rest), "data": first}

    def _IDA_getNextImageBody(self, payload):
        rest = self.next_bodies.get(payload.get("imageId"))
        if rest is None:
            return _error("INVALID_PARAMETERS")
        piece, remaining = rest[: self.body_piece], rest[self.body_piece :]
        if remaining:
            self.next_bodies[payload["imageId"]] = remaining
        else:
            del self.next_bodies[payload["imageId"]]
        return {**OK, "continue": bool(remaining), "data": piece}

    def _IDA_getLUT(self, payload):
        ramp = bytes(range(256))
        return {**OK, "rData": ramp, "gData": ramp, "bData": ramp}

    # --- Processing (stitching): accepted, finished at once -----------------------------------

    def _Processing_startProcessing(self, payload):
        return {**OK, "stitchFilePath": "D:/Images/stitched.oir"}

    def _Processing_stopProcessing(self, payload):
        return dict(OK)

    def _Processing_getProcessingProgress(self, payload):
        return {
            **OK,
            "progress": [{"total": 100, "complete": 100}],
            "stitchFilePaths": ["D:/Images/stitched.oir"],
        }


class MockRdkClient:
    """The real client's ``call`` against a :class:`MockFluoview`, in-process and without a network."""

    def __init__(self, mock: MockFluoview | None = None):
        self.mock = mock if mock is not None else MockFluoview()
        self.last_reply: dict | None = None
        self.calls_made = 0
        self.min_gap_s = 0.0

    @property
    def address(self):
        from zmart_drivers.evident.fv4000.rdk.primitives.rdk.client import RdkAddress

        return RdkAddress("mock", 0)

    def call(self, method: str, payload: dict | None = None) -> dict:
        self.calls_made += 1
        reply = self.mock.call(method, payload)
        self.last_reply = reply
        return reply


class _Namespace:
    def __init__(self, mock: MockFluoview, prefix: str):
        self._mock, self._prefix = mock, prefix

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def method(payload=None):
            return self._mock.call(f"{self._prefix}.{name}", payload)

        return method


class _Tree:
    def __init__(self, mock: MockFluoview):
        for prefix in (
            "Maintenance",
            "Remote",
            "Repeat",
            "Protocol",
            "Canvas",
            "Parameter",
            "Processing",
            "IDA",
        ):
            setattr(self, prefix, _Namespace(mock, prefix))


class _AtFluoviewsPath(SimpleXMLRPCRequestHandler):
    """Answer at ``/xmlrpc``, the path FLUOVIEW's RDK listens on (``http://<host>:8080/xmlrpc``)."""

    rpc_paths = ("/xmlrpc", "/RPC2")


def serve(
    mock: MockFluoview, port: int = 0
) -> tuple[SimpleXMLRPCServer, threading.Thread]:
    """Serve *mock* as a real XML-RPC server on this computer; answers the server and its thread. ``server.shutdown()`` stops it."""
    server = SimpleXMLRPCServer(
        ("127.0.0.1", port),
        requestHandler=_AtFluoviewsPath,
        logRequests=False,
        allow_none=False,
    )
    server.register_instance(_Tree(mock), allow_dotted_names=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread
