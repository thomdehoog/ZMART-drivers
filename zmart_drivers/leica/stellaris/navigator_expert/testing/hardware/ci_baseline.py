"""At the microscope: the stage to the hardware CI's starting position, through the driver alone.

Every hardware check that moves the stage starts from one known, safe place,
the CI baseline: X 63,500 µm, Y 41,500 µm, z-wide 0 and z-galvo 0. After LAS
X starts, the stage can stand anywhere, even at 0, 0, which lies outside its
limits (seen on the simulator on 15 October 2026). So the hardware CI moves
it here first.

This script needs only the driver, not the ZMART Controller, so it works on a
computer where the driver is used by itself. Before it moves anything, it
checks that all four targets lie within this microscope's limits; z-wide is
sent straight to 0 µm, never below. After the moves it reads every drive back
and compares it with its target.

Usage, in the driver's environment on the microscope PC::

    python ci_baseline.py --yes                  # live LAS X (simulator or scope)
    python ci_baseline.py --mock                 # offline, against the stand-in LAS X

It shares its records and its report with ``validate_hardware.py``; the exit
code is 1 when a step failed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

# Put the repository root (for ``zmart_drivers``), this folder (for
# ``validate_hardware``) and testing/helpers (for the stand-ins) on the path,
# wherever the script is started from.
_HERE = Path(__file__).resolve()
_REPO_ROOT = _HERE.parents[6]
_HELPERS = _HERE.parents[1] / "helpers"
for _p in (str(_HERE.parent), str(_REPO_ROOT), str(_HELPERS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import validate_hardware as vh

# How close a drive must come to its target: the driver's own confirmation
# tolerances (20 µm for X and Y, 1 µm for z).
XY_TOL_UM = 20.0
Z_TOL_UM = 1.0

# Hardware CI always starts its mutating phases from this measured, known-safe
# rig position. Keep the raw drives separate: controller frame-Z is their sum,
# but the safety baseline is a four-coordinate hardware contract.
CI_DEFAULT_POSITION_UM = {
    "x_um": 63_500.0,
    "y_um": 41_500.0,
    "z_wide_um": 0.0,
    "z_galvo_um": 0.0,
}


def _within(value: float, lo: float, hi: float) -> bool:
    return lo <= value <= hi


def _raw_readings(xyz: dict) -> dict:
    """The four motor readings of a ``get_xyz`` answer, as the stage reports them.

    ``get_xyz`` lists every motor of each axis under ``actuator_readings`` with its
    own reading, untouched by the origin. The hardware checks below compare
    those readings with the targets handed to the driver's own ``move_xy``
    and ``move_z``, so this gathers them under the driver's names.
    """
    return {
        "x_um": xyz["x"]["actuator_readings"]["motoric"],
        "y_um": xyz["y"]["actuator_readings"]["motoric"],
        "z_wide_um": xyz["z"]["actuator_readings"]["z-wide"],
        "z_galvo_um": xyz["z"]["actuator_readings"]["z-galvo"],
    }


def _selected_job(sess: Any) -> str | None:
    """The LAS X job selected now, which the driver's own z commands address."""
    return sess.get_state()["changeable"].get("job")


def _ci_default_position_error(limits: dict) -> str | None:
    """Return why the fixed four-axis CI baseline cannot be commanded safely."""
    if CI_DEFAULT_POSITION_UM["z_wide_um"] < 0.0:
        return "Z-wide CI target must never be negative"
    for axis in ("x", "y", "z_wide", "z_galvo"):
        target = CI_DEFAULT_POSITION_UM[f"{axis}_um"]
        lo, hi = limits.get(f"{axis}_min"), limits.get(f"{axis}_max")
        if lo is None or hi is None:
            return f"{axis} limits are not configured"
        if not _within(target, float(lo), float(hi)):
            return f"{axis} target {target} um outside [{lo}, {hi}]"
    return None


def phase_ci_default_position(v: vh.Validator, sess: Any, drv: Any) -> bool:
    """Move to and verify the four-coordinate hardware-CI baseline.

    All targets are preflighted before the first command. Z-wide is commanded
    directly to 0 um (never through frame-focus arithmetic), so a non-zero
    z-galvo cannot turn the Z-wide target negative.
    """
    with v.phase("hardware-CI default position"):
        before = v.callable(
            "ci default: read start", lambda: _raw_readings(sess.get_xyz())
        )
        limits = v.callable("ci default: read limits", drv.get_stage_limits)
        if not before or not limits:
            return False
        error = _ci_default_position_error(limits)
        if error:
            v.fail("ci default: preflight", error, context=dict(CI_DEFAULT_POSITION_UM))
            return False
        if not v.compare(
            "ci default: Z-wide target is non-negative",
            CI_DEFAULT_POSITION_UM["z_wide_um"] >= 0.0,
            True,
        ):
            return False

        job = v.callable("ci default: selected job", lambda: _selected_job(sess))
        if not job:
            v.fail("ci default: selected job", "no selected job for the Z commands")
            return False

        x_um = CI_DEFAULT_POSITION_UM["x_um"]
        y_um = CI_DEFAULT_POSITION_UM["y_um"]
        z_wide_um = CI_DEFAULT_POSITION_UM["z_wide_um"]
        z_galvo_um = CI_DEFAULT_POSITION_UM["z_galvo_um"]
        commands = (
            v.command(
                "ci default: move XY",
                lambda: drv.move_xy(
                    sess.plugin.microscope.client, x_um, y_um, unit="um"
                ),
                context={"target_x_um": x_um, "target_y_um": y_um},
            ),
            v.command(
                "ci default: move Z-wide",
                lambda: drv.move_z(
                    sess.plugin.microscope.client,
                    job,
                    z_wide_um,
                    unit="um",
                    z_mode="zwide",
                ),
                context={"target_z_wide_um": z_wide_um, "non_negative": True},
            ),
            v.command(
                "ci default: move Z-galvo",
                lambda: drv.move_z(
                    sess.plugin.microscope.client,
                    job,
                    z_galvo_um,
                    unit="um",
                    z_mode="galvo",
                ),
                context={"target_z_galvo_um": z_galvo_um},
            ),
        )
        if any(not result or not result.get("success") for result in commands):
            return False

        after = v.callable(
            "ci default: readback", lambda: _raw_readings(sess.get_xyz())
        )
        if not after:
            return False
        checks = [
            v.compare(
                "ci default: X readback", after.get("x_um"), x_um, tolerance=XY_TOL_UM
            ),
            v.compare(
                "ci default: Y readback", after.get("y_um"), y_um, tolerance=XY_TOL_UM
            ),
            v.compare(
                "ci default: Z-wide readback",
                after.get("z_wide_um"),
                z_wide_um,
                tolerance=Z_TOL_UM,
            ),
            v.compare(
                "ci default: Z-galvo readback",
                after.get("z_galvo_um"),
                z_galvo_um,
                tolerance=Z_TOL_UM,
            ),
        ]
        return all(checks)


class DriverSession:
    """The driver's ``Microscope``, with the few names the baseline phase reads.

    The baseline phase was written for the controller's session; this gives
    it the same three things straight from the driver: where the stage is,
    the selected job, and the LAS X client the moves are sent through.
    """

    def __init__(self, scope: Any):
        self.plugin = SimpleNamespace(microscope=scope)

    def get_xyz(self) -> dict:
        return self.plugin.microscope.position()

    def get_state(self) -> dict:
        return self.plugin.microscope.state()

    def disconnect(self) -> None:
        self.plugin.microscope.close()


def _connect(mock: bool) -> DriverSession:
    """Connect through the driver's own ``connect()``; with *mock*, to the stand-in LAS X."""
    from zmart_drivers.leica.stellaris import navigator_expert

    if mock:
        from dataclasses import replace

        from limits_fixtures import hermetic_mock_machine_root
        from mock_lasx_api import MockLasxClient

        from zmart_drivers.leica.stellaris.navigator_expert.primitives.cam_api import (
            client,
        )
        from zmart_drivers.leica.stellaris.navigator_expert.primitives.direct import (
            profile as reading_settings,
        )

        # A throwaway settings folder, so the mock never reads or writes this computer's.
        hermetic_mock_machine_root()
        client.connect_python_client = lambda **_kw: MockLasxClient(latency=0.0)
        # The stand-in has no LAS X log, so only the API can confirm a job change.
        reading_settings.STATE_READERS = replace(
            reading_settings.STATE_READERS, selected_job_confirm_fallback="api"
        )
    return DriverSession(navigator_expert.connect())


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--mock", action="store_true", help="the stand-in LAS X (no microscope)"
    )
    p.add_argument("--yes", action="store_true", help="move without asking first")
    p.add_argument(
        "--output",
        default=None,
        help="a JSONL file for the records, or - for the console",
    )
    p.add_argument(
        "--report-dir", default=None, help="where the Markdown report is written"
    )
    p.add_argument("--log-level", default="INFO")
    p.add_argument("--strict-confirmation", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    log = vh._configure_logging(args.log_level, jsonl_to_stdout=(args.output == "-"))
    report = vh.RunReport(
        script="ci_baseline",
        backend="mock (in-process MockLasxClient; no instrument touched)"
        if args.mock
        else "live LAS X (simulator or scope)",
        report_dir=args.report_dir,
        argv=list(argv) if argv is not None else sys.argv[1:],
    )
    v = vh.Validator(
        sink=vh._make_sink(args.output, log),
        log=log,
        strict_confirmation=args.strict_confirmation,
        report=report,
    )
    if not (args.mock or args.yes):
        answer = input(
            f"Move the stage to the CI baseline {CI_DEFAULT_POSITION_UM}? [yes/no] "
        )
        if answer.strip().lower() not in ("y", "yes"):
            log.warning("not moved: the move was not confirmed")
            return 1

    import low_level as drv

    crash: str | None = None
    try:
        try:
            sess = _connect(args.mock)
        except Exception as exc:  # noqa: BLE001 -- LAS X missing, or .NET interop
            v.fail("client: connect", f"{type(exc).__name__}: {exc}")
            return v.exit_code()
        try:
            phase_ci_default_position(v, sess, drv)
        finally:
            sess.disconnect()
    except BaseException as exc:
        crash = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        v.summary()
        try:
            log.info("markdown run report: %s", report.write(crashed=crash))
        except OSError as exc:  # a report that cannot be written never hides the result
            log.error("could not write the markdown run report: %s", exc)
    return v.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())
