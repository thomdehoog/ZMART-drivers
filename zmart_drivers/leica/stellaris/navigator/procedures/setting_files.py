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

#: The Operating Mode list, as LAS X writes each choice into the setting (simulator, 2026-11-01).
OPERATING_MODES = {
    "Fast Mode": {"OperatingType": "2", "AcquisitionMode": "5"},
    "Counting": {"OperatingType": "2", "AcquisitionMode": "1"},
    "Analog": {"OperatingType": "1", "IsReflectionModeActive": "0"},
    "Reflection": {"OperatingType": "1", "IsReflectionModeActive": "1"},
}

#: The TauModes list, as ``DigitalGatingMode`` (offered with a pulsed WLL line, in counting): the driver's one list.
TAU_MODES = {name: str(code) for name, code in writes.TAU_CODES.items()}
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


def known_dye(dye) -> str | None:
    """Why *dye* is not one of LAS X's dyes (``Namespace/Name``, the namespace ``Leica`` or ``User:<name>``), or None."""
    from . import dye_library

    if not isinstance(dye, str) or "/" not in dye:
        return f"a dye is named as LAS X names it, Namespace/Name such as 'Leica/ALEXA 488', not {dye!r}"
    namespace, name = dye.split("/", 1)
    try:
        names = dye_library.dye_names(namespace)
    except Exception as why:  # noqa: BLE001 -- LAS X's dye database could not be asked: refuse rather than guess
        return f"LAS X's dye library could not be asked ({why}), so {dye!r} cannot be checked"
    return None if name in names else f"LAS X's dye library has no {name!r} in {namespace!r}"


def by_command(client, name, *, operating_mode=None, on=None, gain=None, band=None, tau_contrast=None, tau_mode=None, dye=None) -> list:
    """The changes of detector *name* LAS X's window makes itself, in the current setting; refused ones as text.

    The operating mode first, written as the window writes it, since a mode
    can fix the gain; then on, gain, band and tau mode with their commands.
    """
    from . import changes

    units = {unit.get("Name"): unit for unit in readers.get_detection_units(client) or []}
    if name not in units:
        return [f"no detector {name!r}; LAS X's are {sorted(units)}"]
    route = writes.route_of(units[name]["BeamRoute"])
    planned = []
    modes = readers.get_detector_modes(client).get(name, {})
    if operating_mode is not None:
        code = writes.OPERATING_CODES.get(operating_mode)
        if code is None or code not in modes.get("operating_offered", []):
            return [f"{name} does not offer the operating mode {operating_mode} in this setting"]
        planned.append(writes.detector_operating_mode(modes["at"], modes["operating_offered"].index(code), name, operating_mode))
    if on is not None:
        planned.append(writes.detector_active(route, name, on))
    if gain is not None:
        shown = {d["name"]: d for d in readers.get_detectors(client) or []}.get(name)
        if shown is not None and not shown["gain"]["min"] <= gain <= shown["gain"]["max"]:
            return [f"{name}'s gain takes {shown['gain']['min']:g} to {shown['gain']['max']:g}, not {gain:g}"]
        planned.append(writes.detector_gain(route, name, gain))
    if band is not None:
        begin, end = band
        planned.append(writes.detector_band(route, name, float(begin), float(end)))
    if dye is not None:
        refusal = known_dye(dye)
        if refusal:
            return [refusal]
        planned.append(writes.detector_dye(modes["at"], name, dye))
    if tau_contrast is not None:
        if not isinstance(tau_contrast, bool):
            return [f"tau_contrast takes True or False, not {tau_contrast!r}"]
        if tau_contrast and not modes.get("tau_contrast_allowed"):
            return [f"LAS X does not offer TauContrast for {name} now (it needs a pulsed line in photon counting)"]
        planned.append(writes.detector_tau_contrast(modes["at"], name, tau_contrast))
    if tau_mode is not None:
        if tau_mode not in writes.TAU_CODES:
            return [f"tau_mode takes {sorted(writes.TAU_CODES)}, not {tau_mode!r}"]
        offered = modes.get("tau_offered", [])
        if writes.TAU_CODES[tau_mode] not in offered:
            return [f"{name} does not offer the tau mode {tau_mode} in this setting"]
        if "tau_available" in modes and writes.TAU_CODES[tau_mode] not in modes["tau_available"]:
            return [f"LAS X greys the tau mode {tau_mode} out for {name} now (it needs a pulsed line, photon counting, or TauContrast for None)"]
        planned.append(writes.detector_tau(route, name, offered.index(writes.TAU_CODES[tau_mode]), tau_mode))
    return [changes.run(client, change) for change in planned]


def set_detector(
    client, folder, name, *, on=None, gain=None, band=None,
    operating_mode=None, tau_mode=None, tau_contrast=None, dye=None,
) -> dict:
    """Change the detector *name* in the current setting, and read it back; any of its fields at once.

    *operating_mode* (:data:`OPERATING_MODES`) is written into the setting as
    the window writes it, first; *on*, *gain*, *band* ``(begin_nm, end_nm)`` and
    *tau_mode* go with LAS X's own commands, as its window sends them
    (simulator, 2026-11-02), and *tau_contrast* is written as the detector
    panel's switch writes it, and *dye* (LAS X's name, such as
    ``"Leica/ALEXA 488"``, checked against its dye library) as the panel's Dye
    list writes it (2026-11-03); all of these work in any setting, the gain
    within the detector's own range.
    *folder* is where the setting files are kept.
    """
    if operating_mode is not None and operating_mode not in OPERATING_MODES:
        return dispatch.refused(f"no operating mode {operating_mode!r}; LAS X offers {list(OPERATING_MODES)}")
    if tau_mode is not None and tau_mode not in TAU_MODES:
        return dispatch.refused(f"no tau mode {tau_mode!r}; LAS X offers {list(TAU_MODES)}")
    results = []
    for done in by_command(client, name, operating_mode=operating_mode, on=on, gain=gain, band=band, tau_contrast=tau_contrast, tau_mode=tau_mode, dye=dye):
        if isinstance(done, str):
            return dispatch.refused(done)
        results.append(done)
        if not done.get("success"):
            return done
    if not results:
        return dispatch.refused(f"nothing to change on {name}")
    result = dict(results[-1])
    result["confirmed"] = all(r.get("confirmed") for r in results)
    result["changed"] = [c for r in results for c in r.get("changed", [])]
    return result


# =============================================================================
# The file: Setting 1, and the values in it
# =============================================================================


def setting_1(text: str) -> tuple[int, int]:
    """Where Setting 1 is in the file: from its definition to the next definition or the autofocus block.

    Setting 1 is the one setting in the file's list, whatever it is called
    (the operator may rename it). ValueError when the list holds none, or
    several sequential settings.
    """
    begins = text.find("<LDM_Block_Sequential_List")
    listed = text[begins:] if begins >= 0 else ""
    in_list = listed.split("<Autofocus_Block")[0]
    if in_list.count("<ATLConfocalSettingDefinition") > 1:
        raise ValueError("LAS X has several sequential settings; which one is meant cannot be told")
    if begins < 0 or "<ATLConfocalSettingDefinition" not in in_list:
        raise ValueError(f"the saved setting has no {SETTING_1}")
    start = text.index("<ATLConfocalSettingDefinition", begins)
    ends = [i for i in (text.find("<ATLConfocalSettingDefinition", start + 1), text.find("<Autofocus_Block", start)) if i > 0]
    return start, min(ends) if ends else len(text)


def values_in(text: str) -> dict:
    """What the limits are shown of a setting file: every laser power (percent) and every active detector's gain in
    every part of it, the block's master copy, each setting and the Best Focus Setting.

    Setting 1 alone is not enough: a file LAS X saved held HyD 2 at gain 10
    in Setting 1 but 100 in the block's master copy, and loading it set the
    hardware's HyD 2 to 100 (simulator, 2026-11-03).
    """
    lasers = [float(v) for v in re.findall(r'<LaserLineSetting [^>]* IntensityDev="([^"]*)"', text) if float(v) >= 0]
    gains = [float(v) for v in re.findall(r'<Detector [^>]*IsActive="1"[^>]* Gain="([^"]*)"', text)]
    return {"laser_power": lasers, "detector_gain": gains}


# =============================================================================
# Reading back, and what else changed
# =============================================================================


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
    for key, was in before.items():
        if key not in ("detectors", "laser_lines") and was != after[key]:
            said.append(f"{key}: {was} -> {after[key]}")
    return said
