"""At the microscope: the setup wizard walked through its steps, without touching this microscope's settings.

The wizard (``driver_setup/wizard/``) is the window an operator sets the
microscope up with. This check runs it the way its page does: it starts the
wizard's own server and makes the page's calls over it, one step after the
other, against the LAS X that is running (the simulator or the STELLARIS).

**This microscope's settings are never changed.** Everything the wizard saves
(limits, origin, orientation) would otherwise go to this computer's settings
folder, ``C:\\ProgramData\\zmart-microscopy\\...``, and become what every later
session uses. So the check first copies that folder to a throwaway one and
points the driver there; the wizard saves into the copy. Last, it checks that
the real folder is exactly as it was.

What it checks, in order:

1. The page and its files are served.
2. Connect, with a throwaway data folder: the checks name the stage, the
   objective and the copied settings folder.
3. Define limits: the limits are read, one corner is read from the stage and
   matches the stage's own reading, and the same limits are saved again
   unchanged, as a new snapshot in the copy.
4. Define coordinate system origin: Read, then Save and adopt, into the copy.
5. Image-to-stage calibration: Start moves the stage a little along X and Y
   (40 um, within the limits, and back) and takes three pictures. Whether the
   orientation is accepted depends on the field under the objective, so an
   explained refusal passes too; when it is accepted, the check picture is
   taken and the orientation saved, into the copy.
6. Disconnect.
7. This microscope's settings folder is untouched.

The objective calibration is not checked here: it needs a person to change
the objective between measurements. The report says so.

The report is written as ``check_setup_wizard.json``; the last line says
``ALL CHECKS PASSED`` or which check failed.

Usage, in the driver's environment on the microscope PC::

    python check_setup_wizard.py --report-dir D:/ZMART-check
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

from zmart_drivers.leica.stellaris.navigator_expert.driver_setup import store
from zmart_drivers.leica.stellaris.navigator_expert.driver_setup.wizard.server import (
    WizardServer,
)
from zmart_drivers.leica.stellaris.navigator_expert.driver_setup.wizard.steps import (
    leica_wizard,
)

#: How far a corner read through the wizard may differ from the stage's own reading (um).
CORNER_TOLERANCE_UM = 1.0

#: What the report says about the step this check cannot do by itself.
NOT_CHECKED = {
    "Objective calibration": (
        "not checked: it needs a person to change the objective between "
        "measurements; walk it in the wizard by hand"
    )
}


def fingerprint(folder: Path) -> dict[str, tuple[int, int]]:
    """Every file under *folder*, with its size and modification time: enough to see any change."""
    if not folder.exists():
        return {}
    return {
        str(path.relative_to(folder)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(folder.rglob("*"))
        if path.is_file()
    }


def throwaway_settings(workspace: Path) -> tuple[Path, Path]:
    """Copy this microscope's settings folder into *workspace*, and point the driver at the copy.

    Answers (the real folder, the copy). From here on everything the driver
    and the wizard save goes to the copy.
    """
    real = store.MachineProfile().snapshot_root()
    root = workspace / "settings"
    copy = root / real.relative_to(store.MachineProfile().root())
    if real.exists():
        shutil.copytree(real, copy)
    else:
        copy.mkdir(parents=True)
    os.environ[store.PROGRAMDATA_ROOT_ENV] = str(root)
    return real, copy


class Page:
    """The wizard's page, as far as this check needs it: its address, and its calls."""

    def __init__(self, server: WizardServer):
        self.url = server.url

    def get(self, path: str = "") -> bytes:
        with urllib.request.urlopen(self.url + path, timeout=30) as reply:
            return reply.read()

    def call(self, step: str, action: str, values: dict | None = None) -> dict:
        """One action, as the page asks for it. Raises with the wizard's own sentence when it refuses."""
        request = urllib.request.Request(
            f"{self.url}api/{step}/{action}",
            json.dumps(values or {}).encode(),
            {"Content-Type": "application/json"},
        )
        # A measurement takes pictures; give it the time a person would.
        with urllib.request.urlopen(request, timeout=600) as reply:
            answer = json.loads(reply.read())
        if not answer["ok"]:
            raise RuntimeError(answer["error"])
        return answer["result"]


def run_checks(workspace: Path) -> dict:
    """Every check, by name: True when it passed, or a sentence saying what happened instead."""
    checks: dict = {}
    real, copy = throwaway_settings(workspace)
    before = fingerprint(real)
    data_folder = workspace / "data"
    data_folder.mkdir(parents=True, exist_ok=True)

    def check(name, test):
        try:
            result = test()
        except Exception as why:  # noqa: BLE001 -- reported, not raised
            result = f"NO: {type(why).__name__}: {why}"
        checks[name] = result
        return result is True

    server = WizardServer(leica_wizard()).start()
    page = Page(server)
    try:

        def page_is_served():
            html = page.get().decode()
            if 'id="steps"' not in html:
                return "NO: the page came back without its rail of steps"
            assets = [
                part.split('"')[0]
                for part in html.split('src="./')[1:] + html.split('href="./')[1:]
            ]
            missing = [a for a in assets if not page.get(a)]
            return (
                True
                if assets and not missing
                else f"NO: missing {missing or 'every file'}"
            )

        def connects():
            found = dict(
                page.call("connect", "connect", {"output_root": str(data_folder)})[
                    "checks"
                ]
            )
            if Path(found.get("settings folder", "")) != copy:
                return f"NO: the settings folder is {found.get('settings folder')!r}, not the copy"
            if not found.get("stage") or not found.get("objective"):
                return f"NO: Connect named no stage or objective: {found}"
            return True

        def limits_are_read_and_saved_again():
            doc = page.call("limits", "document")
            motors = {
                "x_motor": doc["actuators"]["x"][0],
                "y_motor": doc["actuators"]["y"][0],
            }
            corner = page.call("limits", "corner", motors)
            # The origin step's Read is the wizard's other way of reading the stage; both must agree.
            here = page.call("origin", "read")
            off = max(abs(corner["x"] - here["x_um"]), abs(corner["y"] - here["y_um"]))
            if off > CORNER_TOLERANCE_UM:
                return f"NO: the corner read {corner}, the stage {here}"
            before_save = store.MACHINE.latest_snapshot("limits")
            page.call(
                "limits",
                "publish",
                {"document": doc["document"], "corners": {"check": corner}},
            )
            after_save = store.MACHINE.latest_snapshot("limits")
            if after_save == before_save or copy not in after_save.parents:
                return f"NO: no new limits snapshot in the copy ({after_save})"
            saved = json.loads((after_save / "limits.json").read_text(encoding="utf-8"))
            return (
                True
                if saved == doc["document"]
                else "NO: the saved limits differ from the ones read"
            )

        def origin_is_read_and_saved():
            read = page.call("origin", "read")
            saved = page.call("origin", "publish")
            path = Path(saved["saved_to"])
            if copy not in path.parents or not path.is_file():
                return f"NO: the origin was saved to {path}, not into the copy"
            standing = page.call("origin", "standing")["origin"]
            if abs(standing["x_um"] - read["x_um"]) > CORNER_TOLERANCE_UM:
                return f"NO: saved x {standing['x_um']}, read {read['x_um']}"
            return True

        def orientation_is_measured():
            measured = page.call("orientation", "measure", {"stage_move_um": 40})
            if not measured["accepted"]:
                # The field decides this; the wizard explained it, which is what is checked.
                checks["orientation: the field under the objective"] = (
                    f"not accepted, and said why: {measured.get('why')}"
                )
                return True if measured.get("why") else "NO: refused without saying why"
            if not measured.get("image"):
                return "NO: accepted, but no sheet of candidates"
            page.call("orientation", "check")
            saved = page.call("orientation", "publish")
            return (
                True
                if copy in Path(saved["saved_to"]).parents
                else "NO: saved outside the copy"
            )

        if check("the page and its files are served", page_is_served) and check(
            "Connect: LAS X reached, the checks name the stage, the objective and the copied settings",
            connects,
        ):
            check(
                "Define limits: read, a corner read from the stage, saved again unchanged",
                limits_are_read_and_saved_again,
            )
            check(
                "Define coordinate system origin: Read, then Save and adopt",
                origin_is_read_and_saved,
            )
            check(
                "Image-to-stage calibration: Start ran and answered (saved when accepted)",
                orientation_is_measured,
            )
            check(
                "Disconnect",
                lambda: page.call("connect", "disconnect")["connected"] is False,
            )
    finally:
        server.stop()
    checks.update(NOT_CHECKED)
    after = fingerprint(real)
    checks["this microscope's settings folder is untouched"] = (
        True
        if after == before
        else f"NO: changed: {sorted(set(after.items()) ^ set(before.items()))[:10]}"
    )
    return checks


def failed(checks: dict) -> list[str]:
    """The checks that failed: those answering "NO: ..." (a note or a skipped step is not a failure)."""
    return [
        name
        for name, result in checks.items()
        if isinstance(result, str) and result.startswith("NO:")
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--report-dir", default=".", help="where the report is written (default: here)"
    )
    asked = parser.parse_args()
    workspace = Path(tempfile.mkdtemp(prefix="zmart_wizard_check_"))
    try:
        checks = run_checks(workspace)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
    wrong = failed(checks)
    report = {
        "checks": checks,
        "verdict": "ALL CHECKS PASSED" if not wrong else f"FAILED: {wrong}",
    }
    out = Path(asked.report_dir) / "check_setup_wizard.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\n{report['verdict']}  (report: {out})")
    return 0 if not wrong else 1


if __name__ == "__main__":
    sys.exit(main())
