"""The hardware check, run inside NIS-Elements: does each row of the driver's table work on this microscope?

Start it from NIS-Elements with ``check_hardware.mac`` (Macro > Run Macro From
File). It runs in NIS's own Python and needs nothing installed: it calls NIS's
functions directly, exactly as the bridge does for the driver, from the same
table (``primitives/nis_python/devices.py``). What it confirms here is
therefore what the driver sends.

The same checks run with the microscope switched on or off. With it off,
NIS answers "not available" (-4) where the hardware is missing: that is the
baseline of how it reacts, and it shows that NIS takes each call's arguments
(a wrong argument list is refused with "Bad number of parameters" instead).
With it on, each change really happens, and is seen.

1. **What NIS has**: NIS's version and its Python; whether NIS offers each
   function the driver uses; what NIS's macro reference on this computer
   says about them (``reference.txt``, and the pages in ``help_pages``);
   every hardware setting, which devices are there, and their values.
2. **Every reading**, called once with the driver's arguments, one line each.
3. **Every change, the smallest that can be seen, each put back**: a switch
   is turned over and back; a position moves one step and back; a number is
   nudged by about three times its read-back tolerance and back; the stage
   moves 5 um and back, the focus 1 um and back. Each is read back after the
   change and again after putting it back. What must not change is set to
   the value it already has: Perfect Focus on or off and the nosepiece
   (unless switched on below), the disk's speed, and everything guarded
   (lasers, TIRF, heating, gas). Where a value cannot be read (the hardware
   off), the change is sent once with a neutral value, and "not available"
   is the expected answer; a guarded one is not sent at all.
4. **One picture**, taken into a new document, saved as a TIFF in the
   results folder, and that document alone closed, as the driver's snap does.

Set ``CHANGES = False`` below for a run that only reads.

Everything goes into a folder named after the time of the run, inside the
folder ``ZMART check results`` on the Desktop:
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

# -- what the check may change ------------------------------------------------------------
# The same checks run with the microscope on or off. With it off, NIS answers
# "not available" (-4), which is the baseline of how it reacts. With it on,
# each change is the smallest that can be seen, read back, and put back.
CHANGES = True  # False: only read, change nothing at all
TURN_NOSEPIECE = (
    False  # True: turn to the next objective and back (mind a long or oil objective)
)
SWITCH_PFS = False  # True: switch Perfect Focus on and off again (it may drive the focus a long way)
SNAP = True  # one picture, into a new document of its own, saved as a TIFF in the results folder
DRIVER = True  # also run the driver itself (connect, move, set, state, acquire) through its bridge
#: The longest the driver part may take before the check gives up on it.
DRIVER_TIME_LIMIT_S = 600

#: The smallest moves that the read-back can tell apart (its tolerance is 1 um for x and y, 0.5 um for z).
STAGE_STEP_UM = 5.0
Z_STEP_UM = 1.0
#: A number is nudged by this many times its read-back tolerance, and back.
NUDGE_TOLERANCES = 3
#: How long to let a device settle after each change, before it is read back.
SETTLE_S = 0.5
#: Settings only ever set to the value they already have: a wrong value could harm the
#: sample, the microscope or a person, and a disk takes long to change speed.
OWN_VALUE_ONLY = ("disk_speed",)
#: What Get_InfoStr is asked for NIS's version, as the driver's own reading asks it.
INFO_VERSION = 1

#: Where this file is, and where the driver's bridge and table are beside it.
HERE = os.path.dirname(os.path.abspath(__file__))
NIS_PYTHON = os.path.join(
    os.path.dirname(os.path.dirname(HERE)), "primitives", "nis_python"
)


def desktop() -> str:
    """The Desktop of the person running NIS (also when OneDrive keeps it), or their home folder."""
    home = os.path.expanduser("~")
    for place in [
        os.path.join(home, "Desktop"),
        *sorted(glob.glob(os.path.join(home, "OneDrive*", "Desktop"))),
    ]:
        if os.path.isdir(place):
            return place
    return home


#: Where the results go: a folder "ZMART check results" on the Desktop, one folder per run in it,
#: unless ZMART_CHECK_RESULTS names another place. It is kept short on purpose: Windows refuses
#: paths longer than 260 characters, and an unzipped download often sits two folders deep.
RESULTS_ROOT = os.environ.get("ZMART_CHECK_RESULTS") or os.path.join(
    desktop(), "ZMART check results"
)

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
            outcome = (
                "skip" if isinstance(answer, dict) and answer.get("skipped") else "ok"
            )
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
        return answer if outcome != "FAIL" else None

    def finish(self) -> dict:
        self.report["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(
            os.path.join(self.out_dir, "check_report.json"), "w", encoding="utf-8"
        ) as handle:
            json.dump(self.report, handle, indent=2, default=repr)
        lines = [f"{e['outcome']:4}  {e['check']}" for e in self.report["checks"]]
        failed = sum(e["outcome"] == "FAIL" for e in self.report["checks"])
        skipped = sum(e["outcome"] == "skip" for e in self.report["checks"])
        lines.append(
            f"{len(lines) - failed - skipped} ok, {failed} failed, {skipped} skipped"
        )
        with open(
            os.path.join(self.out_dir, "check_summary.txt"), "w", encoding="utf-8"
        ) as handle:
            handle.write("\n".join(lines) + "\n")
        self.log_file.close()
        return self.report


def settings() -> dict:
    return {
        "CHANGES": CHANGES,
        "TURN_NOSEPIECE": TURN_NOSEPIECE,
        "SWITCH_PFS": SWITCH_PFS,
        "SNAP": SNAP,
        "DRIVER": DRIVER,
        "STAGE_STEP_UM": STAGE_STEP_UM,
        "Z_STEP_UM": Z_STEP_UM,
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


# -- part 2: every function, and the smallest change, each put back ---------------------------

#: NIS's answer to a function whose hardware is not there, or switched off.
NOT_AVAILABLE = -4
#: The words NIS raises when a function is given the wrong number of arguments.
WRONG_COUNT = "Bad number of parameters"

#: The driver's readings outside the table, with the arguments the driver sends.
DRIVER_READS = (
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


def answered(check: Check, fn: str, args: list) -> dict:
    """Call *fn* once; NIS's answer. ``RuntimeError`` only when NIS refused the call itself (wrong arguments)."""
    try:
        answer = check.call(fn, args)
    except Exception as error:  # noqa: BLE001 -- the error is the answer
        text = f"{type(error).__name__}: {error}"
        why = (
            "NIS refused the arguments"
            if WRONG_COUNT.lower() in text.lower()
            else "NIS raised"
        )
        raise RuntimeError(f"{fn}{tuple(args)!r}: {why}: {text}") from None
    returned = answer.get("return")
    return {
        "arguments": args,
        "returned": returned,
        "values": answer.get("values"),
        "meaning": "not available (the hardware is off or absent)"
        if returned == NOT_AVAILABLE
        else "answered",
    }


def available(answer: dict) -> bool:
    """Whether a reading came with a value, not NIS's code for "not available" (or another failure)."""
    returned = answer.get("returned")
    return not (
        isinstance(returned, int) and not isinstance(returned, bool) and returned < 0
    )


def reads(check: Check) -> None:
    """Every reading the driver uses, called once with its arguments: one line each."""
    devices = check.devices
    for fn, args in DRIVER_READS:
        check.check(f"read {fn}", lambda fn=fn, args=args: answered(check, fn, args))
    seen = {fn for fn, _ in DRIVER_READS}
    tokens = {devices.NAME: "", devices.INDEX: 0, devices.LASER: 0, devices.LINE: 0}
    extra = [devices.Call("Stg_GetMultiLaserNumLines", (devices.LASER,))]
    for call in [
        c
        for row in devices.TABLE
        for c in (row.present, row.count, row.name, row.positions, row.read)
    ] + extra:
        if call is None or call.fn in seen:
            continue
        seen.add(call.fn)
        args = devices.arguments(call.args, tokens)
        check.check(
            f"read {call.fn}", lambda fn=call.fn, args=args: answered(check, fn, args)
        )


#: How NIS says it does not take the arguments it was given (as opposed to an answer about the hardware).
REFUSED_ARGUMENTS = ("Bad number of parameters", "Expected macro param")
#: The most argument shapes tried for one reading.
SHAPES_TRIED = 40


def shapes(args: list) -> list[list]:
    """Other ways of giving a reading its arguments, closest to *args* first.

    NIS's Python does not always take its functions' arguments the way the
    macro language writes them: a value may come back as the function's
    answer instead of through a pointer, a device may be named by its index
    instead of its name, and a number may be whole instead of decimal. Each
    shape here changes one or two of those things, so that the first shape NIS
    accepts tells how the table should call the function.
    """
    pointers = ("char", "int", "double")
    is_ptr = lambda a: isinstance(a, dict) and "ptr" in a
    is_size = lambda a: a in (256, 4096) and not isinstance(a, bool)
    plain = [a for a in args if not is_ptr(a)]
    bases = [
        [a for a in args if not is_size(a)],
        plain,
        [a for a in plain if not is_size(a)],
        [],
    ]
    # The device named by its index instead of its name, and the other way round.
    swap = lambda shape: [
        0 if a == "" else "" if a == 0 and not isinstance(a, bool) else a for a in shape
    ]
    bases += [swap(b) for b in [args, *bases]]
    found = []
    for base in bases:
        found.append(base)
        # Each pointer of another kind.
        for i, a in enumerate(base):
            if is_ptr(a):
                for kind in pointers:
                    if kind != a["ptr"]:
                        found.append([*base[:i], {"ptr": kind}, *base[i + 1 :]])
        # The value given back through one pointer of each kind, after the other arguments.
        if not any(is_ptr(a) for a in base):
            for kind in pointers:
                found.append([*base, {"ptr": kind}])
                if kind == "char":
                    found.append([*base, {"ptr": kind}, 256])
            # A name or an index in front, for a device NIS asks to be named.
            for first in ("", 0):
                for kind in pointers:
                    found.append([first, *base, {"ptr": kind}])
    unique = []
    for shape in found:
        if shape != list(args) and shape not in unique:
            unique.append(shape)
    return unique[:SHAPES_TRIED]


def argument_shapes(check: Check) -> None:
    """For each reading NIS refused the arguments of, find the arguments it takes.

    Only readings are tried, so nothing on the microscope changes. A shape that
    NIS accepts may still answer "not available" while the hardware is off; it
    is still the right shape, because NIS checked the arguments first.
    """
    refused = []
    for entry in check.report["checks"]:
        name = entry["check"]
        if (
            name.startswith("read ")
            and entry["outcome"] == "FAIL"
            and any(words in str(entry["answer"]) for words in REFUSED_ARGUMENTS)
        ):
            refused.append(name[len("read ") :])
    first_args = dict(DRIVER_READS)
    devices = check.devices
    tokens = {devices.NAME: "", devices.INDEX: 0, devices.LASER: 0, devices.LINE: 0}
    for row in devices.TABLE:
        for call in (row.present, row.count, row.name, row.positions, row.read):
            if call is not None and call.fn not in first_args:
                first_args[call.fn] = devices.arguments(call.args, tokens)
    first_args.setdefault("Stg_GetMultiLaserNumLines", [0])

    def probe(fn):
        tried = []
        for shape in shapes(list(first_args.get(fn, []))):
            try:
                answer = check.call(fn, shape)
            except Exception as error:  # noqa: BLE001 -- each refusal is part of the answer
                tried.append({"arguments": shape, "refused": f"{error}"[:200]})
                continue
            return {"takes": shape, "answer": answer, "tried before": tried}
        raise RuntimeError(f"no shape tried was accepted: {tried}")

    for fn in refused:
        check.check(f"arguments {fn} takes", lambda fn=fn: probe(fn))


def smallest_change(row, devices, before, positions):
    """The smallest value that differs visibly from *before*, or *before* itself where it must not change."""
    if not CHANGES or row.guarded or row.key in OWN_VALUE_ONLY or not row.confirms:
        return before
    if row.kind == devices.SWITCH:
        if row.key == "pfs" and not SWITCH_PFS:
            return before
        return 1 - int(before)
    if row.kind == devices.CHOICE:
        if not positions or positions < 2:
            return before
        return before + 1 if before < positions else before - 1
    step = max(row.tolerance * NUDGE_TOLERANCES, 1e-3)
    return before - step if before >= step else before + step


def neutral(row, devices):
    """What a change is sent when its value cannot be read (the hardware off): nothing it could act on while off."""
    return 1 if row.kind == devices.CHOICE else 0


def exercise_setting(check: Check, row, instance, positions) -> dict:
    """Read one device, change it the smallest visible amount, read it back, put it back, read it again."""
    devices = check.devices
    tokens = (
        devices.tokens_for(row, instance)
        if instance is not None or row.devices == "one"
        else {devices.NAME: "", devices.LASER: 0, devices.LINE: 0}
    )
    read_args = devices.arguments(row.read.args, tokens)
    first = answered(check, row.read.fn, read_args)
    try:
        before = devices.as_value(
            row,
            devices.answer_of(
                row.read, {"return": first["returned"], "values": first["values"]}
            ),
        )
    except Exception:  # noqa: BLE001 -- not available: the baseline of the hardware off
        before = None
    if before is None:
        if not CHANGES or row.guarded:
            return {
                "read": first,
                "change": "not sent: the value cannot be read"
                + (" and the setting is guarded" if row.guarded else ""),
            }
        sent = answered(
            check,
            row.write.fn,
            devices.arguments(
                row.write.args, tokens, devices.sent_value(row, neutral(row, devices))
            ),
        )
        return {"read": first, "sent once (the value cannot be read)": sent}
    target = smallest_change(row, devices, before, positions)
    read_back = lambda: devices.as_value(
        row, devices.answer_of(row.read, check.call(row.read.fn, read_args))
    )
    try:
        sent = answered(
            check,
            row.write.fn,
            devices.arguments(row.write.args, tokens, devices.sent_value(row, target)),
        )
        time.sleep(SETTLE_S)
        after = read_back()
    finally:
        # Whatever happened above, the device is put back as it was.
        back = answered(
            check,
            row.write.fn,
            devices.arguments(row.write.args, tokens, devices.sent_value(row, before)),
        )
    time.sleep(SETTLE_S)
    again = read_back()
    found = {
        "before": before,
        "set to": target,
        "sent": sent["returned"],
        "read after": after,
        "put back": back["returned"],
        "read at the end": again,
    }
    if row.confirms:
        if target != before and not devices.matches(row, target, after):
            raise RuntimeError(f"the change was not seen: {found}")
        if not devices.matches(row, before, again):
            raise RuntimeError(f"it is not back as it was: {found}")
    return found


def exercise_settings(check: Check, inventory: dict) -> None:
    """Every hardware setting of the table: each device NIS reports, or, with none reported, the call once."""
    for row in check.devices.TABLE:
        entry = inventory.get(row.key) or {}
        items = entry.get("devices") or [
            {"instance": None, "positions": None, "absent": True}
        ]
        for item in items:
            name = row.key + (f"/{item['instance']}" if item.get("instance") else "")
            if item.get("absent"):
                name += " (none reported)"
            check.check(
                f"change {name}",
                lambda row=row, item=item: exercise_setting(
                    check,
                    row,
                    item.get("instance") if not item.get("absent") else None,
                    item.get("positions"),
                ),
                changes=CHANGES,
            )


def stage(check: Check) -> None:
    """x by the smallest step the read-back sees and back, then the focus the same way."""
    xy = answered(check, "StgGetPosXY", [{"ptr": "double"}, {"ptr": "double"}])
    z = answered(check, "StgGetPosZ", [{"ptr": "double"}, 0])

    def move_x():
        x, y = xy["values"]
        if not available(xy):
            return {
                "read": xy,
                "sent once (the stage is not available)": answered(
                    check, "StgMoveXY", [x, y, 0]
                ),
            }
        target = x + (STAGE_STEP_UM if CHANGES else 0.0)
        started = time.monotonic()
        try:
            sent = answered(check, "StgMoveXY", [target, y, 0])
            returned_after = round(time.monotonic() - started, 3)
            there = answered(
                check, "StgGetPosXY", [{"ptr": "double"}, {"ptr": "double"}]
            )["values"]
        finally:
            back = answered(check, "StgMoveXY", [x, y, 0])
        again = answered(check, "StgGetPosXY", [{"ptr": "double"}, {"ptr": "double"}])[
            "values"
        ]
        return {
            "from": [x, y],
            "to": [target, y],
            "sent": sent["returned"],
            "returned after s": returned_after,
            "read there": there,
            "put back": back["returned"],
            "read at the end": again,
        }

    def move_z():
        z0 = z["values"][0]
        if not available(z):
            return {
                "read": z,
                "sent once (the focus is not available)": answered(
                    check, "StgMoveZ", [z0, 0]
                ),
            }
        target = z0 + (Z_STEP_UM if CHANGES else 0.0)
        started = time.monotonic()
        try:
            sent = answered(check, "StgMoveZ", [target, 0])
            returned_after = round(time.monotonic() - started, 3)
            there = answered(check, "StgGetPosZ", [{"ptr": "double"}, 0])["values"][0]
        finally:
            back = answered(check, "StgMoveZ", [z0, 0])
        again = answered(check, "StgGetPosZ", [{"ptr": "double"}, 0])["values"][0]
        return {
            "from": z0,
            "to": target,
            "sent": sent["returned"],
            "returned after s": returned_after,
            "read there": there,
            "put back": back["returned"],
            "read at the end": again,
        }

    check.check(f"stage x {STAGE_STEP_UM:g} um and back", move_x, changes=CHANGES)
    check.check(f"focus {Z_STEP_UM:g} um and back", move_z, changes=CHANGES)


def objective(check: Check) -> None:
    """The nosepiece: set to its own position (or, with TURN_NOSEPIECE, the next one and back)."""

    def turn():
        now = answered(check, "Stg_GetNosepiecePosition", [])
        before = now["returned"]
        if not (isinstance(before, int) and before > 0):
            return {
                "read": now,
                "sent once (no position to read)": answered(
                    check, "Stg_SetNosepiecePosition", [1]
                ),
            }
        positions = answered(check, "Stg_GetNosepiecePositions", [])["returned"]
        target = (
            (before % positions + 1)
            if (CHANGES and TURN_NOSEPIECE and positions)
            else before
        )
        try:
            sent = answered(check, "Stg_SetNosepiecePosition", [target])
            there = answered(check, "Stg_GetNosepiecePosition", [])["returned"]
        finally:
            back = answered(check, "Stg_SetNosepiecePosition", [before])
        return {
            "from": before,
            "to": target,
            "sent": sent["returned"],
            "read there": there,
            "put back": back["returned"],
        }

    check.check(
        "nosepiece"
        + (" one position and back" if TURN_NOSEPIECE else " set to its own position"),
        turn,
        changes=CHANGES,
    )


def optical_configuration(check: Check) -> None:
    """The optical configuration in use, selected again: no change, but the call is tried."""

    def again():
        texts = answered(
            check,
            "GetSelectedOptConf",
            [{"ptr": "char"}, {"ptr": "char"}, {"ptr": "char"}, 256],
        )["values"][:3]
        count = answered(check, "GetOptConfCount", [])["returned"] or 0
        names = [
            answered(check, "GetOptConfName", [i, {"ptr": "char"}, 256])["values"][1]
            for i in range(max(int(count), 0))
        ]
        chosen = next((t for t in texts if t in names), None)
        if chosen is None:
            return {
                "selected texts": texts,
                "configurations": names,
                "change": "not sent: none is selected, and selecting one would change NIS",
            }
        return {
            "selected": chosen,
            "sent": answered(check, "SelectOptConf", [chosen])["returned"],
        }

    check.check("optical configuration selected again", again, changes=CHANGES)


def _or_error(how):
    try:
        return how()
    except Exception as error:  # noqa: BLE001 -- kept as the answer
        return f"{type(error).__name__}: {error}"


def snap(check: Check) -> None:
    """One picture into a new document of its own, saved as a TIFF, and only that document closed."""

    def picture():
        bridge = check.server.Bridge(check.nis, "check")
        path = os.path.join(
            os.path.abspath(check.out_dir), f"snap_{time.strftime('%H%M%S')}.tif"
        )
        # The list of open documents is only a courtesy here: if NIS refuses it, the picture is still taken.
        documents = lambda: _or_error(bridge.documents)
        documents_before = documents()
        try:
            answer = bridge.snap(path)
        except check.server.Refused as refused:
            if "answered -" in str(refused):
                return {
                    "documents before": documents_before,
                    "capture": f"not taken: {refused}",
                }
            raise
        return {
            **answer,
            "documents before": documents_before,
            "documents after": documents(),
        }

    check.check(
        "one picture, saved as a TIFF, its document closed", picture, changes=True
    )


# -- part 3: the driver itself, through its bridge, inside NIS -----------------------------

#: The repository's root: the folder that holds ``zmart_drivers``.
REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(HERE))))
)
#: How NIS says a device is not there or switched off, as the driver's errors carry it.
NOT_THERE = (
    "DR_NOTAVAILABLE",
    "answered -4",
    "answered -9",
    "not available",
    "could not be read",
)


def not_there(error: BaseException) -> bool:
    return any(words in str(error) for words in NOT_THERE)


def baseline(how):
    """Run *how*; NIS saying a device is not there is an answer (the hardware off), any other error a failure."""

    def run():
        try:
            return how()
        except Exception as error:
            if not_there(error):
                return {
                    "not available (the hardware is off or absent)": f"{type(error).__name__}: {error}"
                }
            raise

    return run


def driver_checks(check: Check, package, port: int, data: str) -> None:
    """The driver's own calls, as a user or the ZMART Controller makes them, each with the smallest change and back."""
    devices = check.devices
    scope = check.check(
        "driver: connect()", lambda: package.connect(port=port, output_root=data)
    )
    if scope is None:
        return
    try:
        check.check("driver: state()", lambda: scope.state())
        here = check.check("driver: position()", baseline(lambda: scope.position()))

        def move():
            x, y, z = (here[a]["position"] for a in ("x", "y", "z"))
            step = STAGE_STEP_UM if CHANGES else 0.0
            try:
                there = scope.move_to(x + step, y, z)
            finally:
                back = scope.move_to(x, y, z)
            return {
                "from": [x, y, z],
                "there": there["x"]["position"],
                "back": back["x"]["position"],
            }

        if isinstance(here, dict) and "x" in here:
            check.check(
                f"driver: move_to() x {STAGE_STEP_UM:g} um and back",
                move,
                changes=CHANGES,
            )
        else:

            def move_while_off():
                # x and y cannot be read, so the move goes to (0, 0) at the focus where it stands:
                # with the stage off, NIS cannot act on it, and the driver's answer is the baseline.
                from zmart_drivers.nikon.nis_elements_ar7.procedures import readings

                z_now = readings.z(scope.client)["z_um"] - scope.handle.origin["z_um"]
                return scope.move_to(0.0, 0.0, z_now)

            check.check(
                "driver: move_to() while the stage is not available",
                baseline(move_while_off),
            )
        found = check.check("driver: devices()", lambda: scope.devices()) or {}
        tried = 0
        for key, entry in found.items():
            row = devices.SETTINGS.get(key)
            for item in entry.get("devices") or []:
                name = key + (f"/{item['instance']}" if item["instance"] else "")
                tried += 1

                def set_and_back(row=row, item=item, name=name):
                    before = scope.device(name)
                    target = smallest_change(
                        row, devices, before, item.get("positions")
                    )
                    if target == before:
                        return {
                            "before": before,
                            "change": "not sent: it holds this value (guarded, or kept as it is)",
                        }
                    try:
                        scope.set_device(name, target)
                        after = scope.device(name)
                    finally:
                        scope.set_device(name, before)
                    again = scope.device(name)
                    if not devices.matches(row, target, after) or not devices.matches(
                        row, before, again
                    ):
                        raise RuntimeError(
                            f"before {before}, set {target}, read {after}, back {again}"
                        )
                    return {
                        "before": before,
                        "set to": target,
                        "read after": after,
                        "read at the end": again,
                    }

                check.check(
                    f"driver: set_device({name!r}) and back",
                    baseline(set_and_back),
                    changes=CHANGES,
                )
        if not tried:

            def refused():
                try:
                    scope.set_device("light_path", 1)
                except ValueError as why:
                    return {"refused before anything was sent": str(why)}
                raise RuntimeError("a device NIS does not report was not refused")

            check.check(
                "driver: set_device() of a device NIS does not report is refused",
                refused,
            )
        check.check("driver: objectives()", baseline(lambda: scope.objectives()))
        check.check(
            "driver: optical_configurations()",
            baseline(lambda: scope.optical_configurations()),
        )
        check.check(
            "driver: set_state(state()['changeable']) sends nothing that is unchanged",
            baseline(lambda: scope.set_state(scope.state()["changeable"])),
        )
        if CHANGES and SNAP:

            def picture():
                try:
                    return scope.acquire(
                        "zmart_check", acquisition_type="check", output_folder=data
                    )
                except RuntimeError as why:
                    if "No module named" in str(why):
                        return {
                            "skipped": True,
                            "why": f"the picture was taken, but saving it as OME-Zarr needs packages this Python lacks: {why}",
                        }
                    raise

            check.check(
                "driver: acquire(), saved as OME-Zarr", baseline(picture), changes=True
            )
    finally:
        check.check("driver: close()", lambda: scope.close() or "closed")
    plugin_checks(check, port)


def plugin_checks(check: Check, port: int) -> None:
    """The ZMART Controller's commands, through the driver's plugin class (the controller itself is not needed)."""
    from zmart_drivers.nikon.nis_elements_ar7.zmart_controller_plugin.zmart_controller_plugin import (
        ZmartControllerPlugin,
    )

    plugin = check.check(
        "controller plugin: connect", lambda: ZmartControllerPlugin({"port": port})
    )
    if plugin is None:
        return
    try:
        for command in (
            "get_info",
            "get_actuators",
            "get_xyz",
            "get_state",
            "get_acquisition_settings",
            "get_procedures",
        ):
            check.check(
                f"controller plugin: {command}()",
                baseline(lambda command=command: getattr(plugin, command)()[1]),
            )
        state = check.check(
            "controller plugin: state for set_state",
            baseline(lambda: plugin.get_state()[1][0]),
        )
        if isinstance(state, dict) and "objective_position" in state:
            check.check(
                "controller plugin: set_state(changeable) sends nothing that is unchanged",
                baseline(lambda: plugin.set_state(state)[1]),
            )
    finally:
        check.check(
            "controller plugin: disconnect()",
            lambda: plugin.disconnect() or "disconnected",
        )


def driver_part(check: Check) -> None:
    """Run the driver itself inside NIS: its bridge on NIS's thread, the driver's calls on another, as in use.

    The driver keeps its settings in a scratch folder inside the results, so
    the microscope's real setup (limits, origin, orientation) is never read
    or changed by the check.
    """
    import secrets
    import threading

    os.environ["ZMART_MICROSCOPY_ROOT"] = os.path.join(
        os.path.abspath(check.out_dir), "driver_settings"
    )
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    try:
        import zmart_drivers.nikon.nis_elements_ar7 as package
        from zmart_drivers.nikon.nis_elements_ar7.driver_setup import store
        from zmart_drivers.nikon.nis_elements_ar7.driver_setup.nis_bridge import macro
        from zmart_drivers.nikon.nis_elements_ar7.primitives.nis_python import (
            server as bridge_server,
        )

        package.connect  # noqa: B018 -- loads the driver's methods, and with them numpy
    except Exception as error:  # noqa: BLE001 -- said in the summary
        why = f"{type(error).__name__}: {error}"

        def cannot_load():
            raise RuntimeError(f"the driver cannot be loaded here: {why}")

        check.check("driver: loads in NIS's Python", cannot_load)
        return
    store.MACHINE = store.MachineProfile()
    store.use_configuration(None)
    key = secrets.token_hex(16)
    key_file = macro.files()["key"]
    os.makedirs(os.path.dirname(key_file), exist_ok=True)
    with open(key_file, "w", encoding="ascii") as handle:
        handle.write(key)
    bridge = bridge_server.serve(check.nis, key, port=0)
    port = bridge.server.server_address[1]
    data = os.path.join(os.path.abspath(check.out_dir), "driver_data")
    os.makedirs(data, exist_ok=True)
    done = threading.Event()

    def work():
        try:
            driver_checks(check, package, port, data)
        finally:
            done.set()

    threading.Thread(target=work, name="zmart-check-driver", daemon=True).start()
    deadline = time.monotonic() + DRIVER_TIME_LIMIT_S
    # This thread is NIS's own, as the bridge's macro loop is: it runs the driver's requests.
    while not done.is_set() and time.monotonic() < deadline:
        bridge.pump(wait_s=0.01)
    bridge.close()
    if not done.is_set():
        check.check(
            "driver: finished in time",
            lambda: (_ for _ in ()).throw(
                RuntimeError(f"still running after {DRIVER_TIME_LIMIT_S} s")
            ),
        )


def main(nis_module=None, out_dir: str | None = None) -> dict:
    if nis_module is None:
        import nis as nis_module  # exists only inside NIS-Elements
    out_dir = out_dir or os.path.join(RESULTS_ROOT, time.strftime("%Y%m%d_%H%M%S"))
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
    check.check(
        "the stage, the focus, the objectives, the optical configurations",
        lambda: stage_and_optics(check),
    )
    reads(check)
    argument_shapes(check)
    exercise_settings(check, inventory)
    stage(check)
    objective(check)
    optical_configuration(check)
    if CHANGES and SNAP:
        snap(check)
    if DRIVER:
        driver_part(check)
    report = check.finish()
    try:
        nis_module.log(f"ZMART hardware check finished: {out_dir}")
    except Exception:  # noqa: BLE001, S110 -- the log line is only a courtesy
        pass
    return report


if __name__ == "__main__":
    main()
