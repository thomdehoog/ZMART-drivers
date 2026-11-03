"""LAS X's data container: ROI regions and bleach points as it holds them and as the driver gives them, and the
requests that read, add to and clear its sets (run by :mod:`.data_container_worker` in a process of its own).

LAS X keeps the regions of an ROI scan and the bleach points in its data
container, each set an element whose children are ``ROISingle`` elements
(the same XML as a ``.roi`` file or a setting's ``ROI`` block). A region's
points are in metres around its centre, in the picture's frame (x to the
right, y down), turned by its ``Transformation``'s rotation and moved by its
``Translation`` from the field's middle (simulator, 2026-11-03: a rectangle
drawn at pixels 100 to 250 by 100 to 220 had its centre at -184.07,
-220.59 um, within a pixel of the pixels' 2.27 um each).

The driver gives positions in micrometres from the field's centre, x to the
right and y up, as ``set_pan`` and the stage do; this module turns them into
that XML and back. It writes every shape unturned (rotation 0, as LAS X writes
its polygons and ranges) and reads any rotation LAS X wrote (270 for its own
rectangles and ellipses).

Shapes, by LAS X's ``RoiType``: 1 a bleach point, 8 a polygon, 16 a
rectangle, 32 an ellipse (100 points on its outline, as LAS X draws it), 64 a
range (a band of columns across the whole picture: two points, its left and
right edges). A bleach point carries its bleaching time in its
``BLEACH_POINT`` attachment (``Duration``, milliseconds), with the z it is
bleached at. LAS X adds each region's laser lines (``AOTF_SETTING``) itself.

Found by observation (simulator, 2026-11-03): the wrapper's public listing (its methods and their
parameters, version 2.65.0.0), the sets' ids in LCS's state tree, and the regions LAS X itself wrote for
shapes drawn in its window.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import quoteattr

#: LAS X's region types, by the driver's names.
SHAPES = {"point": 1, "polygon": 8, "rectangle": 16, "ellipse": 32, "range": 64}
NAMES = {code: name for name, code in SHAPES.items()}

#: Points on an ellipse's outline, as LAS X draws one.
ELLIPSE_POINTS = 100

#: The look LAS X gives a region it draws: red, labelled, two pixels wide.
LOOK = ('RoiAction="65535" TransformationType="65535" Color="4294901760" FontName="Arial" FontSize="10000" '
        'FontStyle="0" Inverted="0" VisibleLabel="1" Visible="1" LineWidth="2" AnnotationText=""')


def number(value, what: str) -> float:
    """*value* as a finite float; ValueError naming *what* otherwise."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{what} is a number of micrometres, not {value!r}")
    return float(value)


def pair(value, what: str) -> tuple[float, float]:
    """*value* as an (x, y) pair of finite floats; ValueError naming *what* otherwise."""
    try:
        x, y = value
    except (TypeError, ValueError) as why:
        raise ValueError(f"{what} is a pair (x, y) in micrometres, not {value!r}") from why
    return number(x, what), number(y, what)


def outline(region: dict) -> tuple[int, tuple[float, float], list[tuple[float, float]]]:
    """A region given by the driver as LAS X's type, its centre and its points around the centre, in micrometres in
    the picture's frame (y down). ValueError for a region the driver does not take."""
    shape = region.get("shape")
    if shape == "rectangle" or shape == "ellipse":
        cx, cy = pair(region.get("centre_um"), f"a {shape}'s centre_um")
        w, h = pair(region.get("size_um"), f"a {shape}'s size_um")
        if w <= 0 or h <= 0:
            raise ValueError(f"a {shape}'s size_um is positive both ways, not {region.get('size_um')!r}")
        if shape == "rectangle":
            points = [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]
        else:
            points = [(w / 2 * math.cos(2 * math.pi * k / ELLIPSE_POINTS), h / 2 * math.sin(2 * math.pi * k / ELLIPSE_POINTS))
                      for k in range(ELLIPSE_POINTS)]
        return SHAPES[shape], (cx, -cy), points
    if shape == "polygon":
        corners = [pair(p, "a polygon's vertex") for p in region.get("vertices_um") or []]
        if len(corners) < 3:
            raise ValueError(f"a polygon has three vertices or more, not {len(corners)}")
        down = [(x, -y) for x, y in corners]
        cx = (min(x for x, _ in down) + max(x for x, _ in down)) / 2
        cy = (min(y for _, y in down) + max(y for _, y in down)) / 2
        return SHAPES[shape], (cx, cy), [(x - cx, y - cy) for x, y in down]
    if shape == "range":
        left, right = sorted((number(region.get("from_um"), "a range's from_um"), number(region.get("to_um"), "a range's to_um")))
        if right <= left:
            raise ValueError("a range's from_um and to_um are two different places")
        half = (right - left) / 2
        return SHAPES[shape], (left + half, 0.0), [(-half, 0.0), (half, 0.0)]
    raise ValueError(f"a region's shape is 'rectangle', 'ellipse', 'polygon' or 'range', not {shape!r}")


def extent(region: dict) -> tuple[float, float, float, float]:
    """The region's left, right, top and bottom, in micrometres from the field's centre, x right and y up."""
    _, (cx, cy), points = outline(region)
    xs = [cx + x for x, _ in points]
    ys = [-(cy + y) for _, y in points]
    return min(xs), max(xs), max(ys), min(ys)


def element(name: str, data: str) -> str:
    """One data container element holding *data*."""
    return (f'<Element Name={quoteattr(name)} Visibility="2" CopyOption="1" UniqueID="{uuid.uuid1()}"><Data>{data}</Data>'
            '<Memory Size="0" MemoryBlockID="MemBlock_0"/><Children/></Element>')


def single(code: int, centre: tuple[float, float], points, closed: bool, attachment: str = "") -> str:
    """One ``ROISingle``: *points* around *centre*, both in micrometres in the picture's frame, written in metres."""
    listed = "".join(f'<P X="{x * 1e-6!r}" Y="{y * 1e-6!r}"/>' for x, y in points)
    return (f'<ROISingle RoiType="{code}" {LOOK} IsClosed="{int(closed)}"><Vertices>{listed}</Vertices>'
            f'<Transformation Rotation="0"><Scaling XScale="1" YScale="1"/>'
            f'<Translation X="{centre[0] * 1e-6!r}" Y="{centre[1] * 1e-6!r}"/></Transformation>{attachment}</ROISingle>')


def set_xml(children: list[str]) -> str:
    """A set of *children* as LAS X loads one (its ``LoadElementFromXML``)."""
    return ('<LMSDataContainerHeader Version="2"><Element Name="Save" Visibility="2" CopyOption="1" '
            f'UniqueID="{uuid.uuid1()}"><Data><ROISet ROISetType="1" PossibleChildROITypes="4294967295" '
            'PossibleROITransforms="65535" PossibleROIActions="65535"/></Data><Memory Size="0" MemoryBlockID="MemBlock_0"/>'
            f'<Children>{"".join(children)}</Children></Element></LMSDataContainerHeader>')


def regions_xml(regions: list[dict], first: int = 1) -> str:
    """The driver's *regions* as a set LAS X takes, named ``ROI <first>``, ``ROI <first + 1>``, ...; ValueError when one
    is not a region the driver takes."""
    children = []
    for k, region in enumerate(regions):
        code, centre, points = outline(region)
        children.append(element(f"ROI {first + k}", single(code, centre, points, closed=code != SHAPES["range"])))
    return set_xml(children)


def bleach_points_xml(points: list[dict], z_m: float, first: int = 1) -> str:
    """The driver's bleach *points* (``at_um`` and ``seconds``) as a set LAS X takes, each bleached at *z_m* (metres, the
    z drive's position, as LAS X writes it); ValueError when one is not a point the driver takes."""
    children = []
    for k, point in enumerate(points):
        x, y = pair(point.get("at_um"), "a bleach point's at_um")
        seconds = point.get("seconds")
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 0 < seconds <= 86400:
            raise ValueError(f"a bleach point's seconds are above 0 and at most a day, not {seconds!r}")
        attachment = (f'<Attachment Name="BLEACH_POINT" Application="LAS AF" Version="1.0" Duration="{round(seconds * 1000)}" '
                      f'ZPosition="{float(z_m)!r}" LastUnitIndex="0" IsXY="1"/>')
        children.append(element(f"ROI {first + k}", single(SHAPES["point"], (x, -y), [(0.0, 0.0)], closed=False, attachment=attachment)))
    return set_xml(children)


def turned(x: float, y: float, degrees: float) -> tuple[float, float]:
    """A point around a region's centre, turned as LAS X turns it (picture frame, y down: 270 takes (x, y) to (y, -x))."""
    a = math.radians(degrees)
    return x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a)


def read_set(xml: str) -> list[dict]:
    """The regions or bleach points of a set's XML, as the driver gives them: ``name``, ``shape``, ``points_um`` (x right,
    y up, from the field's centre), ``centre_um``; a bleach point also ``seconds``, a region its ``laser_lines`` (each
    line's wavelength and whether it is on in the region, from ``AOTF_SETTING``)."""
    found = []
    for item in ET.fromstring(xml).iter("Element"):
        roi = item.find("Data/ROISingle")
        if roi is None:
            continue
        move = roi.find("Transformation")
        degrees = float(move.get("Rotation", "0")) if move is not None else 0.0
        at = move.find("Translation") if move is not None else None
        cx, cy = (float(at.get("X")) * 1e6, float(at.get("Y")) * 1e6) if at is not None else (0.0, 0.0)
        points = []
        for p in roi.find("Vertices") if roi.find("Vertices") is not None else []:
            x, y = turned(float(p.get("X")) * 1e6, float(p.get("Y")) * 1e6, degrees)
            points.append((cx + x, -(cy + y)))
        entry = {"name": item.get("Name"), "shape": NAMES.get(int(roi.get("RoiType")), f"type {roi.get('RoiType')}"),
                 "points_um": points, "centre_um": (cx, -cy)}
        for attachment in roi.findall("Attachment"):
            if attachment.get("Name") == "BLEACH_POINT":
                entry["seconds"] = int(attachment.get("Duration", "0")) / 1000
            if attachment.get("Name") == "AOTF_SETTING":
                entry["laser_lines"] = sorted({(float(line.get("LaserLine")), line.get("Intensity") != "0") for line in attachment})
        found.append(entry)
    return found


#: Where the worker finds pythonnet (kept apart from the driver's environment) and LAS X's BIN; ZMART_PYTHONNET and
#: ZMART_LASX_BIN move them.
PYTHONNET_DIR = os.environ.get("ZMART_PYTHONNET", r"C:\ProgramData\MinicondaZMB\tools\pynet")
LASX_BIN = os.environ.get("ZMART_LASX_BIN", r"C:\Program Files\Leica Microsystems CMS GmbH\LAS X\BIN")
WORKER = Path(__file__).with_name("data_container_worker.py")

#: How long one request may take, in seconds (connecting, loading the regions, moving them: about 2 s on the simulator).
TIMEOUT_S = 60.0


def request(do: str, set_id: int, xml: str | None = None, *, timeout: float = TIMEOUT_S) -> dict:
    """Ask LAS X's data container to ``"read"``, ``"add"`` (the set *xml*'s children) or ``"clear"`` the set *set_id*.

    Answers ``{"success", "xml" (the set afterwards), "message"}``; never raises for what LAS X or the worker does.
    """
    asked = {"do": do, "set": int(set_id), "xml": xml, "pythonnet": PYTHONNET_DIR, "bin": LASX_BIN,
             "user": os.environ.get("USERNAME", "zmart")}
    with tempfile.TemporaryDirectory(prefix="zmart_dc_") as folder:
        path = Path(folder) / "request.json"
        path.write_text(json.dumps(asked), encoding="utf-8")
        try:
            done = subprocess.run([sys.executable, "-I", str(WORKER), str(path)], capture_output=True, text=True, check=False,
                                  encoding="utf-8", timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"success": False, "message": f"LAS X's data container did not answer within {timeout:g} s"}
    try:
        answer = json.loads(done.stdout)
    except ValueError:
        return {"success": False, "message": f"the data container's worker answered nothing readable: {(done.stdout or done.stderr)[-300:]}"}
    return answer if isinstance(answer, dict) else {"success": False, "message": f"the worker answered {answer!r}"}
