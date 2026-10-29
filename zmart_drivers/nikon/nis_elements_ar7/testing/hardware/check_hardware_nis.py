"""The hardware check, run inside NIS-Elements: does each row of the driver's table work on this microscope?

Start it from NIS-Elements with ``check_hardware.mac`` (Macro > Run Macro From
File). It runs in NIS's own Python and needs nothing installed: it calls NIS's
functions directly, exactly as the bridge does for the driver, from the same
table (``primitives/nis_python/devices.py``). What it confirms here is
therefore what the driver sends.

It runs in three parts. The first only reads; the others are switched off
until you switch them on below, one at a time, with someone at the microscope.

1. **What NIS has** (always; nothing changes):
   - NIS's version and its Python;
   - whether NIS offers each function the driver uses, with NIS's own
     description of it where NIS gives one;
   - where NIS keeps its macro reference, and what it says about each of
     these functions (copied into ``reference.txt``);
   - every hardware setting of the table: which devices are there, and their
     values now;
   - the stage, the focus, the objectives and the optical configurations.
2. **Small changes, each put back**: for each group switched on below, every
   device that is there is changed and read back, then put back as it was.
   A switch is turned over and back; a choice moves one position and back; a
   number is set to the value it already has, which tries the function's
   arguments without changing anything. A guarded group (lasers, TIRF, the
   environment) is only ever set to the value it already has.
   The stage moves 20 um and back, the focus 2 um and back, the nosepiece one
   position and back, and the optical configuration is selected again.
3. **One picture**, taken into a new document, saved as a TIFF in the results
   folder, and that document alone closed, as the driver's snap does.

Everything goes into a ``results`` folder beside this file:
``check_summary.txt`` (one line per check), ``check_report.json`` (all of it),
``check.log`` (written as it goes: if NIS hangs, its last line names the
call) and ``reference.txt``.

Standard library and NIS's own ``nis`` module only.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
Modelled on step 0 of the NIS 7 study in thomdehoog/nis-useq-aiagent.
"""

from __future__ import annotations

import glob
import importlib.util
import json
import os
import platform
import sys
import time
import traceback

# -- switch on what may change, one at a time, with someone at the microscope ---------------
ALLOW_GROUPS = {
    "optics": False,  # light path, condenser, aperture, analyzer, zoom, relay lens, correction collar
    "filters": False,  # filter wheels and turrets
    "light": False,  # shutters, lamps and LEDs (turned on and off again), ND filters
    "focus": False,  # Perfect Focus switched on and off again, its offset set to its own value
    "spinning disk": False,  # its shutter and speed
    # Guarded groups: only ever set to the value they already have.
    "TIRF": False,
    "lasers": False,
    "environment": False,
}
ALLOW_STAGE = False  # x and y 20 um and back, z 2 um and back
ALLOW_OBJECTIVE = False  # the nosepiece one position and back
ALLOW_OPTICAL_CONFIGURATION = False  # the selected optical configuration selected again
ALLOW_SNAP = False  # one picture, saved as a TIFF in the results folder
# With the microscope switched off, call every one of the driver's functions once,
# with the arguments the driver sends, to see whether NIS takes them. It changes
# nothing: it only runs when NIS reports the stage as not available.
ALLOW_ARGUMENT_CHECK_WHEN_OFF = True

STAGE_STEP_UM = 20.0
#: How long to let a device settle after each change, before it is read back.
SETTLE_S = 0.5
Z_STEP_UM = 2.0
GUARDED_GROUPS = ("TIRF", "lasers", "environment")
#: What Get_InfoStr is asked for NIS's version, as the driver's own reading asks it.
INFO_VERSION = 1

#: Where this file is, and where the driver's bridge and table are beside it.
HERE = os.path.dirname(os.path.abspath(__file__))
NIS_PYTHON = os.path.join(
    os.path.dirname(os.path.dirname(HERE)), "primitives", "nis_python"
)
#: Where the results folder goes: beside this file, unless ZMART_CHECK_RESULTS names another folder.
RESULTS_ROOT = os.environ.get("ZMART_CHECK_RESULTS") or HERE

#: Where NIS may keep its macro reference, besides the folder its Python runs from.
NIS_FOLDERS = (r"C:\Program Files\NIS-Elements", r"C:\Program Files (x86)\NIS-Elements")
REFERENCE_PATTERNS = ("*macro*", "*Macro*", "*MACRO*", "*function*", "*Function*")
REFERENCE_SUFFIXES = (".htm", ".html", ".txt", ".xml", ".chm", ".pdf", ".hlp", ".json")


def load(name: str, path: str):
    """A file of the driver, loaded by its path (inside NIS the driver is not installed)."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class Check:
    """The run: its log, its report, and how each NIS function is called."""

    def __init__(self, nis_module, out_dir: str) -> None:
        self.nis = nis_module
        self.out_dir = out_dir
        os.makedirs(out_dir, exist_ok=True)
        self.log_file = open(os.path.join(out_dir, "check.log"), "a", encoding="utf-8")  # noqa: SIM115
        self.server = load("zmart_check_server", os.path.join(NIS_PYTHON, "server.py"))
        self.devices = self.server.devices
        self.report = {
            "started": time.strftime("%Y-%m-%d %H:%M:%S"),
            "checks": [],
            "settings": settings(),
        }

    def log(self, text: str) -> None:
        self.log_file.write(f"{time.strftime('%H:%M:%S')} {text}\n")
        self.log_file.flush()

    def call(self, fn: str, args: list) -> dict:
        """One NIS function, as the bridge calls it; logged before and after, so a hang names the call."""
        self.log(f"call {fn} {args!r}")
        answer = self.server.call(self.nis, fn, list(args))
        self.log(f"  -> {answer!r}")
        return answer

    def check(self, name: str, how, *, changes: bool = False):
        """Run one check; its answer, or None when it failed. Never raises."""
        self.log(f"== {name}")
        started = time.monotonic()
        try:
            answer = how()
            outcome = "ok"
        except Exception as error:  # noqa: BLE001 -- every failure is an answer worth keeping
            answer = f"{type(error).__name__}: {error}"
            outcome = "FAIL"
            self.log(traceback.format_exc())
        entry = {
            "check": name,
            "outcome": outcome,
            "changes": changes,
            "seconds": round(time.monotonic() - started, 3),
            "answer": answer,
        }
        self.report["checks"].append(entry)
        self.log(f"   {outcome}: {answer!r}"[:2000])
        return answer if outcome == "ok" else None

    def finish(self) -> dict:
        self.report["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(
            os.path.join(self.out_dir, "check_report.json"), "w", encoding="utf-8"
        ) as handle:
            json.dump(self.report, handle, indent=2, default=repr)
        lines = [f"{e['outcome']:4}  {e['check']}" for e in self.report["checks"]]
        failed = sum(e["outcome"] != "ok" for e in self.report["checks"])
        lines.append(f"{len(lines) - failed} ok, {failed} failed")
        with open(
            os.path.join(self.out_dir, "check_summary.txt"), "w", encoding="utf-8"
        ) as handle:
            handle.write("\n".join(lines) + "\n")
        self.log_file.close()
        return self.report


def settings() -> dict:
    return {
        "ALLOW_GROUPS": dict(ALLOW_GROUPS),
        "ALLOW_STAGE": ALLOW_STAGE,
        "ALLOW_OBJECTIVE": ALLOW_OBJECTIVE,
        "ALLOW_OPTICAL_CONFIGURATION": ALLOW_OPTICAL_CONFIGURATION,
        "ALLOW_SNAP": ALLOW_SNAP,
    }


# -- part 1: what NIS has ----------------------------------------------------------------------

DRIVER_FUNCTIONS_BEYOND_THE_TABLE = (
    "StgMoveXY",
    "StgMoveZ",
    "SelectOptConf",
    "Stg_SetNosepiecePosition",
    "Capture",
    "ImageSaveAs",
    "CloseCurrentDocument",
    "ActivateDocument",
    "SaveImageFullDescription",
)


def functions_used(check: Check) -> list[str]:
    return sorted(
        check.server.READS
        | check.server.WRITES
        | set(DRIVER_FUNCTIONS_BEYOND_THE_TABLE)
    )


def offered(check: Check) -> dict:
    """Whether NIS offers each function the driver uses, and what NIS says about it."""
    found = {}
    for name in functions_used(check):
        try:
            function = getattr(check.nis.mac, name)
        except Exception as error:  # noqa: BLE001
            found[name] = {"offered": False, "why": f"{type(error).__name__}: {error}"}
            continue
        entry = {"offered": True}
        for attr in ("__doc__", "__text_signature__"):
            value = getattr(function, attr, None)
            if value:
                entry[attr.strip("_")] = str(value)[:500]
        found[name] = entry
    missing = sorted(name for name, entry in found.items() if not entry["offered"])
    return {"functions": found, "not offered": missing}


def nis_folders() -> list[str]:
    """Where NIS-Elements may be installed on this computer: the usual places, and wherever its Python runs from."""
    found = {path for path in NIS_FOLDERS if os.path.isdir(path)}
    for pattern in (r"C:\Program Files*\*NIS*", r"C:\Program Files*\Nikon\*"):
        found.update(path for path in glob.glob(pattern) if os.path.isdir(path))
    for place in (sys.executable, sys.prefix):
        for folder in (
            os.path.dirname(place),
            os.path.dirname(os.path.dirname(place)),
            place,
        ):
            if folder and "nis" in folder.lower() and os.path.isdir(folder):
                found.add(folder)
    # A folder inside another one found is searched with it.
    return sorted(
        f for f in found if not any(f != o and f.startswith(o + os.sep) for o in found)
    )


def read_text(path: str) -> str | None:
    """A help file's text, whatever Windows encoding it is in."""
    try:
        raw = open(path, "rb").read()  # noqa: SIM115 -- read whole, closed at once
    except OSError:
        return None
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    for encoding in ("utf-8", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


#: The most help pages copied into the results folder, and their largest total size.
PAGES_KEPT = 300
PAGES_BYTES = 30_000_000


def reference(check: Check) -> dict:
    """Find NIS's macro reference on this computer, and copy out what it says about each function the driver uses.

    A help file is anything under NIS's folders that is documentation (in a
    ``Docs`` folder) or whose path speaks of macros, help or functions. Every
    place a text page names one of the driver's functions is quoted into
    ``reference.txt``, and each such page is copied into the results folder's
    ``help_pages``, to be sent along. A compiled help file (``.chm``) cannot be
    read here, so the ones about macros are copied whole.
    """
    import re

    roots = nis_folders()
    files = []
    for root in roots:
        for folder, _, names in os.walk(root):
            for name in names:
                path = os.path.join(folder, name)
                lowered = path.lower()
                if lowered.endswith(REFERENCE_SUFFIXES) and any(
                    word in lowered
                    for word in ("macro", "help", "function", os.sep + "docs" + os.sep)
                ):
                    files.append(path)
    copied = []
    for path in files:
        if (
            path.lower().endswith(".chm")
            and "macro" in path.lower()
            and os.path.getsize(path) < 50_000_000
        ):
            target = os.path.join(check.out_dir, "help_" + os.path.basename(path))
            with open(path, "rb") as source, open(target, "wb") as copy:
                copy.write(source.read())
            copied.append(target)
    names = functions_used(check)
    named = re.compile(
        r"(?<![A-Za-z0-9_])("
        + "|".join(sorted(map(re.escape, names), key=len, reverse=True))
        + r")(?![A-Za-z0-9_])"
    )
    excerpts, mentioned, pages = [], set(), []
    pages_dir = os.path.join(check.out_dir, "help_pages")
    kept_bytes = 0
    for path in files[:20000]:
        if not path.lower().endswith((".htm", ".html", ".txt", ".xml", ".json")):
            continue
        text = read_text(path)
        if not text:
            continue
        here = set()
        for match in named.finditer(text):
            name = match.group(1)
            if name in here:
                continue
            here.add(name)
            excerpts.append(
                f"--- {name}  ({path})\n{text[max(match.start() - 300, 0) : match.start() + 900]}\n"
            )
        if here:
            mentioned |= here
            size = os.path.getsize(path)
            if len(pages) < PAGES_KEPT and kept_bytes + size <= PAGES_BYTES:
                os.makedirs(pages_dir, exist_ok=True)
                relative = os.path.relpath(
                    path,
                    os.path.commonpath([path, *roots])
                    if roots
                    else os.path.dirname(path),
                )
                target = os.path.join(
                    pages_dir, relative.replace(os.sep, "__").replace(":", "")
                )
                with open(path, "rb") as source, open(target, "wb") as copy:
                    copy.write(source.read())
                pages.append(path)
                kept_bytes += size
    with open(
        os.path.join(check.out_dir, "reference.txt"), "w", encoding="utf-8"
    ) as handle:
        handle.write(
            "Where NIS keeps its macro reference:\n"
            + "\n".join(files)
            + "\n\n"
            + "\n".join(excerpts)
        )
    return {
        "searched": roots,
        "help files": len(files),
        "first help files": files[:50],
        "functions found in them": len(mentioned),
        "functions not found in them": sorted(set(names) - mentioned),
        "pages copied into help_pages": len(pages),
        "compiled help copied (send these along)": copied,
    }


def stage_and_optics(check: Check) -> dict:
    """The readings the driver already relies on, read once."""
    call = check.call
    position = call("StgGetPosXY", [{"ptr": "double"}, {"ptr": "double"}])
    z = call("StgGetPosZ", [{"ptr": "double"}, 0])
    count = call("GetOptConfCount", [])["return"]
    names = [
        call("GetOptConfName", [i, {"ptr": "char"}, 256])["values"][1]
        for i in range(max(int(count or 0), 0))
    ]
    selected = call(
        "GetSelectedOptConf", [{"ptr": "char"}, {"ptr": "char"}, {"ptr": "char"}, 256]
    )
    nosepiece = call("Stg_GetNosepiecePosition", [])["return"]
    return {
        "xy": position["values"],
        "xy return": position["return"],
        "z": z["values"][0],
        "optical configurations": names,
        "selected optical configuration texts": selected["values"][:3],
        "nosepiece position": nosepiece,
    }


# -- part 2: small changes, each put back --------------------------------------------------------


def try_setting(check: Check, row, item: dict) -> dict:
    """Change one device of one row, read it back, put it back, read it again."""
    devices = check.devices
    caller = lambda fn, args: check.call(fn, args)
    instance = item["instance"]
    before = devices.read(caller, row, instance)
    guarded = row.guarded or row.group in GUARDED_GROUPS
    if guarded or row.kind == devices.NUMBER:
        target = before
    elif row.kind == devices.SWITCH:
        target = 1 - int(before)
    else:
        positions = item.get("positions") or 0
        target = before % positions + 1 if positions >= 2 else before
    try:
        sent = check.call(row.write.fn, devices.write_arguments(row, instance, target))
        time.sleep(SETTLE_S)
        after = devices.read(caller, row, instance)
    finally:
        # Whatever happened above, the device is put back as it was.
        back = check.call(row.write.fn, devices.write_arguments(row, instance, before))
    time.sleep(SETTLE_S)
    again = devices.read(caller, row, instance)
    return {
        "before": before,
        "set to": target,
        "returned": sent["return"],
        "read after": after,
        "shows it": devices.matches(row, target, after)
        if row.confirms
        else "not read back (a target)",
        "put back returned": back["return"],
        "read at the end": again,
        "back as before": devices.matches(row, before, again)
        if row.confirms
        else "not read back (a target)",
    }


def changes(check: Check, inventory: dict) -> None:
    for row in check.devices.TABLE:
        if not ALLOW_GROUPS.get(row.group):
            continue
        if not row.confirms:
            # Its reading is not what it sets (a heating stage reads its current
            # temperature but is set to a target): writing the reading back would
            # change the target. It is listed in part 1 and never written here.
            continue
        for item in inventory.get(row.key, {}).get("devices", []):
            name = row.key + (f"/{item['instance']}" if item["instance"] else "")

            def attempt(row=row, item=item):
                found = try_setting(check, row, item)
                if row.confirms and not (found["shows it"] and found["back as before"]):
                    raise RuntimeError(
                        f"not seen as set, or not back as before: {found}"
                    )
                return found

            check.check(f"change {name}", attempt, changes=True)


def stage(check: Check) -> None:
    call = check.call

    def xy():
        x, y = call("StgGetPosXY", [{"ptr": "double"}, {"ptr": "double"}])["values"]
        started = time.monotonic()
        try:
            moved = call("StgMoveXY", [x + STAGE_STEP_UM, y, 0])["return"]
            returned_after = round(time.monotonic() - started, 3)
            there = call("StgGetPosXY", [{"ptr": "double"}, {"ptr": "double"}])[
                "values"
            ]
        finally:
            call("StgMoveXY", [x, y, 0])
        back = call("StgGetPosXY", [{"ptr": "double"}, {"ptr": "double"}])["values"]
        return {
            "from": [x, y],
            "returned": moved,
            "returned after s": returned_after,
            "read there": there,
            "read back": back,
        }

    def z():
        z0 = call("StgGetPosZ", [{"ptr": "double"}, 0])["values"][0]
        started = time.monotonic()
        try:
            moved = call("StgMoveZ", [z0 + Z_STEP_UM, 0])["return"]
            returned_after = round(time.monotonic() - started, 3)
            there = call("StgGetPosZ", [{"ptr": "double"}, 0])["values"][0]
        finally:
            call("StgMoveZ", [z0, 0])
        back = call("StgGetPosZ", [{"ptr": "double"}, 0])["values"][0]
        return {
            "from": z0,
            "returned": moved,
            "returned after s": returned_after,
            "read there": there,
            "read back": back,
        }

    check.check("stage x, y 20 um and back", xy, changes=True)
    check.check("focus 2 um and back", z, changes=True)


def objective(check: Check) -> None:
    call = check.call

    def turn():
        before = call("Stg_GetNosepiecePosition", [])["return"]
        positions = call("Stg_GetNosepiecePositions", [])["return"]
        target = before % positions + 1
        z_before = call("StgGetPosZ", [{"ptr": "double"}, 0])["values"][0]
        try:
            returned = call("Stg_SetNosepiecePosition", [target])["return"]
            there = call("Stg_GetNosepiecePosition", [])["return"]
            z_there = call("StgGetPosZ", [{"ptr": "double"}, 0])["values"][0]
        finally:
            call("Stg_SetNosepiecePosition", [before])
        back = call("Stg_GetNosepiecePosition", [])["return"]
        return {
            "from": before,
            "to": target,
            "returned": returned,
            "read there": there,
            "z before": z_before,
            "z there": z_there,
            "read back": back,
        }

    check.check("nosepiece one position and back", turn, changes=True)


def optical_configuration(check: Check, names: list) -> None:
    call = check.call

    def again():
        texts = call(
            "GetSelectedOptConf",
            [{"ptr": "char"}, {"ptr": "char"}, {"ptr": "char"}, 256],
        )["values"][:3]
        chosen = next((t for t in texts if t in names), names[0] if names else None)
        if chosen is None:
            raise RuntimeError("NIS has no optical configuration")
        returned = call("SelectOptConf", [chosen])["return"]
        after = call(
            "GetSelectedOptConf",
            [{"ptr": "char"}, {"ptr": "char"}, {"ptr": "char"}, 256],
        )["values"][:3]
        return {
            "selected": chosen,
            "texts before": texts,
            "returned": returned,
            "texts after": after,
        }

    check.check("optical configuration selected again", again, changes=True)


def snap(check: Check) -> None:
    def picture():
        bridge = check.server.Bridge(check.nis, "check")
        path = os.path.join(
            os.path.abspath(check.out_dir), f"snap_{time.strftime('%H%M%S')}.tif"
        )
        documents_before = bridge.documents()
        answer = bridge.snap(path)
        return {
            **answer,
            "documents before": documents_before,
            "documents after": bridge.documents(),
        }

    check.check(
        "one picture, saved as a TIFF, its document closed", picture, changes=True
    )


# -- with the microscope off: does NIS take each function's arguments? -------------------

#: NIS's answer to a function whose hardware is not there (or switched off).
NOT_AVAILABLE = -4
#: The words NIS raises when a function is given the wrong number of arguments.
WRONG_COUNT = "Bad number of parameters"

#: The driver's own functions outside the table, with the arguments it sends
#: (``None`` is filled in from the stage's own reading, so a call moves nothing).
DRIVER_CALLS = (
    ("Get_InfoStr", [INFO_VERSION, {"ptr": "char"}]),
    ("StgGetPosXY", [{"ptr": "double"}, {"ptr": "double"}]),
    ("StgGetPosZ", [{"ptr": "double"}, 0]),
    ("StgXY_GetLimits", [{"ptr": "double"}] * 4),
    ("StgZ_GetLimits", [{"ptr": "double"}] * 2),
    ("GetOptConfCount", []),
    ("GetOptConfName", [0, {"ptr": "char"}, 256]),
    ("GetSelectedOptConf", [{"ptr": "char"}, {"ptr": "char"}, {"ptr": "char"}, 256]),
    ("Stg_IsNosepiecePresent", []),
    ("Stg_GetNosepiecePositions", []),
    ("Stg_GetNosepiecePosition", []),
    ("Stg_GetNosepieceObjectiveName", [1, {"ptr": "char"}, 256]),
    ("Stg_IsPFSPresent", []),
    ("Stg_GetPFSStatus", []),
    (
        "Get_ImageInfo",
        ["", {"ptr": "int"}, {"ptr": "int"}, {"ptr": "int"}, {"ptr": "int"}],
    ),
    (
        "Get_Calibration",
        [{"ptr": "char"}, {"ptr": "double"}, {"ptr": "double"}, {"ptr": "int"}],
    ),
    ("GetOpenedDocumentList", [{"ptr": "char"}, 4096]),
)
#: Groups whose changes are never sent in this mode, even with the microscope off:
#: a wrong target there could matter if something were on after all.
NEVER_SENT_WHEN_OFF = ("environment", "TIRF")


def classify(check: Check, fn: str, args: list) -> dict:
    """Call *fn* once with *args*; whether NIS took the arguments, and what it answered."""
    try:
        answer = check.call(fn, args)
    except Exception as error:  # noqa: BLE001 -- the error is the answer
        text = f"{type(error).__name__}: {error}"
        if WRONG_COUNT.lower() in text.lower():
            raise RuntimeError(f"NIS refused the arguments {args!r}: {text}") from None
        raise RuntimeError(f"NIS raised for {args!r}: {text}") from None
    returned = answer.get("return")
    meaning = (
        "not available (the hardware is off or absent)"
        if returned == NOT_AVAILABLE
        else "answered"
    )
    return {
        "arguments": args,
        "returned": returned,
        "values": answer.get("values"),
        "meaning": meaning,
    }


def microscope_is_off(check: Check) -> bool:
    """Whether NIS reports the stage as not available, which it does with the microscope switched off."""
    try:
        return (
            check.call("StgGetPosXY", [{"ptr": "double"}, {"ptr": "double"}])["return"]
            == NOT_AVAILABLE
        )
    except Exception:  # noqa: BLE001 -- when in doubt, it is not off
        return False


def argument_check(check: Check) -> None:
    """Call every function the driver uses once, with its arguments, and say for each whether NIS took them."""
    devices = check.devices
    for fn, args in DRIVER_CALLS:
        check.check(
            f"arguments of {fn}", lambda fn=fn, args=args: classify(check, fn, args)
        )
    seen = {fn for fn, _ in DRIVER_CALLS}
    tokens = {devices.NAME: "", devices.INDEX: 0, devices.LASER: 0, devices.LINE: 0}
    for row in devices.TABLE:
        for call in (row.present, row.count, row.name, row.positions, row.read):
            if call is None or call.fn in seen:
                continue
            seen.add(call.fn)
            args = devices.arguments(call.args, tokens)
            check.check(
                f"arguments of {call.fn}",
                lambda fn=call.fn, args=args: classify(check, fn, args),
            )
    if "Stg_GetMultiLaserNumLines" not in seen:
        check.check(
            "arguments of Stg_GetMultiLaserNumLines",
            lambda: classify(check, "Stg_GetMultiLaserNumLines", [0]),
        )

    if not microscope_is_off(check):
        check.check(
            "the changes' arguments",
            lambda: (
                "not tried: NIS reports the stage as available, so the "
                "microscope may be on; switch on one group at a time at the top instead"
            ),
        )
        return
    xy = check.call("StgGetPosXY", [{"ptr": "double"}, {"ptr": "double"}])["values"]
    z = check.call("StgGetPosZ", [{"ptr": "double"}, 0])["values"][0]
    position = check.call("Stg_GetNosepiecePosition", [])["return"]
    writes = [
        ("StgMoveXY", [xy[0], xy[1], 0]),
        ("StgMoveZ", [z, 0]),
        (
            "Stg_SetNosepiecePosition",
            [position if isinstance(position, int) and position > 0 else 1],
        ),
    ]
    for row in devices.TABLE:
        if row.group in NEVER_SENT_WHEN_OFF:
            continue
        try:
            value = devices.read(
                lambda fn, args: check.call(fn, args),
                row,
                ""
                if row.devices == "named"
                else ("0.0" if row.devices == "laser_lines" else None),
            )
        except Exception:  # noqa: BLE001 -- absent: a neutral value, which the hardware cannot act on while off
            value = 1 if row.kind == devices.CHOICE else 0
        if row.devices == "laser_lines":
            value = 0  # a laser line is never switched on or powered, not even here
        args = devices.arguments(row.write.args, tokens, value)
        writes.append((row.write.fn, args))
    for fn, args in writes:
        check.check(
            f"arguments of {fn} (a change, with the microscope off)",
            lambda fn=fn, args=args: classify(check, fn, args),
            changes=True,
        )


def main(nis_module=None, out_dir: str | None = None) -> dict:
    if nis_module is None:
        import nis as nis_module  # exists only inside NIS-Elements
    out_dir = out_dir or os.path.join(
        RESULTS_ROOT, "results", time.strftime("%Y%m%d_%H%M%S")
    )
    check = Check(nis_module, out_dir)
    check.check(
        "NIS and its Python",
        lambda: {
            "nis": check.call("Get_InfoStr", [INFO_VERSION, {"ptr": "char"}])["values"][
                1
            ],
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "table": {
                "settings": len(check.devices.TABLE),
                "confirmed": sum(r.confirmed for r in check.devices.TABLE),
            },
        },
    )
    check.check("which of the driver's functions NIS offers", lambda: offered(check))
    check.check("NIS's macro reference", lambda: reference(check))
    caller = lambda fn, args: check.call(fn, args)
    inventory = (
        check.check(
            "every hardware setting: which devices are there, and their values",
            lambda: check.devices.discover(caller),
        )
        or {}
    )
    readings = (
        check.check(
            "the stage, the focus, the objectives, the optical configurations",
            lambda: stage_and_optics(check),
        )
        or {}
    )
    if ALLOW_ARGUMENT_CHECK_WHEN_OFF:
        argument_check(check)
    changes(check, inventory)
    if ALLOW_STAGE:
        stage(check)
    if ALLOW_OBJECTIVE:
        objective(check)
    if ALLOW_OPTICAL_CONFIGURATION:
        optical_configuration(check, readings.get("optical configurations") or [])
    if ALLOW_SNAP:
        snap(check)
    report = check.finish()
    try:
        nis_module.log(f"ZMART hardware check finished: {out_dir}")
    except Exception:  # noqa: BLE001, S110 -- the log line is only a courtesy
        pass
    return report


if __name__ == "__main__":
    main()
