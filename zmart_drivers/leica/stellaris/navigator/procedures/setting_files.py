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


def by_command(client, name, *, operating_mode=None, on=None, gain=None, band=None, tau_contrast=None, tau_mode=None) -> list:
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
    panel's switch writes it (2026-11-03); all of these work in any setting,
    the gain within the detector's own range. *dye* (LAS X's name, such as
    ``"Leica/ALEXA 488"``) goes through a saved setting first, Setting 1 only;
    ``set_dyes`` sets dyes in any setting.
    *folder* is where the setting files are kept.
    """
    if operating_mode is not None and operating_mode not in OPERATING_MODES:
        return dispatch.refused(f"no operating mode {operating_mode!r}; LAS X offers {list(OPERATING_MODES)}")
    if tau_mode is not None and tau_mode not in TAU_MODES:
        return dispatch.refused(f"no tau mode {tau_mode!r}; LAS X offers {list(TAU_MODES)}")
    results = []
    if dye is not None:
        # The dye on its own: through the setting, first (set_dyes does it in any setting).
        def change(part):
            return with_detector(part, name, dye=dye)

        def shows(client_, folder_):
            asked = {"dye": dye}
            found = saved_now(client_, folder_)["detectors"].get(name, {})
            return all(found.get(key) == value for key, value in asked.items() if value is not None)

        results.append(through_setting_1(client, folder, change, shows))
        if not results[-1].get("success"):
            return results[-1]
    for done in by_command(client, name, operating_mode=operating_mode, on=on, gain=gain, band=band, tau_contrast=tau_contrast, tau_mode=tau_mode):
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


def set_excitation_line(client, folder, line) -> dict:
    """Choose the excitation line of the setting's detector: a wavelength in nm, or ``"auto"`` for LAS X's choice; read back.

    LAS X keeps one reference line per setting, not one per detector, so
    this is refused while more than one detector is on.
    """
    if len(readers.get_detectors(client) or []) != 1:
        return dispatch.refused("the excitation line is set with one detector on; which detector it belongs to with several is not known")
    if line != "auto":
        found = [entry for entry in readers.get_laser_lines(client) or [] if entry.get("wavelength") == float(line)]
        if not found:
            return dispatch.refused(f"no laser line at {line} nm is on in the setting")
        laser = "WLL" if found[0].get("laserName") == "WLL" else found[0].get("laserName")

    def change(part):
        tag = re.search(r"<DetectionReferenceLine [^>]*>", part)
        if tag is None:
            raise ValueError("the setting has no excitation line")
        new = re.sub(r' IsAutomaticAssigned="[^"]*"', f' IsAutomaticAssigned="{1 if line == "auto" else 0}"', tag[0])
        if line != "auto":
            new = re.sub(r' LaserName="[^"]*"', f' LaserName="{laser}"', new)
            new = re.sub(r' LaserWavelength="[^"]*"', f' LaserWavelength="{float(line):g}"', new)
        return part.replace(tag[0], new, 1)

    def shows(client_, folder_):
        return saved_now(client_, folder_)["excitation_line"] == (line if line == "auto" else float(line))

    return through_setting_1(client, folder, change, shows)


def show_spectra(client, folder, *, excitation=None, emission=None) -> dict:
    """Show or hide the dyes' excitation and emission spectra in LAS X's setting panel; read back."""

    def change(part):
        head = re.search(r"<ATLConfocalSettingDefinition [^>]*>", part)[0]
        new = head
        if excitation is not None:
            new = re.sub(r' IsShowDyeExcitationSpectrum="[^"]*"', f' IsShowDyeExcitationSpectrum="{int(bool(excitation))}"', new)
        if emission is not None:
            new = re.sub(r' IsShowDyeEmissionSpectrum="[^"]*"', f' IsShowDyeEmissionSpectrum="{int(bool(emission))}"', new)
        return part.replace(head, new, 1)

    def shows(client_, folder_):
        found = saved_now(client_, folder_)["show"]
        return (excitation is None or found["excitation"] == excitation) and (emission is None or found["emission"] == emission)

    return through_setting_1(client, folder, change, shows)


def through_setting_1(client, folder, change, shows) -> dict:
    """Save the setting, apply *change* to Setting 1's text in a copy, load it, and set ``confirmed`` by *shows*."""
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
        part = change(text[start:end])
    except (OSError, ValueError) as why:
        return dispatch.refused(f"the setting cannot be changed: {why}")
    changed.write_text(text[:start] + part + text[end:], encoding="utf-8")
    result = load(client, changed)
    if result.get("success"):
        result["confirmed"] = bool(shows(client, folder))
    return result


def saved_now(client, folder) -> dict:
    """Setting 1 as LAS X saves it now (:func:`read_back`); empty when it cannot be saved."""
    Path(folder).mkdir(parents=True, exist_ok=True)
    path = Path(folder) / f"setting_{time.strftime('%Y%m%d-%H%M%S')}_read.seq"
    if not save(client, path).get("success"):
        return {"detectors": {}, "excitation_line": None, "excitation_nm": None, "show": {}}
    return read_back(path.read_text(encoding="utf-8"))


def read_back(text: str) -> dict:
    """What Setting 1 of a saved setting says: each detector's mode, tau mode, contrast and dye; the excitation line; the spectra shown."""
    start, end = setting_1(text)
    part = text[start:end]
    detectors = {}
    for tag in re.findall(r"<Detector [^>]*>", part):
        fields = dict(re.findall(r' (\w+)="([^"]*)"', tag))
        if fields.get("OperatingType") == "1":
            mode = "Reflection" if fields.get("IsReflectionModeActive") == "1" else "Analog"
        else:
            mode = next((n for n, f in OPERATING_MODES.items() if f.get("AcquisitionMode") == fields.get("AcquisitionMode")), None)
        detectors[fields.get("Name")] = {
            "operating_mode": mode,
            "tau_mode": next((n for n, code in TAU_MODES.items() if code == fields.get("DigitalGatingMode")), None),
            "tau_contrast": fields.get("IsLifetimeContrastActive") == "1",
            "dye": fields.get("DyeName"),
        }
    line = re.search(r"<DetectionReferenceLine [^>]*>", part)
    excitation = excitation_nm = None
    if line:
        fields = dict(re.findall(r' (\w+)="([^"]*)"', line[0]))
        excitation_nm = float(fields.get("LaserWavelength", "nan"))
        excitation = "auto" if fields.get("IsAutomaticAssigned") == "1" else excitation_nm
    head = dict(re.findall(r' (\w+)="([^"]*)"', re.search(r"<ATLConfocalSettingDefinition [^>]*>", part)[0]))
    show = {"excitation": head.get("IsShowDyeExcitationSpectrum") == "1", "emission": head.get("IsShowDyeEmissionSpectrum") == "1"}
    return {"detectors": detectors, "excitation_line": excitation, "excitation_nm": excitation_nm, "show": show}


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


def with_detector(
    part: str, name: str, *, on=None, gain=None, band=None,
    operating_mode=None, tau_mode=None, tau_contrast=None, dye=None,
) -> str:
    """Setting 1's text with the detector *name* changed; ValueError when it has no such detector."""
    found = re.search(rf'<Detector [^>]*Name="{re.escape(name)}"[^>]*>', part)
    if found is None:
        raise ValueError(f"the setting has no detector {name!r}")
    tag = found[0]
    fields = {}
    if on is not None:
        fields["IsActive"] = str(int(bool(on)))
    if gain is not None:
        fields["Gain"] = f"{float(gain):g}"
    if operating_mode is not None:
        fields.update(OPERATING_MODES[operating_mode])
    if tau_mode is not None:
        fields["DigitalGatingMode"] = TAU_MODES[tau_mode]
    if tau_contrast is not None:
        fields["IsLifetimeContrastActive"] = str(int(bool(tau_contrast)))
    if dye is not None:
        fields["DyeName"] = str(dye)
    new = tag
    for key, value in fields.items():
        if f' {key}="' not in new:
            raise ValueError(f"the setting's detector {name!r} has no {key}")
        new = re.sub(rf' {key}="[^"]*"', f' {key}="{value}"', new)
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
    return band is None or (detector["detectionBegin"], detector["detectionEnd"]) == tuple(float(v) for v in band)


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
