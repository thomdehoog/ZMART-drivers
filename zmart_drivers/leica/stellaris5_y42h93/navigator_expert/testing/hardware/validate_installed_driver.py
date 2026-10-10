"""Drive the Leica driver exactly as a workflow does: installed by folder, connected by name.

The other validators hand the controller the driver's module. This one takes
the path an experiment takes after the driver is installed on the microscope
computer: it registers the driver's folder (``zmart_driver.json`` and the
``ZmartDriver`` class in ``zmart_driver.py``), connects by name, and then
asks every command the controller offers, the way ``mic.connect("stellaris")``
followed by ``mic.get_xyz()`` would.

It does not know whether LAS X is in simulator mode or driving real optics;
that is LAS X's own switch. Run it against the simulator first, then the
same command on the microscope.

What it checks, in order:

1. **Installed by folder.** ``register_driver`` accepts the folder and lists
   the driver as ``stellaris``. On the microscope computer this writes the
   path of this checkout into the controller's list of drivers, which is
   what an operator wants: the code under test is the code that runs.
2. **Connected by name**, with the controller's own ``validate_driver``
   checking every ``get_*`` answer against its contract.
3. **Every command answers in the controller's shape**: ``get_info``,
   ``get_actuators``, ``get_xyz``, ``get_state``, ``get_acquisition_settings``,
   ``get_procedures``, and ``set_state`` with the settings already in place.
4. **The limits gate refuses** a move far outside the travel, before
   anything moves: the answer is a failure, and the stage has not moved.
5. **A small move and back** (``--allow-move``), read back within tolerance.
6. **One acquisition** (``--allow-acquire``), checked with the controller's
   ``check_acquire_answer`` and by opening the files it names.
7. **Everything restored**: the stage where it was, the settings as they were.

The run report ends with the acceptance checklist, one line per point above.
Safe by default: moves and acquisitions are opt-in, every change is restored
in a ``finally``, and nothing touches the objective turret.

Usage:
  python validate_installed_driver.py --mock --allow-move              # offline, the LAS X mock
  python validate_installed_driver.py --yes --allow-move --allow-acquire   # simulator or scope
"""


from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve()
_NAV_ROOT = _HERE.parents[2]  # navigator_expert/
_REPO_ROOT = _HERE.parents[6]  # the repository root, which holds zmart_drivers/
_HELPERS = _NAV_ROOT / "testing" / "helpers"
for _p in (str(_HERE.parent), str(_REPO_ROOT), str(_HELPERS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import validate_hardware as vh
import zmart_controller
from validate_zmart_adapter import _ReportingSession
from zmart_controller.registry import registry_file

#: The driver's name in zmart_driver.json, and what get_instruments lists it under.
DRIVER_NAME = "stellaris"
#: The folder register_driver is pointed at: it holds zmart_driver.json.
DRIVER_FOLDER = _NAV_ROOT
#: Readback tolerance for a move, matching the driver's own confirmation gate.
XY_TOL_UM = 20.0
#: A step far beyond any stage, so the gate refuses it whatever the limits are.
FAR_UM = 1.0e7


# --- setup ------------------------------------------------------------------


def _adapter() -> Any:
    from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.zmart_adapter import (
        zmart_adapter as adapter,
    )

    return adapter


def _go_offline(args: argparse.Namespace) -> None:
    """Stand the LAS X mock in for the CAM API, under a throwaway machine root.

    The throwaway root also receives the controller's list of drivers, so
    registering here never touches this computer's real list.
    """
    from dataclasses import replace

    from limits_fixtures import hermetic_mock_machine_root
    from mock_lasx_api import MockLasxClient

    from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.actions import profiles

    hermetic_mock_machine_root()
    _adapter()._session.connect_python_client = lambda **_kw: MockLasxClient(
        latency=args.mock_latency
    )
    # The mock has no LAS X log stream, so a job selection can only be
    # confirmed through the API.
    profiles.STATE_READERS = replace(
        profiles.STATE_READERS, selected_job_confirm_source="api"
    )


def _connection(args: argparse.Namespace, output_root: str | None) -> dict[str, Any]:
    """The connection the session uses, on top of what zmart_driver.json saved."""
    connection: dict[str, Any] = {
        "client": args.client_name,
        "api_delay_ms": args.api_delay_ms,
    }
    if output_root is not None:
        connection["output_root"] = output_root
    return connection


# --- phases -----------------------------------------------------------------


def phase_install(v: vh.Validator) -> bool:
    with v.phase("install: register the folder"):
        name = v.callable(
            "install: register_driver(folder)",
            lambda: zmart_controller.register_driver(DRIVER_FOLDER),
            context={"folder": str(DRIVER_FOLDER), "registry": str(registry_file())},
        )
        if name is None:
            return False
        ok = v.compare("install: the driver is listed as stellaris", name, DRIVER_NAME)
        listed = zmart_controller.get_instruments()
        ok &= v.compare(
            "install: get_instruments lists it", DRIVER_NAME in listed, True
        )
        ok &= v.compare(
            "install: the listing carries no error",
            "error" not in listed.get(DRIVER_NAME, {}),
            True,
        )
    return ok


def phase_contract(v: vh.Validator, connection: dict[str, Any]) -> None:
    with v.phase("contract: validate_driver by name"):
        problems = v.callable(
            "contract: validate_driver connects, reads, disconnects",
            lambda: zmart_controller.validate_driver(DRIVER_NAME, connection),
            context={
                "connection": {
                    k: val for k, val in connection.items() if k != "password"
                }
            },
        )
        if problems is not None:
            v.compare("contract: every get_* answer fits", problems, [])


def phase_commands(v: vh.Validator, sess: Any) -> dict[str, Any]:
    """Ask every read command, and set_state with the settings already in place."""
    seen: dict[str, Any] = {}
    with v.phase("commands: every read command through the controller"):
        info = v.callable("get_info", sess.get_info)
        if info is not None:
            description = info.get("description") or ""
            v.compare(
                "get_info: a description in plain words",
                bool(description.strip()),
                True,
            )
            v.compare(
                "get_info: it names the microscope", "STELLARIS" in description, True
            )

        actuators = v.callable("get_actuators", sess.get_actuators)
        if actuators is not None:
            v.compare(
                "get_actuators: the three axes", sorted(actuators), ["x", "y", "z"]
            )
            v.compare(
                "get_actuators: z has its two drives",
                actuators.get("z"),
                ["z-wide", "z-galvo"],
            )

        xyz = v.callable("get_xyz", sess.get_xyz)
        if xyz is not None:
            seen["xyz"] = xyz
            for axis in ("x", "y", "z"):
                v.compare(
                    f"get_xyz: {axis} has position, unit, actuators and canvas",
                    sorted(xyz.get(axis, {})),
                    ["actuators", "canvas", "position", "unit"],
                )
            v.compare(
                "get_xyz: in micrometres", xyz.get("x", {}).get("unit"), "micrometer"
            )
            canvas = xyz.get("x", {}).get("canvas") or [0, 0]
            v.compare("get_xyz: the canvas has width", canvas[1] > canvas[0], True)

        state = v.callable("get_state", sess.get_state)
        if state is not None:
            seen["state"] = state
            v.compare(
                "get_state: changeable and observed",
                sorted(state),
                ["changeable", "observed"],
            )
            observed = state.get("observed") or {}
            v.compare(
                "get_state: a limits file governs this session",
                bool(observed.get("limits")),
                True,
            )
            v.compare(
                "get_state: the setup readiness is reported",
                "setup" in observed,
                True,
            )

        settings = v.callable("get_acquisition_settings", sess.get_acquisition_settings)
        if settings is not None:
            shaped = all(
                isinstance(spec, dict) and {"options", "active"} <= spec.keys()
                for spec in settings.values()
            )
            v.compare(
                "get_acquisition_settings: options and active per setting", shaped, True
            )

        procedures = v.callable("get_procedures", sess.get_procedures)
        if procedures is not None:
            described = all("description" in spec for spec in procedures.values())
            v.compare("get_procedures: every routine is described", described, True)

        if state is not None:
            changeable = dict(state.get("changeable") or {})
            applied = v.callable(
                "set_state: the settings already in place",
                lambda: sess.set_state({"changeable": changeable}),
                context={"changeable": changeable},
                mutating=True,
            )
            if applied is not None:
                # The settings were already in place, so nothing needed
                # changing; what matters is that the answer names what was
                # applied, as a dictionary, and that nothing was refused.
                v.compare(
                    "set_state: answers what was applied",
                    isinstance(applied.get("applied"), dict),
                    True,
                )
    return seen


def _position(xyz: dict[str, Any]) -> tuple[float, float, float]:
    return tuple(float(xyz[axis]["position"]) for axis in ("x", "y", "z"))


def phase_limits(v: vh.Validator, sess: Any, before: dict[str, Any] | None) -> None:
    """A move far outside the travel is refused before anything moves."""
    with v.phase("limits: the gate refuses before anything moves"):
        if before is None:
            v.skip("limits: refusal", "no position read")
            return
        x, y, z = _position(before)
        raw = sess._session.set_xyz(x + FAR_UM, y, z)  # the raw answer, not unwrapped
        refused = isinstance(raw, dict) and raw.get("success") is False
        v.compare("limits: a far move is answered as a failure", refused, True)
        text = str(raw.get("content", "")) if isinstance(raw, dict) else ""
        v.compare(
            "limits: the answer says the move was refused",
            any(
                word in text.lower()
                for word in ("limit", "refus", "outside", "valueerror")
            ),
            True,
        )
        after = v.callable("limits: get_xyz afterwards", sess.get_xyz)
        if after is not None:
            v.compare(
                "limits: the stage did not move",
                _position(after)[0],
                x,
                tolerance=XY_TOL_UM,
            )


def phase_move(
    v: vh.Validator, sess: Any, before: dict[str, Any] | None, delta_um: float
) -> None:
    """A small move and back, read back within tolerance; the start is restored in finally."""
    if before is None:
        v.skip("phase: move", "no position read")
        return
    x, y, z = _position(before)
    with v.phase("move: a small step and back"):
        try:
            moved = v.callable(
                "set_xyz: a small step",
                lambda: sess.set_xyz(x + delta_um, y + delta_um, z),
                context={"target": [x + delta_um, y + delta_um, z]},
                mutating=True,
            )
            if moved is not None:
                v.compare(
                    "set_xyz: x read back",
                    _position(moved)[0],
                    x + delta_um,
                    tolerance=XY_TOL_UM,
                )
                v.compare(
                    "set_xyz: y read back",
                    _position(moved)[1],
                    y + delta_um,
                    tolerance=XY_TOL_UM,
                )
        finally:
            back = v.callable(
                "set_xyz: back to where it was",
                lambda: sess.set_xyz(x, y, z),
                context={"target": [x, y, z]},
                mutating=True,
            )
            if back is not None:
                v.compare(
                    "restore: x where it was",
                    _position(back)[0],
                    x,
                    tolerance=XY_TOL_UM,
                )
                v.compare(
                    "restore: y where it was",
                    _position(back)[1],
                    y,
                    tolerance=XY_TOL_UM,
                )


def phase_acquire(v: vh.Validator, sess: Any, args: argparse.Namespace) -> None:
    """One capture, checked the way the controller checks it."""
    if args.mock:
        v.skip("phase: acquire", "the mock exports no LAS X files; run live")
        return
    with v.phase("acquire: one capture, checked by the controller"):
        answer = v.callable(
            "acquire: capture + save",
            lambda: sess._session.acquire(
                position_label="installed-smoke",
                acquisition_settings={"folder": "installed-smoke"},
            ),
            mutating=True,
        )
        if answer is None:
            return
        problems = zmart_controller.check_acquire_answer(answer)
        v.compare("acquire: the controller accepts the answer", problems, [])
        content = answer.get("content") if isinstance(answer, dict) else None
        files = (content or {}).get("files") or []
        v.compare("acquire: at least one file", len(files) >= 1, True)
        present = all(Path(f).is_file() and Path(f).stat().st_size > 0 for f in files)
        v.compare("acquire: every file exists and is not empty", present, True)


# --- the checklist ----------------------------------------------------------


def acceptance(v: vh.Validator, report: vh.RunReport) -> None:
    """One line per acceptance point, from the records, at the end of the report."""
    by_name = {r.name: r.status for r in v.records}

    def verdict(*names: str, skipped_by: str | None = None) -> str:
        """PASS when every record passed; SKIP when the phase was skipped; else FAIL."""
        if skipped_by is not None and by_name.get(skipped_by) == "SKIP":
            return "SKIP"
        statuses = [by_name.get(n, "MISSING") for n in names]
        if all(s == "PASS" for s in statuses):
            return "PASS"
        if any(s in ("FAIL", "MISSING") for s in statuses):
            return "FAIL"
        return "SKIP"

    points = [
        (
            "installed by folder",
            verdict(
                "install: register_driver(folder)", "install: get_instruments lists it"
            ),
        ),
        (
            "connected by name, every get_* answer fits the contract",
            verdict("contract: every get_* answer fits"),
        ),
        (
            "every command answers in the controller's shape",
            verdict(
                "get_info",
                "get_actuators",
                "get_xyz",
                "get_state",
                "get_acquisition_settings",
                "get_procedures",
                "set_state: answers what was applied",
            ),
        ),
        (
            "a limits file governs the session",
            verdict("get_state: a limits file governs this session"),
        ),
        (
            "the limits gate refuses before anything moves",
            verdict(
                "limits: a far move is answered as a failure",
                "limits: the stage did not move",
            ),
        ),
        (
            "a move is read back where it was asked",
            verdict(
                "set_xyz: x read back", "set_xyz: y read back", skipped_by="phase: move"
            ),
        ),
        (
            "the controller accepts an acquisition",
            verdict(
                "acquire: the controller accepts the answer",
                "acquire: every file exists and is not empty",
                skipped_by="phase: acquire",
            ),
        ),
        (
            "everything restored",
            verdict(
                "restore: x where it was",
                "restore: y where it was",
                skipped_by="phase: move",
            ),
        ),
    ]
    report.note(
        "**Acceptance checklist** (green on the simulator, then green on the scope, is done):"
    )
    for label, result in points:
        report.note(f"{result}: {label}")
    v._log.info("--- acceptance ---")
    for label, result in points:
        v._log.info("  %-5s %s", result, label)


# --- CLI --------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--mock", action="store_true", help="the in-process LAS X mock (no LAS X)"
    )
    p.add_argument("--mock-latency", type=float, default=0.0)
    p.add_argument("--client-name", default="PythonClient")
    p.add_argument("--api-delay-ms", type=int, default=None)
    p.add_argument("--allow-move", action="store_true", help="a small move and back")
    p.add_argument("--allow-acquire", action="store_true", help="one capture + save")
    p.add_argument("--xy-delta-um", type=float, default=25.0)
    p.add_argument(
        "--output-root", default=None, help="where acquire writes (default: temp)"
    )
    p.add_argument("--output", default=None, help="JSONL output path; '-' for stdout")
    p.add_argument(
        "--report-dir", default=None, help="directory for the Markdown run report"
    )
    p.add_argument("--strict-confirmation", action="store_true")
    p.add_argument(
        "--yes", action="store_true", help="skip the interactive confirm before writes"
    )
    p.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    log = vh._configure_logging(args.log_level, jsonl_to_stdout=(args.output == "-"))
    sink = vh._make_sink(args.output, log)
    report = vh.RunReport(
        script="validate_installed_driver",
        backend=(
            "mock (in-process MockLasxClient; no instrument touched)"
            if args.mock
            else "live LAS X (simulator or scope)"
        ),
        report_dir=args.report_dir,
        argv=list(argv) if argv is not None else sys.argv[1:],
    )
    v = vh.Validator(
        sink=sink, log=log, strict_confirmation=args.strict_confirmation, report=report
    )

    output_root = args.output_root
    if args.mock:
        _go_offline(args)
        if output_root is None:
            output_root = tempfile.mkdtemp(prefix="zmart_installed_validate_")
    connection = _connection(args, output_root)

    log.info("=== installed driver validator: register the folder, connect by name ===")
    crash: str | None = None
    sess = None
    try:
        if not phase_install(v):
            return v.exit_code()
        phase_contract(v, connection)
        try:
            sess = _ReportingSession(
                zmart_controller.ZmartController(DRIVER_NAME, connection)
            )
        except Exception as exc:  # noqa: BLE001 -- .NET interop / missing LAS X
            v.fail("connect: by name", f"{type(exc).__name__}: {exc}")
            return v.exit_code()
        v.compare("connect: by name", True, True)
        seen = phase_commands(v, sess)
        phase_limits(v, sess, seen.get("xyz"))
        writes = args.allow_move or args.allow_acquire
        if (
            writes
            and not args.mock
            and not args.yes
            and not vh._confirm_live_write(args)
        ):
            log.warning("aborted before live writes")
        else:
            if args.allow_move:
                phase_move(v, sess, seen.get("xyz"), args.xy_delta_um)
            else:
                v.skip("phase: move", "use --allow-move to enable")
            if args.allow_acquire:
                phase_acquire(v, sess, args)
            else:
                v.skip("phase: acquire", "use --allow-acquire to enable")
    except BaseException as exc:
        crash = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if sess is not None:
            try:
                sess.disconnect()
            except Exception as exc:  # noqa: BLE001 -- never mask the run result
                log.warning("disconnect failed: %s: %s", type(exc).__name__, exc)
        acceptance(v, report)
        v.summary()
        try:
            report_path = report.write(crashed=crash)
        except OSError as exc:  # never mask the run result with a report error
            log.error("could not write markdown run report: %s", exc)
        else:
            log.info("markdown run report: %s", report_path)
    return v.exit_code()


if __name__ == "__main__":
    sys.exit(main())
