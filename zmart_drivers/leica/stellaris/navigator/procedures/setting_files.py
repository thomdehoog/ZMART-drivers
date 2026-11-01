"""Changing what has no command of its own, through a saved setting: save it, change Setting 1, load it.

LAS X's window changes the detectors without a command LCS logs, but its
Setting panel saves and loads a whole setting as a file with two commands it
does log (``ATL_Block_Save_To_XML``, ``ATL_Block_Load_From_XML``; simulator,
2026-11-01). The file is the same XML as a block in a Navigator Expert
scanning template: the setting's master, ``Setting 1`` and the ``Best Focus
Setting``, each with its lasers, detectors and bands. Values changed in
Setting 1 of a saved file and loaded again were applied: a detector on, its
gain (not in photon counting, where LAS X fixes it), its band (the
``MultiBand`` of its channel), a laser line's power.

So a change here goes: save the current setting, change only Setting 1 in a
copy, check every laser power and detector gain in it against the limits,
load it, read the change back, and compare what LCS holds before and after,
because LAS X rearranges dependent values on a load (with a detector switched
on in the Best Focus setting, its format went from 64 x 64 to 8192 x 8192).
With several sequential settings the change is refused: which one is meant
cannot be told.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from ..dispatcher import dispatch
from ..primitives.direct import writes
from . import readings as readers
from .changes import run

SETTING_1 = "Setting 1"
#: How close a detector's gain must come when read back.
GAIN_TOLERANCE = 0.05


def folder_for(handle) -> Path:
    """Where a session keeps the setting files it saves and changes: beside its pictures, else in the configuration folder."""
    from ..driver_setup.store import MACHINE

    root = handle.output_root()
    base = Path(root) / ".settings" if root else MACHINE.root / "settings"
    return base / handle.hash6


def save(client, path) -> dict:
    """Save the current setting to *path*, as LAS X's Setting panel does; ``success`` when the file is there."""
    path = Path(path)
    result = run(client, writes.save_setting(path))
    if result.get("success") and not path.is_file():
        result.update(success=False, message=f"LAS X did not write {path}")
    return result


def load(client, path) -> dict:
    """Load the setting in *path* after checking its values against the limits; the result lists what changed.

    Every laser power and detector gain in the file is shown to the limits
    first; one outside them refuses the file, and nothing is loaded. After
    the load, ``changed`` lists every reading that is no longer what it was.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as why:
        return dispatch.refused(f"the setting file cannot be read: {why}")
    before = snapshot(client)
    result = run(client, writes.load_setting(path, values_in(text)))
    if result.get("success"):
        time.sleep(0.5)
        result["changed"] = changes_between(before, snapshot(client))
    return result


def set_detector(client, folder, name, *, on=None, gain=None, band=None) -> dict:
    """Switch the detector *name* on or off, set its gain, or its band ``(begin_nm, end_nm)``, through Setting 1; read back.

    *folder* is where the saved and the changed setting files are kept.
    ``confirmed`` is True when the detector reads back as asked.
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    saved, changed = folder / f"setting_{stamp}.seq", folder / f"setting_{stamp}_changed.seq"
    result = save(client, saved)
    if not result.get("success"):
        return result
    try:
        text = saved.read_text(encoding="utf-8")
        start, end = setting_1(text)
        part = with_detector(text[start:end], name, on=on, gain=gain, band=band)
    except (OSError, ValueError) as why:
        return dispatch.refused(f"the detector cannot be set through the setting: {why}")
    changed.write_text(text[:start] + part + text[end:], encoding="utf-8")
    result = load(client, changed)
    if result.get("success"):
        result["confirmed"] = detector_shows(readers.get_detectors(client), name, on=on, gain=gain, band=band)
    return result


# =============================================================================
# The file: Setting 1, and the values in it
# =============================================================================


def setting_1(text: str) -> tuple[int, int]:
    """Where Setting 1 is in the file: from its definition to the next definition or the autofocus block.

    ValueError when the file has no Setting 1, or several sequential settings.
    """
    listed = text[text.find("<LDM_Block_Sequential_List"):]
    in_list = listed.split("<Autofocus_Block")[0]
    if in_list.count("<ATLConfocalSettingDefinition") > 1:
        raise ValueError("LAS X has several sequential settings; which one is meant cannot be told")
    found = text.find(f'UserSettingName="{SETTING_1}"')
    if found < 0:
        raise ValueError(f"the saved setting has no {SETTING_1}")
    start = text.rindex("<ATLConfocalSettingDefinition", 0, found)
    ends = [i for i in (text.find("<ATLConfocalSettingDefinition", start + 1), text.find("<Autofocus_Block", start)) if i > 0]
    return start, min(ends) if ends else len(text)


def with_detector(part: str, name: str, *, on=None, gain=None, band=None) -> str:
    """Setting 1's text with the detector *name* changed; ValueError when it has no such detector."""
    found = re.search(rf'<Detector [^>]*Name="{re.escape(name)}"[^>]*>', part)
    if found is None:
        raise ValueError(f"the setting has no detector {name!r}")
    tag = found[0]
    new = tag
    if on is not None:
        new = re.sub(r' IsActive="[^"]*"', f' IsActive="{int(bool(on))}"', new)
    if gain is not None:
        new = re.sub(r' Gain="[^"]*"', f' Gain="{float(gain):g}"', new)
    part = part.replace(tag, new, 1)
    if band is not None:
        begin, end = (float(v) for v in band)
        channel = re.search(r' Channel="([^"]*)"', tag)[1]

        def moved(m):
            text = re.sub(r' (LeftWorld|TargetWaveLengthBegin)="[^"]*"', rf' \g<1>="{begin:g}"', m[0])
            return re.sub(r' (RightWorld|TargetWaveLengthEnd)="[^"]*"', rf' \g<1>="{end:g}"', text)

        part, n = re.subn(rf'<MultiBand [^>]*Channel="{channel}"[^>]*>', moved, part, count=1)
        if n == 0:
            raise ValueError(f"the setting has no band for {name!r}")
    return part


def values_in(text: str) -> dict:
    """What the limits are shown of a setting file: every laser power (percent) and detector gain in Setting 1."""
    try:
        start, end = setting_1(text)
    except ValueError:
        start, end = 0, len(text)
    part = text[start:end]
    lasers = [float(v) for v in re.findall(r'<LaserLineSetting [^>]* IntensityDev="([^"]*)"', part) if float(v) >= 0]
    gains = [float(v) for v in re.findall(r'<Detector [^>]*IsActive="1"[^>]* Gain="([^"]*)"', part)]
    return {"laser_power": lasers, "detector_gain": gains}


# =============================================================================
# Reading back, and what else changed
# =============================================================================


def detector_shows(detectors, name, *, on=None, gain=None, band=None) -> bool:
    """Whether the detectors in use show *name* as asked."""
    found = {d["name"]: d for d in detectors or []}
    if on is not None and (name in found) != bool(on):
        return False
    if name not in found:
        return on is False
    detector = found[name]
    if gain is not None and abs(detector["gain"]["value"] - float(gain)) > GAIN_TOLERANCE:
        return False
    if band is not None and (detector["detectionBegin"], detector["detectionEnd"]) != tuple(float(v) for v in band):
        return False
    return True


def snapshot(client) -> dict:
    """What a load may change, read before and after: the detectors in use, the laser lines, the scan."""
    return {
        "detectors": {d["name"]: d for d in readers.get_detectors(client) or []},
        "laser_lines": {line["wavelength"]: line for line in readers.get_laser_lines(client) or []},
        "zoom": readers.get_zoom(client),
        "scan_speed": readers.get_scan_speed(client),
        "format": readers.get_format(client),
        "averaging": readers.get_averaging(client),
        "pinhole": readers.get_pinhole(client),
        "scan_mode": readers.get_scan_mode(client),
        "z_stack": readers.get_z_stack(client),
    }


def changes_between(before: dict, after: dict) -> list[str]:
    """Every reading that differs, in words: ``"detector Trans PMT: off -> on"``, ``"zoom: ..."``."""
    said = []
    for group in ("detectors", "laser_lines"):
        for key in sorted(set(before[group]) | set(after[group]), key=str):
            a, b = before[group].get(key), after[group].get(key)
            if a != b:
                label = "detector" if group == "detectors" else "laser"
                if a is None or b is None:
                    said.append(f"{label} {key}: {'on' if a else 'off'} -> {'on' if b else 'off'}")
                else:
                    said.append(f"{label} {key}: {a} -> {b}")
    for key in before:
        if key not in ("detectors", "laser_lines") and before[key] != after[key]:
            said.append(f"{key}: {before[key]} -> {after[key]}")
    return said
