"""Offline tests for the ZMART Controller adapter.

The driver layers underneath (readers, commands, capture, save, motion)
are patched; what is under test is the adapter's contract with
``zmart_controller``: registration, frame math, actuator mapping,
option validation, and closed-handle semantics — including a full
end-to-end pass through a real controller ``Session``.
"""

import json
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from limits_fixtures import (
    DEFAULT_STAGE_UM,
    install_permissive_limits,
    provision_machine_limits,
)

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_interface.primitive_handling.profiles import (
    ImageSaveProfile,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.output.product import (
    PlaneIndex,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_interface.primitive_handling import (
    limit_rules as limits_checks,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.dispatcher import (
    gate as _gate,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_interface import (
    parsing as _readers_parsing,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.for_zmart_controller.zmart_adapter import (
    zmart_adapter as adapter,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.configuration import (
    image_stage_registration as _orientation,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.configuration import (
    store as _machine,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.output import (
    command_log as _log,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.output import (
    lasx_cache_release as _release,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.output import save as _save
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.procedures import (
    acquire as _capture,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.procedures import (
    acquisition as _acquisition,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.procedures import (
    backlash as _motion,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.procedures import (
    changes as _commands,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.procedures import (
    frame as _frame,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.procedures import (
    instrument as _instrument,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.procedures import (
    session as _info,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_interface.primitive_handling import (
    state as _readers,
)


def _origin(
    x_um=0.0, y_um=0.0, z_wide_um=0.0, z_galvo_um=0.0, z_focus_um=0.0, objective=None
):
    return {
        "x_um": x_um,
        "y_um": y_um,
        "z_wide_um": z_wide_um,
        "z_galvo_um": z_galvo_um,
        "z_focus_um": z_focus_um,
        "objective": objective,
    }


def _handle(gate_constraints=None, gated=True, **overrides):
    """A handle whose client carries a permissive commands-layer gate state.

    ``gate_constraints`` bounds the ``set_xyz`` key (e.g.
    ``{"x_um": {"min": 0, "max": 100}}``); ``gated=False`` leaves the client
    without any gate state (the fail-closed posture of a failed handshake).
    The stage stands at the origin, as a get_xyz after connecting would read.
    """
    h = adapter.ZmartHandle(
        client=object(), connection=dict(adapter.CONNECTION), hash6="000abc"
    )
    h.driven_to = {"x": 0.0, "y": 0.0, "z": 0.0, "z_wide_um": 0.0, "z_galvo_um": 0.0}
    if gated:
        install_permissive_limits(h.client, **(gate_constraints or {}))
    for key, value in overrides.items():
        setattr(h, key, value)
    return h


def _settings(
    z_wide_um=50.0, z_galvo_um=0.0, objective="HC PL APO 63x/1.40 OIL CS2", slot=3
):
    return {
        "objective": {"name": objective, "magnification": 63, "slotIndex": slot},
        "zPosition": {
            "z-wide": {"position": z_wide_um},
            "z-galvo": {"position": z_galvo_um},
        },
    }


def _patch_position(
    x_um=100.0, y_um=200.0, z_wide_um=50.0, z_galvo_um=0.0, job="Overview", slot=3
):
    return (
        patch.object(_readers, "get_xy", return_value={"x_um": x_um, "y_um": y_um}),
        patch.object(
            _readers,
            "get_job_settings",
            return_value=_settings(
                z_wide_um=z_wide_um, z_galvo_um=z_galvo_um, slot=slot
            ),
        ),
        patch.object(
            _readers,
            "get_selected_job",
            return_value={"Name": job, "IsSelected": True},
        ),
        patch.object(
            _readers_parsing,
            "make_changeable_copy",
            side_effect=lambda settings: settings,
        ),
    )


def _wide_limits():
    """Configure a permissive stage envelope for tests that exercise set_xyz."""
    limits_checks.set_stage_limits(
        x_min=0.0,
        x_max=1_000_000.0,
        y_min=0.0,
        y_max=1_000_000.0,
        z_galvo_min=-250.0,
        z_galvo_max=250.0,
        z_wide_min=-100_000.0,
        z_wide_max=100_000.0,
    )


def _clear_limits():
    limits_checks._stage_limits.update(dict.fromkeys(limits_checks._stage_limits, None))


def _a_capture(job: str) -> SimpleNamespace:
    """What a capture answers, as the driver's acquire hands it on: the job and when it ran."""
    return SimpleNamespace(
        job=job,
        started_at=1_760_000_000.0,
        finished_at=1_760_000_002.5,
        command_result={"success": True},
    )


_WRITE_THE_LOG = _log.write
_NO_LOG_ON_DISK = patch.object(
    _log,
    "write",
    lambda output_root, naming, entry: (
        Path("/tmp/out") / "log" / f"{naming.stem}.log.json"
    ),
)


def setUpModule():
    """No test here writes a capture's log to disk; the log's own test does, in a temporary folder."""
    _NO_LOG_ON_DISK.start()


def tearDownModule():
    _NO_LOG_ON_DISK.stop()


class TestRegistration(unittest.TestCase):
    def test_connection_profile_does_not_select_operator_calibration(self):
        self.assertNotIn("calibration_name", adapter.CONNECTION)

    def test_the_driver_module_holds_every_command(self):
        from zmart_controller.registry import driver_functions
        from zmart_controller.zmart_controller import COMMANDS

        from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.for_zmart_controller import (
            zmart_controller_plugin as driver,
        )

        ops = driver_functions(driver)
        for op in COMMANDS:
            self.assertIn(op, ops)
        self.assertIn("connect", ops)
        self.assertIn("disconnect", ops)

    def test_importing_the_adapter_registers_nothing(self):
        source = Path(adapter.__file__).read_text(encoding="utf-8")
        self.assertNotIn("zmart_controller import", source)

    def test_set_origin_is_a_driver_step_not_a_controller_op(self):
        """The origin is driver configuration: the controller cannot set it."""
        from zmart_controller.registry import driver_functions

        from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.for_zmart_controller import (
            zmart_controller_plugin as driver,
        )

        self.assertNotIn("set_origin", driver_functions(driver))
        self.assertTrue(callable(adapter.set_origin))


class TestCalibrationSelection(unittest.TestCase):
    def test_named_connection_calibration_is_used_for_objective_translations(self):
        # The driver now owns loading: the named calibration flows through
        # connect_microscope -> session._load_objective_calibration, where one
        # exact document supplies both translations and readiness provenance.
        from zmart_drivers.leica.stellaris5_y42h93.navigator_expert import (
            connect as drv_session,
        )
        from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.configuration.optical_calibration import (
            model as cal_model,
        )

        cfg = {
            "schema_version": 13,
            "objectives": {
                "1": {
                    "name": "10x",
                    "translation_um": [0.0, 0.0, 0.0],
                },
                "2": {
                    "name": "20x",
                    "translation_um": [12.0, 17.0, 3.0],
                },
            },
        }

        path = Path("/tmp/lens_A/calibration.json")
        with (
            patch.object(cal_model, "default_path", return_value=path),
            patch.object(cal_model, "load_calibration", return_value=cfg) as load,
        ):
            translations, info = drv_session._load_objective_calibration("lens_A")

        load.assert_called_once_with(path.absolute())
        self.assertEqual(translations[2], (12.0, 17.0, 3.0))
        self.assertEqual(info["measured_slots"], [1, 2])

    def test_translations_and_provenance_come_from_one_calibration_read(self):
        """A snapshot adoption during connect cannot mix old math with new proof."""
        from zmart_drivers.leica.stellaris5_y42h93.navigator_expert import (
            connect as drv_session,
        )
        from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.configuration.optical_calibration import (
            model as cal_model,
        )

        old = {
            "schema_version": 13,
            "objectives": {
                "1": {"name": "10x", "translation_um": [0, 0, 0]},
                "2": {"name": "20x", "translation_um": [1, 2, 3]},
            },
        }
        newly_adopted = {
            **old,
            "objectives": {
                "1": {"name": "10x", "translation_um": [0, 0, 0]},
                "2": {"name": "20x", "translation_um": [9, 8, 7]},
            },
        }
        path = Path("/tmp/lens_A/calibration.json")
        with (
            patch.object(cal_model, "default_path", return_value=path),
            patch.object(
                cal_model,
                "load_calibration",
                side_effect=[old, newly_adopted],
            ) as load,
        ):
            translations, info = drv_session._load_objective_calibration("lens_A")

        self.assertEqual(load.call_count, 1)
        self.assertEqual(translations[2], (1.0, 2.0, 3.0))
        self.assertEqual(info["measured_slots"], [1, 2])


class TestFrame(unittest.TestCase):
    def setUp(self):
        _wide_limits()  # set_xyz pre-flights against the stage envelope

    def tearDown(self):
        _clear_limits()

    def test_set_origin_zeros_the_frame(self):
        h = _handle()
        patches = _patch_position(x_um=1000.0, y_um=2000.0, z_wide_um=30.0)
        with patches[0], patches[1], patches[2], patches[3]:
            record = adapter.set_origin(h)
            self.assertEqual(record["origin"], {"x": 0.0, "y": 0.0, "z": 0.0})
            self.assertTrue(record["origin_file"].endswith("origin.json"))
            self.assertEqual(record["reference"]["z_focus_um"], 30.0)
            pos = adapter.get_xyz(h)
        self.assertEqual(pos["x"]["position"], 0.0)
        self.assertEqual(pos["z"]["position"], 0.0)
        # Each axis carries the five entries of the controller's contract, in
        # this order, says which motor this answer used, and names every motor
        # that can move it.
        self.assertEqual(
            list(pos["x"]), ["position", "unit", "actuator", "actuators", "canvas"]
        )
        self.assertEqual(pos["z"]["actuator"], "z-wide")
        self.assertEqual(pos["x"]["unit"], "micrometer")
        self.assertEqual(pos["x"]["actuators"], {"motoric": 1000.0})
        self.assertEqual(pos["z"]["actuators"], {"z-wide": 30.0, "z-galvo": 0.0})

    def test_set_origin_persists_into_its_own_origin_folder(self):
        import json
        import tempfile

        from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.configuration.store import (
            MachineProfile,
            is_snapshot_name,
        )

        with tempfile.TemporaryDirectory() as tmp:
            profile = MachineProfile(programdata_root=Path(tmp))
            h = _handle()
            patches = _patch_position(
                x_um=1000.0, y_um=2000.0, z_wide_um=30.0, z_galvo_um=2.0
            )
            with (
                patch.object(_machine, "MACHINE", profile),
                patches[0],
                patches[1],
                patches[2],
                patches[3],
            ):
                record = adapter.set_origin(h)
            # Persists to its own origin/<datetime>/ snapshot tree.
            self.assertEqual(record["origin_file"], str(profile.origin_path()))
            self.assertEqual(profile.origin_path().parent.parent.name, "origin")
            self.assertTrue(is_snapshot_name(profile.origin_path().parent.name))
            saved = json.loads(profile.origin_path().read_text(encoding="utf-8"))
            self.assertEqual(saved["origin"]["x_um"], 1000.0)
            self.assertEqual(saved["origin"]["z_wide_um"], 30.0)
            self.assertEqual(saved["origin"]["z_galvo_um"], 2.0)
            self.assertEqual(saved["origin"]["z_focus_um"], 32.0)
            self.assertEqual(saved["origin"]["objective"]["magnification"], 63)
            self.assertEqual(saved["job"], "Overview")

    def _connect_with_profile(self, profile, connection=None):
        """Connect against a throwaway machine configuration and a stand-in client."""
        with (
            patch.object(_machine, "MACHINE", profile),
            patch.object(
                adapter._session, "connect_python_client", return_value=object()
            ),
        ):
            return adapter.connect(connection or dict(adapter.CONNECTION))

    def test_connect_restores_the_saved_origin(self):
        """The newest saved origin, objective included, becomes the session's frame."""
        import tempfile

        from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.configuration.store import (
            MachineProfile,
        )

        objective = {"slotIndex": 2, "magnification": 63, "name": "HC PL APO 63x"}
        with tempfile.TemporaryDirectory() as tmp:
            profile = MachineProfile(programdata_root=Path(tmp))
            profile.write_origin({"origin": _origin(x_um=1.0), "captured_at": 1.0})
            saved = _origin(
                x_um=1000.0,
                y_um=2000.0,
                z_wide_um=30.0,
                z_galvo_um=2.0,
                z_focus_um=32.0,
                objective=objective,
            )
            profile.write_origin({"origin": saved, "captured_at": 2.0})
            h = self._connect_with_profile(profile)
        self.assertEqual(h.origin, saved)
        # The loaded origin keeps its objective, so a move under another
        # objective without calibration is still refused.
        h.translations = None
        with self.assertRaisesRegex(RuntimeError, "objective changed"):
            _frame.objective_delta_um(h, {"slotIndex": 3})

    def test_set_origin_then_connect_round_trips(self):
        """What set_origin captures is exactly what the next connect loads."""
        import tempfile

        from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.configuration.store import (
            MachineProfile,
        )

        with tempfile.TemporaryDirectory() as tmp:
            profile = MachineProfile(programdata_root=Path(tmp))
            h = _handle()
            patches = _patch_position(
                x_um=1000.0, y_um=2000.0, z_wide_um=30.0, z_galvo_um=2.0
            )
            with (
                patch.object(_machine, "MACHINE", profile),
                patches[0],
                patches[1],
                patches[2],
                patches[3],
            ):
                adapter.set_origin(h)
            fresh = self._connect_with_profile(profile)
        self.assertEqual(fresh.origin, h.origin)

    def test_connect_without_a_saved_origin_uses_stage_coordinates(self):
        """No origin saved yet: the frame is the absolute stage, with a warning."""
        import tempfile

        from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.configuration.store import (
            MachineProfile,
        )

        with tempfile.TemporaryDirectory() as tmp:
            profile = MachineProfile(programdata_root=Path(tmp))
            with self.assertLogs(_frame.log, level="WARNING") as logs:
                h = self._connect_with_profile(profile)
        self.assertEqual(h.origin, _origin())
        self.assertIn("no frame origin has been saved", "\n".join(logs.output))

    def test_connect_refuses_a_corrupt_origin_file(self):
        """A damaged origin file stops connect before LAS X is contacted."""
        import tempfile

        from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.configuration.store import (
            MachineProfile,
        )

        with tempfile.TemporaryDirectory() as tmp:
            profile = MachineProfile(programdata_root=Path(tmp))
            path = profile.write_origin({"origin": _origin(x_um=5.0)})
            path.write_text("{not json", encoding="utf-8")
            with (
                patch.object(_machine, "MACHINE", profile),
                patch.object(
                    adapter._session, "connect_microscope"
                ) as connect_microscope,
            ):
                with self.assertRaisesRegex(RuntimeError, "could not be read"):
                    adapter.connect(dict(adapter.CONNECTION))
            connect_microscope.assert_not_called()

    def test_connect_refuses_an_incomplete_origin_file(self):
        """An origin missing a value or its objective is refused, not zero-filled."""
        import tempfile

        from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.configuration.store import (
            MachineProfile,
        )

        broken = [
            ({"origin": {"x_um": 5}}, "'y_um'"),
            ({"origin": {**_origin(), "z_focus_um": "nan"}}, "'z_focus_um'"),
            ({"origin": {**_origin(), "x_um": float("inf")}}, "'x_um'"),
            (
                {"origin": {k: v for k, v in _origin().items() if k != "objective"}},
                "objective",
            ),
            ({"origin": {**_origin(), "objective": "63x"}}, "objective"),
            ({"job": "Overview"}, "no 'origin' section"),
        ]
        for payload, message in broken:
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as tmp:
                profile = MachineProfile(programdata_root=Path(tmp))
                profile.write_origin(payload)
                with self.assertRaisesRegex(RuntimeError, message):
                    self._connect_with_profile(profile)

    def test_connect_can_skip_the_saved_origin(self):
        """load_origin=False starts in stage coordinates, even over a damaged file."""
        import tempfile

        from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.configuration.store import (
            MachineProfile,
        )

        with tempfile.TemporaryDirectory() as tmp:
            profile = MachineProfile(programdata_root=Path(tmp))
            path = profile.write_origin({"origin": _origin(x_um=5.0)})
            path.write_text("{not json", encoding="utf-8")
            h = self._connect_with_profile(
                profile, {**adapter.CONNECTION, "load_origin": False}
            )
        self.assertEqual(h.origin, _origin())

    def test_get_xyz_is_origin_relative(self):
        h = _handle(origin=_origin(x_um=1000.0, y_um=2000.0, z_focus_um=30.0))
        patches = _patch_position(
            x_um=1010.0, y_um=1990.0, z_wide_um=32.0, z_galvo_um=3.0
        )
        with patches[0], patches[1], patches[2], patches[3]:
            pos = adapter.get_xyz(h)
        self.assertEqual(pos["x"]["position"], 10.0)
        self.assertEqual(pos["y"]["position"], -10.0)
        self.assertEqual(pos["z"]["position"], 5.0)  # (32 + 3) - 30 focus sum
        # The motors' own readings are the stage's numbers, with nothing
        # subtracted: neither the origin nor an objective translation.
        self.assertEqual(pos["x"]["actuators"], {"motoric": 1010.0})
        self.assertEqual(pos["y"]["actuators"], {"motoric": 1990.0})
        self.assertEqual(pos["z"]["actuators"], {"z-wide": 32.0, "z-galvo": 3.0})
        for axis in ("x", "y", "z"):
            self.assertEqual(pos[axis]["unit"], "micrometer")
        self.assertNotIn("hardware", pos)
        # What it read is what a capture is labelled with, so a capture asks nothing more.
        self.assertEqual(
            h.driven_to,
            {"x": 10.0, "y": -10.0, "z": 5.0, "z_wide_um": 32.0, "z_galvo_um": 3.0},
        )

    def test_get_xyz_names_every_motor_get_actuators_lists(self):
        """The motors reported per axis are exactly the menu, so a workflow can
        look a motor's reading up by the name it would pass to with_actuators."""
        h = _handle()
        patches = _patch_position()
        with patches[0], patches[1], patches[2], patches[3]:
            pos = adapter.get_xyz(h)
            menu = adapter.get_actuators(h)
        for axis in ("x", "y", "z"):
            self.assertEqual(list(pos[axis]["actuators"]), menu[axis]["options"])
            self.assertEqual(pos[axis]["actuator"], menu[axis]["default"])
        # with_actuators only chooses a motor; the answer still reports all of
        # them, and says which one it used
        with patches[0], patches[1], patches[2], patches[3]:
            pos = adapter.get_xyz(h, with_actuators={"z": "z-galvo"})
        self.assertEqual(list(pos["z"]["actuators"]), ["z-wide", "z-galvo"])
        self.assertEqual(pos["z"]["actuator"], "z-galvo")

    def test_set_xyz_moves_to_absolute_and_maps_z_actuator(self):
        h = _handle(origin=_origin(x_um=1000.0, y_um=2000.0, z_focus_um=30.0))
        moves = {}

        def fake_move_xy_with_backlash(client, x_um, y_um, **kwargs):
            moves["xy"] = (x_um, y_um)
            return {"success": True, "confirmed": True}

        def fake_move_z(client, job, z, unit="um", z_mode="galvo", **kwargs):
            moves["z"] = (z, z_mode)
            return {"success": True, "confirmed": True}

        # current hardware: z-wide at 32, z-galvo at 3
        patches = _patch_position(z_wide_um=32.0, z_galvo_um=3.0)
        with (
            patch.object(_motion, "arrive_xy", fake_move_xy_with_backlash),
            patch.object(_commands, "move_z", fake_move_z),
            patches[0],
            patches[1],
            patches[2],
            patches[3],
        ):
            record = adapter.set_xyz(
                h, 10.0, 20.0, 5.0, with_actuators={"z": "z-galvo"}
            )
        self.assertEqual(moves["xy"], (1010.0, 2020.0))
        # target focus = 30 + 5 = 35; galvo absorbs it minus z-wide's 32 -> 3
        self.assertEqual(moves["z"], (3.0, "galvo"))
        # The answer is a fresh get_xyz: the (patched) readings, not the targets.
        self.assertEqual(list(record), ["x", "y", "z", "objective_translation_um"])
        self.assertEqual(
            list(record["z"]), ["position", "unit", "actuator", "actuators", "canvas"]
        )
        self.assertEqual(record["z"]["actuator"], "z-galvo")
        self.assertEqual(record["z"]["actuators"], {"z-wide": 32.0, "z-galvo": 3.0})
        self.assertEqual(record["z"]["position"], 5.0)  # (32 + 3) − 30
        # the selection does NOT persist: defaults are fixed, never sticky
        self.assertEqual(_frame._resolve_actuators(None)["z"], "z-wide")
        self.assertEqual(_frame._resolve_actuators(None)["x"], "motoric")

    def test_set_xyz_z_wide_compensates_parked_galvo(self):
        # origin inside the physical backstop (x/y >= 1000)
        h = _handle(origin=_origin(x_um=10000.0, y_um=10000.0, z_focus_um=30.0))
        moves = {}

        def fake_move_z(client, job, z, unit="um", z_mode="galvo", **kwargs):
            moves["z"] = (z, z_mode)
            return {"success": True, "confirmed": True}

        patches = _patch_position(z_wide_um=25.0, z_galvo_um=4.0)
        with (
            patch.object(
                _motion,
                "arrive_xy",
                return_value={"success": True, "confirmed": True},
            ),
            patch.object(_commands, "move_z", fake_move_z),
            patches[0],
            patches[1],
            patches[2],
            patches[3],
        ):
            adapter.set_xyz(h, 0.0, 0.0, 10.0)  # default z actuator: z-wide
        # target focus = 30 + 10 = 40; the parked galvo offset (4) is kept
        self.assertEqual(moves["z"], (36.0, "zwide"))

    def test_unconfirmed_z_move_is_recorded_and_the_position_is_read_back(self):
        """A leg that was accepted but never confirmed does not stop the workflow:
        it is written down, and the answer is what the microscope reads back."""
        h = _handle()
        patches = _patch_position()
        with (
            patch.object(
                _motion,
                "arrive_xy",
                return_value={"success": True, "confirmed": True},
            ),
            patch.object(
                _commands,
                "move_z",
                return_value={
                    "success": True,
                    "confirmed": False,
                    "message": "no readback",
                },
            ),
            patches[0],
            patches[1],
            patches[2],
            patches[3],
        ):
            answer = adapter.set_xyz(h, 0.0, 0.0, 0.0)
        self.assertEqual(sorted(answer), ["objective_translation_um", "x", "y", "z"])
        self.assertEqual(len(h.unconfirmed), 1)
        self.assertIn("set_xyz: z", h.unconfirmed[0]["what"])
        self.assertEqual(h.unconfirmed[0]["message"], "no readback")

    def test_failed_z_move_still_raises(self):
        """A leg LAS X refused or that failed is a real failure, not an unconfirmed one."""
        h = _handle()
        patches = _patch_position()
        with (
            patch.object(
                _motion,
                "arrive_xy",
                return_value={"success": True, "confirmed": True},
            ),
            patch.object(
                _commands,
                "move_z",
                return_value={
                    "success": False,
                    "confirmed": None,
                    "message": "refused",
                },
            ),
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            self.assertRaisesRegex(RuntimeError, "failed"),
        ):
            adapter.set_xyz(h, 0.0, 0.0, 0.0)
        self.assertEqual(h.unconfirmed, [])

    def test_unknown_actuator_rejected(self):
        h = _handle()
        with self.assertRaises(ValueError):
            adapter.get_actuators(h) and adapter.set_xyz(
                h, 0, 0, 0, with_actuators={"z": "hovercraft"}
            )

    def test_actuator_menu(self):
        """The driver names its default per axis: the focus drive that reaches the whole travel for z."""
        h = _handle()
        self.assertEqual(
            adapter.get_actuators(h),
            {
                "x": {"options": ["motoric"], "default": "motoric"},
                "y": {"options": ["motoric"], "default": "motoric"},
                "z": {"options": ["z-wide", "z-galvo"], "default": "z-wide"},
            },
        )


class TestAcquire(unittest.TestCase):
    def _jobs(self):
        return [
            {"Name": "Overview", "IsSelected": True, "IsAutofocus": False},
            {"Name": "HiRes", "IsSelected": False, "IsAutofocus": False},
            {"Name": "AF Job", "IsSelected": False, "IsAutofocus": True},
        ]

    def test_the_only_setting_is_how_a_capture_is_triggered(self):
        """``full`` runs the whole selected job; the job itself is state, saving is set up."""
        h = _handle()
        self.assertEqual(
            adapter.get_acquisition_settings(h),
            {"trigger": {"options": ["full"], "default": "full", "active": "full"}},
        )
        self.assertEqual(_acquisition.ACQUISITION_BACKLASH_DEFAULT_ROUNDS, 0)

    def test_unknown_or_invalid_option_rejected(self):
        h = _handle(connection={**adapter.CONNECTION, "output_root": "/tmp/out"})
        with (
            patch.object(_readers, "get_jobs", return_value=self._jobs()),
            patch.object(
                _readers,
                "get_selected_job",
                return_value={"Name": "Overview", "IsSelected": True},
            ),
        ):
            with self.assertRaisesRegex(ValueError, "unknown acquisition setting"):
                adapter.acquire(
                    h,
                    acquisition_type="acquisition",
                    output_folder=None,
                    position_label="A1",
                    acquisition_settings={"fromat": "x"},
                )
            with self.assertRaisesRegex(ValueError, "invalid value"):
                adapter.acquire(
                    h,
                    acquisition_type="acquisition",
                    output_folder=None,
                    position_label="A1",
                    acquisition_settings={"trigger": "snap"},
                )
            for gone in ("job", "folder", "format", "save_to", "backlash_rounds"):
                with (
                    self.subTest(gone=gone),
                    self.assertRaisesRegex(ValueError, "unknown acquisition setting"),
                ):
                    adapter.acquire(
                        h,
                        acquisition_type="acquisition",
                        output_folder=None,
                        position_label="A1",
                        acquisition_settings={gone: "x"},
                    )

    def test_without_a_data_location_nothing_is_saved_and_the_notebook_is_named(self):
        h = _handle()
        with self.assertRaisesRegex(RuntimeError, "set_data_location"):
            adapter.acquire(
                h,
                acquisition_type="acquisition",
                output_folder=None,
                position_label="A1",
            )

    def test_the_set_up_data_location_is_where_captures_are_staged(self):
        """Where this microscope saves is set up once and said in ``setup``, not asked per capture."""
        with tempfile.TemporaryDirectory() as tmp:
            h = _handle(data_location=Path(tmp))
            with patch.object(_info, "selected_job_name", return_value="Overview"):
                info = adapter.get_info(h)
            self.assertNotIn("output_root", info)
            self.assertTrue(info["setup"]["data_location"]["done"])
            self.assertIn(str(Path(tmp)), info["setup"]["data_location"]["says"])
            with self._capturing(output_root=None) as calls:
                adapter.acquire(
                    h,
                    acquisition_type="acquisition",
                    output_folder=None,
                    position_label="A1",
                )
            self.assertEqual(calls["output_root"].parent.parent, Path(tmp).resolve())

    def test_a_sessions_own_output_root_wins_over_the_set_up_one(self):
        h = _handle(
            connection={**adapter.CONNECTION, "output_root": "/chosen/zmart"},
            data_location=Path("/elsewhere"),
        )
        with self._capturing(output_root=None) as calls:
            adapter.acquire(
                h,
                acquisition_type="acquisition",
                output_folder=None,
                position_label="A1",
            )
        self.assertEqual(
            calls["output_root"].parent.parent, Path("/chosen/zmart").resolve()
        )

    @contextmanager
    def _capturing(
        self, *, image_paths=None, projections=None, output_root=Path("/tmp/out")
    ):
        """Everything an ``acquire`` needs stood in for, so a test says one thing."""
        written = image_paths or {0: Path("/tmp/out/img.ome.tif")}
        calls = {}

        def fake_save(client, acq, output_root, naming, **kwargs):
            calls["save"] = kwargs
            calls["output_root"] = Path(output_root)
            return SimpleNamespace(
                image_paths=dict(written),
                naming=naming,
                projection_paths=tuple(projections or ()),
            )

        def fake_log(output_root, naming, entry):
            calls["log"] = entry
            return Path("/tmp/out/log") / f"{naming.stem}.log.json"

        patches = _patch_position(job="Overview")
        with ExitStack() as standing_in:
            for stand_in in (
                patch.object(_readers, "get_jobs", return_value=self._jobs()),
                patch.object(_readers, "get_hardware_info", return_value={}),
                patch.object(
                    _commands, "select_job", lambda c, j, **k: {"success": True}
                ),
                patch.object(
                    _motion,
                    "correct_backlash",
                    lambda client, **k: {"success": True, "confirmed": True},
                ),
                patch.object(_capture, "acquire", lambda c, j, **k: _a_capture(j)),
                patch.object(_save, "save", fake_save),
                patch.object(_log, "write", fake_log),
                *patches,
            ):
                standing_in.enter_context(stand_in)
            # Where to save is otherwise this microscope's set-up data location.
            if output_root is not None:
                standing_in.enter_context(
                    patch.object(_info, "output_root", return_value=output_root)
                )
            yield calls

    def test_get_info_says_how_far_the_stage_can_go_and_what_the_session_stands_on(
        self,
    ):
        """The two things the operator page reads at connect, and the mock invented.

        The page sizes its canvas from ``get_info().canvas`` and lists what a
        session stands on from ``connection_status``. The mock reports both;
        this adapter reported neither, so a real connect drew a canvas of no
        size under a Connect step with nothing to say. The canvas is the
        limits envelope in the frame -- nothing can be imaged outside it --
        and the checks are facts the driver already holds.
        """
        h = _handle(
            gate_constraints={
                "x_um": {"min": 0, "max": 120_000},
                "y_um": {"min": 0, "max": 80_000},
                "z_wide_um": {"min": 0, "max": 10_000},
            }
        )
        h.origin = {
            **h.origin,
            "x_um": 1_000.0,
            "y_um": 500.0,
            "z_wide_um": 100.0,
            "z_focus_um": 100.0,
        }
        # The envelope the gate was handed at connect, in raw stage um.
        gate = adapter._gate._state_for(h.client)
        adapter._gate._install(
            h.client,
            adapter._gate.GateState(
                limits=gate.limits,
                error=None,
                stage_cfg={
                    "stage_um": {
                        "x": [0.0, 120_000.0],
                        "y": [0.0, 80_000.0],
                        "z_wide": [0.0, 10_000.0],
                        "z_galvo": [-50.0, 50.0],
                    }
                },
            ),
        )
        patches = _patch_position(x_um=1_000.0, y_um=500.0, z_wide_um=100.0)
        with (
            patch.object(_info, "selected_job_name", return_value="Overview"),
            patch.object(_info, "output_root", return_value=Path("/runs")),
            patch.object(_readers, "ping", return_value=True),
            patches[0],
            patches[1],
            patches[2],
            patches[3],
        ):
            info = adapter.get_info(h)

        # In the frame: the envelope shifted by the origin.
        self.assertEqual(info["canvas"]["x_um"], [-1_000.0, 119_000.0])
        self.assertEqual(info["canvas"]["y_um"], [-500.0, 79_500.0])
        # z follows the focus, so the z-wide travel is widened by the z-galvo's.
        self.assertEqual(info["canvas"]["z_um"], [-150.0, 9_950.0])

        checks = info["connection_status"]
        self.assertEqual(checks["api"], "answering")
        self.assertNotIn(
            "autosave", checks
        )  # captures come from LAS X's cache; AutoSave is not used
        self.assertIn("um", checks["stage"])
        self.assertEqual(checks["output root"], str(Path("/runs")))
        self.assertNotIn("output_root", info)
        self.assertNotIn("failed", checks["limits"])

        h = _handle(connection={**adapter.CONNECTION, "output_root": "/tmp/out"})
        calls = {}

        def fake_capture(client, job, **kwargs):
            calls["captured"] = job
            return _a_capture(job)

        def fake_save(client, acq, output_root, naming, **kwargs):
            calls["saved"] = (str(output_root), naming)
            calls["lineage"] = kwargs.get("lineage")
            calls["state"] = kwargs.get("state")
            return SimpleNamespace(
                image_paths={0: Path("/tmp/out/img.ome.tif")},
                naming=naming,
            )

        patches = _patch_position(job="HiRes")
        with (
            patch.object(_readers, "get_jobs", return_value=self._jobs()),
            patch.object(_readers, "get_hardware_info", return_value={}),
            patch.object(
                _motion,
                "correct_backlash",
                lambda client, **k: {"success": True, "confirmed": True},
            ),
            patch.object(_capture, "acquire", fake_capture),
            patch.object(_save, "save", fake_save),
            patches[0],
            patches[1],
            patches[2],
            patches[3],
        ):
            record = adapter.acquire(
                h,
                acquisition_type="acquisition",
                output_folder=None,
                position_label="prescan_a1b2c3_well-7",
                acquisition_settings={"trigger": "full"},
            )
        # The selected job is what runs: choosing it is set_state's, not acquire's.
        self.assertEqual(calls["captured"], "HiRes")
        saved_root, naming = calls["saved"]
        self.assertEqual(
            Path(saved_root), Path("/tmp/out").resolve() / ".staging" / h.hash6
        )  # OS-agnostic separators
        # Every file name is the acquisition type, then the label as the workflow gave it.
        self.assertEqual(naming.stem, "acquisition_prescan_a1b2c3_well-7")
        self.assertEqual(calls["lineage"]["position_label"], "prescan_a1b2c3_well-7")
        self.assertEqual(calls["lineage"]["stem"], "acquisition_prescan_a1b2c3_well-7")
        self.assertEqual(calls["lineage"]["session_hash6"], h.hash6)
        # the machine/software state is captured and threaded into save()
        self.assertIsInstance(calls["state"], dict)
        self.assertEqual(
            calls["state"]["provenance"]["stem"], "acquisition_prescan_a1b2c3_well-7"
        )
        self.assertEqual(record["settle"], "direct")
        self.assertEqual(record["trigger"], "full")
        self.assertNotIn("folder", record)
        self.assertEqual(
            [Path(p) for p in record["files"]],
            [Path("/tmp/out/img.ome.tif"), *map(Path, record["log"])],
        )
        self.assertNotIn("images", record)
        self.assertEqual(
            record["planes"],
            [
                {
                    "t": 0,
                    "z": 0,
                    "c": 0,
                    "path": str(Path("/tmp/out/img.ome.tif")),
                    "x_um": 0.0,
                    "y_um": 0.0,
                    "z_um": 0.0,
                }
            ],
        )

    def test_files_lists_every_file_the_acquisition_saved(self):
        """The images, the state printed beside them and the vendor's metadata, under one name.

        ``files`` is the name the ZMART Controller's contract fixes, so a
        workflow finds what was saved on any microscope; the controller's own
        check confirms the answer fits.
        """
        from zmart_controller import check_acquire_answer

        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            image = out / "img_c0.ome.tif"
            state = out / "metadata" / "ZMART_state" / "img.json"
            vendor = out / "MetaData" / "img.xlif"
            for path in (image, state, vendor):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"saved")
            h = _handle(connection={**adapter.CONNECTION, "output_root": str(out)})

            def fake_save(client, acq, output_root, naming, **kwargs):
                return SimpleNamespace(
                    image_paths={0: image},
                    naming=naming,
                    vendor_metadata_paths=(vendor,),
                    state_paths=(state,),
                )

            patches = _patch_position(job="Overview")
            with (
                patch.object(_readers, "get_jobs", return_value=self._jobs()),
                patch.object(_readers, "get_hardware_info", return_value={}),
                patch.object(
                    _commands, "select_job", lambda *a, **k: {"success": True}
                ),
                patch.object(
                    _motion,
                    "correct_backlash",
                    lambda client, **k: {"success": True, "confirmed": True},
                ),
                patch.object(
                    _capture,
                    "acquire",
                    lambda client, job, **k: _a_capture(job),
                ),
                patch.object(_save, "save", fake_save),
                # This answer is checked for files that exist: the log is written.
                patch.object(_log, "write", _WRITE_THE_LOG),
                patches[0],
                patches[1],
                patches[2],
                patches[3],
            ):
                record = adapter.acquire(
                    h,
                    acquisition_type="acquisition",
                    output_folder=None,
                    position_label="well-7",
                )

            self.assertEqual(
                record["files"], [str(image), str(vendor), str(state), *record["log"]]
            )
            self.assertEqual(
                check_acquire_answer({"success": True, "content": record}), []
            )

    def test_saving_follows_this_microscopes_profile(self):
        """The format, the projection and letting go of the cache are set up once.

        They are this microscope's ``IMAGE_SAVE`` profile, the same for every
        capture, never a choice made per capture.
        """
        h = _handle()
        h.driven_to = {"x": 0.0, "y": 0.0, "z": 0.0, "z_wide_um": 0.0}
        with self._capturing() as calls:
            record = adapter.acquire(
                h,
                acquisition_type="acquisition",
                output_folder=None,
                position_label="A1",
            )
        self.assertNotIn(
            "source", calls["save"]
        )  # always LAS X's cache: nothing to choose
        self.assertEqual(calls["save"]["format"], _acquisition.IMAGE_SAVE.format)
        self.assertEqual(
            calls["save"]["projection"], _acquisition.IMAGE_SAVE.projection
        )
        self.assertIs(
            calls["save"]["cleanup_cache"], _acquisition.IMAGE_SAVE.cleanup_cache
        )
        self.assertEqual(record["format"], _acquisition.IMAGE_SAVE.format)

    def test_the_capture_is_saved_in_the_output_folder_it_is_given(self):
        """The driver writes the files itself (from LAS X's cache), so it saves where it is told.

        The output folder replaces the session's staging folder under the
        data location; nothing else about the capture changes.
        """
        h = _handle()
        h.driven_to = {"x": 0.0, "y": 0.0, "z": 0.0, "z_wide_um": 0.0}
        with self._capturing(output_root=None) as calls:
            adapter.acquire(
                h,
                position_label="A1",
                acquisition_type="acquisition",
                output_folder="/runs/run-1",
            )
        self.assertEqual(calls["output_root"], Path("/runs/run-1"))

    def test_the_acquisition_type_names_the_files_and_comes_back(self):
        """The stem is the acquisition type, then the label; the log and the answer say the type."""
        h = _handle()
        h.driven_to = {"x": 0.0, "y": 0.0, "z": 0.0, "z_wide_um": 0.0}
        with self._capturing() as calls:
            record = adapter.acquire(
                h,
                position_label="well-7",
                acquisition_type="overview",
                output_folder=None,
            )
        self.assertEqual(calls["save"]["lineage"]["stem"], "overview_well-7")
        self.assertEqual(calls["save"]["lineage"]["acquisition_type"], "overview")
        self.assertEqual(calls["log"]["acquisition_type"], "overview")
        self.assertEqual(record["acquisition_type"], "overview")
        self.assertEqual(record["position_label"], "well-7")

    def test_letting_go_of_the_cache_follows_the_profile(self):
        """A capture read from LAS X's cache is let go of when the profile says so, never per capture."""
        h = _handle()
        h.driven_to = {"x": 0.0, "y": 0.0, "z": 0.0, "z_wide_um": 0.0}
        releasing = replace(_acquisition.IMAGE_SAVE, cleanup_cache=True)
        with (
            patch.object(_acquisition, "IMAGE_SAVE", releasing),
            self._capturing() as calls,
        ):
            adapter.acquire(
                h,
                acquisition_type="acquisition",
                output_folder=None,
                position_label="A1",
            )
        self.assertIs(calls["save"]["cleanup_cache"], True)

    def test_a_capture_that_will_be_released_has_the_canvas_shown_first(self):
        """The canvas is shown while LAS X is idle, before the capture, so the release can clear it after."""
        h = _handle()
        h.driven_to = {"x": 0.0, "y": 0.0, "z": 0.0, "z_wide_um": 0.0}
        order = []
        releasing = replace(_acquisition.IMAGE_SAVE, cleanup_cache=True)
        with (
            patch.object(_acquisition, "IMAGE_SAVE", releasing),
            self._capturing(),
            patch.object(_release, "prepare", lambda: order.append("canvas shown")),
            patch.object(
                _capture,
                "acquire",
                lambda c, j, **k: order.append("captured") or _a_capture(j),
            ),
        ):
            adapter.acquire(
                h,
                acquisition_type="acquisition",
                output_folder=None,
                position_label="A1",
            )
        self.assertEqual(order, ["canvas shown", "captured"])

    def test_a_capture_that_is_kept_leaves_the_screen_alone(self):
        h = _handle()
        h.driven_to = {"x": 0.0, "y": 0.0, "z": 0.0, "z_wide_um": 0.0}
        prepared = []
        keeping = replace(_acquisition.IMAGE_SAVE, cleanup_cache=False)
        with (
            patch.object(_acquisition, "IMAGE_SAVE", keeping),
            self._capturing(),
            patch.object(_release, "prepare", lambda: prepared.append(1)),
        ):
            adapter.acquire(
                h,
                acquisition_type="acquisition",
                output_folder=None,
                position_label="A1",
            )
        self.assertEqual(prepared, [])

    def test_captures_come_from_the_cache_and_are_let_go_of_by_default(self):
        """Every capture is read from LAS X's cache (AutoSave is not used, Thom, 2026-10-14),
        and LAS X lets go of it once saved (on since the STELLARIS, 2026-10-15)."""
        self.assertFalse(hasattr(ImageSaveProfile(), "source"))
        self.assertIs(ImageSaveProfile().cleanup_cache, True)
        self.assertIs(ImageSaveProfile(cleanup_cache=False).cleanup_cache, False)

    def test_the_capture_is_placed_where_the_answer_says_its_planes_are(self):
        """The store is placed where the answer says its planes are."""
        h = _handle()
        h.driven_to = {
            "x": 10.0,
            "y": 20.0,
            "z": 100.0,
            "z_wide_um": 1000.0,
            "z_galvo_um": 0.0,
        }
        with (
            self._capturing() as calls,
            patch.object(
                _readers,
                "get_job_settings",
                return_value={
                    "stack": {
                        "begin": -10.0,
                        "end": 10.0,
                        "sections": 3,
                        "mode": "z-galvo",
                    }
                },
            ),
        ):
            adapter.acquire(
                h,
                acquisition_type="acquisition",
                output_folder=None,
                position_label="A1",
            )
            where = calls["save"]["placement"]([0, 1, 2])
            placed = [where(z) for z in (0, 2)]

        self.assertEqual(
            placed,
            [
                {"x_um": 10.0, "y_um": 20.0, "z_um": 90.0},
                {"x_um": 10.0, "y_um": 20.0, "z_um": 110.0},
            ],
        )

    def test_the_projection_is_asked_for_and_listed_with_the_files(self):
        h = _handle()
        h.driven_to = {
            "x": 0.0,
            "y": 0.0,
            "z": 0.0,
            "z_wide_um": 0.0,
            "z_galvo_um": 0.0,
        }
        projection = Path("/tmp/out/projections/A1.ome.zarr")
        with self._capturing(projections=[projection]) as calls:
            record = adapter.acquire(
                h,
                acquisition_type="acquisition",
                output_folder=None,
                position_label="A1",
            )

        self.assertEqual(
            calls["save"]["projection"], _acquisition.IMAGE_SAVE.projection
        )
        self.assertEqual(record["projections"], [str(projection)])
        self.assertIn(str(projection), record["files"])

    def test_every_capture_leaves_a_log_of_what_was_asked_and_done(self):
        h = _handle()
        h.driven_to = {
            "x": 5.0,
            "y": 6.0,
            "z": 7.0,
            "z_wide_um": 0.0,
            "z_galvo_um": 0.0,
        }
        image = Path("/tmp/out/.staging") / h.hash6 / "data" / "img.ome.tif"
        with self._capturing(image_paths={0: image}) as calls:
            record = adapter.acquire(
                h,
                acquisition_type="acquisition",
                output_folder=None,
                position_label="A1",
                acquisition_settings={"trigger": "full"},
            )

        entry = calls["log"]
        self.assertEqual(entry["asked"], {"trigger": "full"})
        self.assertEqual(entry["settings"], {"trigger": "full"})
        self.assertEqual(entry["stem"], "acquisition_A1")
        self.assertIn("started", entry["capture"])
        self.assertIn("finished", entry["capture"])
        self.assertEqual(
            [
                {k: v for k, v in plane.items() if k != "path"}
                for plane in entry["planes"]
            ],
            [
                {k: v for k, v in plane.items() if k != "path"}
                for plane in record["planes"]
            ],
        )
        # Paths from the run's start, so the log stays true when the run is moved.
        self.assertEqual(entry["planes"][0]["path"], "data/img.ome.tif")
        self.assertEqual(entry["files"], ["data/img.ome.tif"])
        self.assertEqual(entry["unconfirmed"], [])
        [log] = record["log"]
        self.assertTrue(log.endswith(".log.json"))
        self.assertIn(log, record["files"])
        self.assertNotIn(
            log, entry["files"], "the log lists what the capture saved, not itself"
        )

    def test_a_plane_says_where_on_the_sample_it_was_taken(self):
        """The place the last confirmed move put the stage, in the frame.

        A client cannot work this out for itself: a saved OME-TIFF says how
        large a pixel is and nothing about where it was taken. So the
        acquisition reports it, being the only thing that knows -- and it
        reports the frame :func:`set_xyz` takes, so a position handed back can
        be handed straight back in.
        """
        h = _handle()
        h.driven_to = {"x": 1200.0, "y": -450.0, "z": 33.5, "z_wide_um": 900.0}
        with self._capturing() as calls:
            record = adapter.acquire(
                h,
                acquisition_type="acquisition",
                output_folder=None,
                position_label="A1",
            )

        del calls
        self.assertEqual(
            [(p["x_um"], p["y_um"], p["z_um"]) for p in record["planes"]],
            [(1200.0, -450.0, 33.5)],
        )

    def test_the_slices_of_a_stack_are_spread_about_where_the_drive_stands(self):
        """A stack is taken around the drive, so its planes are not all at it.

        The job's own block says where its slices sit; the middle of that
        stack is where the stage is standing. Reporting every plane at the
        drive's height would put a whole sweep on one plane, and the height a
        focus routine chose would then be the height it was handed.
        """
        h = _handle()
        h.driven_to = {"x": 0.0, "y": 0.0, "z": 100.0, "z_wide_um": 1000.0}
        planes = {i: Path(f"/tmp/out/img_z{i}.ome.tif") for i in range(5)}
        with (
            self._capturing(image_paths=planes) as calls,
            patch.object(
                _readers,
                "get_job_settings",
                return_value={
                    "stack": {
                        "begin": 990.0,
                        "end": 1010.0,
                        "sections": 5,
                        "mode": "z-wide",
                    }
                },
            ),
        ):
            record = adapter.acquire(
                h,
                acquisition_type="acquisition",
                output_folder=None,
                position_label="A1",
            )

        del calls
        # 5 slices over 20 um about the z-wide the drive was realized at.
        self.assertEqual(
            [p["z_um"] for p in record["planes"]], [90.0, 95.0, 100.0, 105.0, 110.0]
        )
        self.assertEqual({p["x_um"] for p in record["planes"]}, {0.0})

    def test_a_stack_that_does_not_straddle_the_drive_is_placed_where_it_is(self):
        """Nothing assumes the drive stands in the middle of its own stack.

        A job whose stack begins at the current plane and climbs is perfectly
        ordinary, and a run that split it evenly either side would report every
        plane half a stack away from where it was taken.
        """
        h = _handle()
        h.driven_to = {"x": 0.0, "y": 0.0, "z": 100.0, "z_wide_um": 1000.0}
        planes = {i: Path(f"/tmp/out/img_z{i}.ome.tif") for i in range(3)}
        with (
            self._capturing(image_paths=planes),
            patch.object(
                _readers,
                "get_job_settings",
                return_value={
                    "stack": {
                        "begin": 1000.0,
                        "end": 1020.0,
                        "sections": 3,
                        "mode": "z-wide",
                    }
                },
            ),
        ):
            record = adapter.acquire(
                h,
                acquisition_type="acquisition",
                output_folder=None,
                position_label="A1",
            )

        self.assertEqual([p["z_um"] for p in record["planes"]], [100.0, 110.0, 120.0])

    def _heights(self, h, planes, stack):
        with (
            self._capturing(image_paths=planes),
            patch.object(
                _readers,
                "get_job_settings",
                return_value={"stack": stack},
            ),
        ):
            record = adapter.acquire(
                h,
                acquisition_type="acquisition",
                output_folder=None,
                position_label="A1",
            )
        return [(p["c"], p["z"], p["z_um"]) for p in record["planes"]]

    def test_a_stack_made_by_the_galvo_is_measured_from_the_galvo(self):
        """A z-galvo stack's slices are galvo heights, not z-wide ones.

        Measured from z-wide they came out a whole z-wide away (here 1000 um
        below the sample). The galvo stood at 0 when the drive arrived, so
        a slice at -10 um is 10 um below where the drive stood.
        """
        h = _handle()
        h.driven_to = {
            "x": 0.0,
            "y": 0.0,
            "z": 100.0,
            "z_wide_um": 1000.0,
            "z_galvo_um": 0.0,
        }
        planes = {
            PlaneIndex(t=0, z=i, c=0): Path(f"/tmp/out/img_z{i}.ome.tif")
            for i in range(5)
        }

        heights = self._heights(
            h, planes, {"begin": -10.0, "end": 10.0, "sections": 5, "mode": "z-galvo"}
        )

        self.assertEqual(
            [z_um for _c, _z, z_um in heights], [90.0, 95.0, 100.0, 105.0, 110.0]
        )

    def test_a_galvo_stack_is_measured_from_where_the_galvo_stood(self):
        h = _handle()
        h.driven_to = {
            "x": 0.0,
            "y": 0.0,
            "z": 100.0,
            "z_wide_um": 1000.0,
            "z_galvo_um": 5.0,
        }
        planes = {
            PlaneIndex(t=0, z=i, c=0): Path(f"/tmp/out/img_z{i}.ome.tif")
            for i in range(3)
        }

        heights = self._heights(
            h, planes, {"begin": -10.0, "end": 10.0, "sections": 3, "mode": "z-galvo"}
        )

        self.assertEqual([z_um for _c, _z, z_um in heights], [85.0, 95.0, 105.0])

    def test_every_channel_of_a_plane_is_at_that_plane(self):
        """Two channels and three planes are six files, but three heights.

        Counting files against the stack's sections found 6 against 3, gave
        up, and put every plane at the drive's height.
        """
        h = _handle()
        h.driven_to = {
            "x": 0.0,
            "y": 0.0,
            "z": 100.0,
            "z_wide_um": 1000.0,
            "z_galvo_um": 0.0,
        }
        planes = {
            PlaneIndex(t=0, z=z, c=c): Path(f"/tmp/out/img_z{z}_c{c}.ome.tif")
            for z in range(3)
            for c in range(2)
        }

        heights = self._heights(
            h, planes, {"begin": 1000.0, "end": 1020.0, "sections": 3, "mode": "z-wide"}
        )

        self.assertEqual(
            sorted(heights),
            [
                (0, 0, 100.0),
                (0, 1, 110.0),
                (0, 2, 120.0),
                (1, 0, 100.0),
                (1, 1, 110.0),
                (1, 2, 120.0),
            ],
        )

    def test_a_stack_that_does_not_say_its_drive_is_refused(self):
        """Which drive made the stack decides what its slices mean; unsaid, the capture fails."""
        h = _handle()
        h.driven_to = {
            "x": 0.0,
            "y": 0.0,
            "z": 100.0,
            "z_wide_um": 1000.0,
            "z_galvo_um": 0.0,
        }
        planes = {
            PlaneIndex(t=0, z=i, c=0): Path(f"/tmp/out/img_z{i}.ome.tif")
            for i in range(3)
        }

        with self.assertRaisesRegex(RuntimeError, "cannot be placed"):
            self._heights(h, planes, {"begin": 1000.0, "end": 1020.0, "sections": 3})

    def test_a_stack_swept_downwards_counts_upwards(self):
        """Depth 0 is the lowest slice, as in the store, whichever way LAS X swept."""
        h = _handle()
        h.driven_to = {
            "x": 0.0,
            "y": 0.0,
            "z": 100.0,
            "z_wide_um": 1000.0,
            "z_galvo_um": 0.0,
        }
        planes = {
            PlaneIndex(t=0, z=i, c=0): Path(f"/tmp/out/img_z{i}.ome.tif")
            for i in range(3)
        }

        heights = self._heights(
            h, planes, {"begin": 1020.0, "end": 1000.0, "sections": 3, "mode": "z-wide"}
        )

        self.assertEqual(
            sorted((z, z_um) for _c, z, z_um in heights),
            [(0, 100.0), (1, 110.0), (2, 120.0)],
        )

    def test_a_capture_before_the_stage_was_read_is_refused(self):
        """Every plane says where it is; with no position read or set yet, there is none to say."""
        h = _handle(driven_to=None)
        with (
            self._capturing(),
            self.assertRaisesRegex(RuntimeError, "where the stage is"),
        ):
            adapter.acquire(
                h,
                acquisition_type="acquisition",
                output_folder=None,
                position_label="A1",
            )

    def test_acquire_applies_the_rigs_measured_orientation(self):
        """The microscope's measured turn reaches ``save``, so saved planes are
        already lined up with the stage.

        This is the safety seam the whole orientation feature rests on. The
        ``set_orientation`` notebook measures how the camera is turned relative to
        the stage and publishes it into the microscope's ProgramData snapshot,
        next to its calibration and limits. Every acquire must read that turn and
        hand it to ``save`` so the images that land on disk are stage-aligned. If
        this one wire were dropped, all the other tests would still pass, yet the
        machine would quietly save quarter-turned pictures -- and the workflow
        would then chase every feature the wrong way. Here we publish a measured
        90-degree turn into a hermetic snapshot and prove it arrives at ``save``.
        """
        from datetime import datetime, timezone

        from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.configuration.store import (
            MachineProfile,
        )

        h = _handle(connection={**adapter.CONNECTION, "output_root": "/tmp/out"})
        seen = {}

        def fake_save(client, acq, output_root, naming, **kwargs):
            seen["orientation"] = kwargs.get("orientation")
            return SimpleNamespace(
                image_paths={0: Path("/tmp/out/img.ome.tif")}, naming=naming
            )

        patches = _patch_position(job="Overview")
        with tempfile.TemporaryDirectory() as tmp:
            machine = MachineProfile(programdata_root=Path(tmp) / "programdata")
            machine.publish_snapshot(
                datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
                orientation={"schema_version": 1, "rotate_deg": 90},
            )
            with (
                patch.object(_machine, "MACHINE", machine),
                patch.object(_readers, "get_jobs", return_value=self._jobs()),
                patch.object(_readers, "get_hardware_info", return_value={}),
                patch.object(
                    _commands,
                    "select_job",
                    lambda client, job, **k: {"success": True},
                ),
                patch.object(
                    _motion,
                    "correct_backlash",
                    lambda client, **k: {"success": True, "confirmed": True},
                ),
                patch.object(
                    _capture,
                    "acquire",
                    lambda client, job, **k: _a_capture(job),
                ),
                patch.object(_save, "save", fake_save),
                patches[0],
                patches[1],
                patches[2],
                patches[3],
            ):
                adapter.acquire(
                    h,
                    acquisition_type="acquisition",
                    output_folder=None,
                    position_label="A1",
                )

        # The measured 90-degree turn was read from the snapshot and threaded
        # into save -- not the shipped "no turn" placeholder.
        self.assertEqual(seen["orientation"], _orientation.Orientation(rotate_deg=90))

    def _fake_acquire_env(self, calls):
        """Patches for a fully-mocked acquire; records each save's naming."""

        def fake_save(client, acq, output_root, naming, **kwargs):
            calls.setdefault("namings", []).append(naming)
            return SimpleNamespace(
                image_paths={0: Path("/tmp/out/img.ome.tif")}, naming=naming
            )

        patches = _patch_position(job="Overview")
        return (
            patch.object(_readers, "get_jobs", return_value=self._jobs()),
            patch.object(_readers, "get_hardware_info", return_value={}),
            patch.object(_commands, "select_job", return_value={"success": True}),
            patch.object(
                _motion,
                "correct_backlash",
                side_effect=lambda client, **k: (
                    calls.setdefault("backlash", []).append(k["passes"])
                    or {"success": True, "confirmed": True}
                ),
            ),
            patch.object(_capture, "acquire", lambda client, job, **k: _a_capture(job)),
            patch.object(_save, "save", fake_save),
            patches[0],
            patches[1],
            patches[2],
            patches[3],
        )

    def test_an_unlabelled_capture_is_named_by_the_session_counter(self):
        h = _handle(connection={**adapter.CONNECTION, "output_root": "/tmp/out"})
        calls = {}
        with ExitStack() as standing_in:
            for stand_in in self._fake_acquire_env(calls):
                standing_in.enter_context(stand_in)
            record0 = adapter.acquire(
                h, acquisition_type="acquisition", output_folder=None
            )
            record1 = adapter.acquire(
                h, acquisition_type="acquisition", output_folder=None
            )
        self.assertEqual(calls.get("backlash", []), [])
        self.assertEqual(record0["settle"], "direct")
        self.assertEqual(record0["backlash_rounds"], 0)
        # unlabeled acquires consume the per-session counter, zero-padded
        self.assertEqual(record0["position_label"], "000000")
        self.assertEqual(record1["position_label"], "000001")
        self.assertEqual(
            [naming.stem for naming in calls["namings"]],
            ["acquisition_000000", "acquisition_000001"],
        )

    def test_a_label_already_staged_gets_the_next_free_stem(self):
        """No capture is ever overwritten: the same label again gets ``_001``."""
        with tempfile.TemporaryDirectory() as tmp:
            h = _handle(connection={**adapter.CONNECTION, "output_root": tmp})
            staged = Path(tmp).resolve() / ".staging" / h.hash6 / "data"
            staged.mkdir(parents=True)
            (staged / "acquisition_A1_T000000_C00_Z00000.ome.tiff").write_bytes(
                b"the first capture"
            )
            calls = {}
            with ExitStack() as standing_in:
                for stand_in in self._fake_acquire_env(calls):
                    standing_in.enter_context(stand_in)
                adapter.acquire(
                    h,
                    acquisition_type="acquisition",
                    output_folder=None,
                    position_label="A1",
                )
        self.assertEqual(calls["namings"][0].stem, "acquisition_A1_001")

    def test_without_a_selected_job_nothing_is_captured(self):
        """The job is state: a capture runs the selected one, and refuses when none can be read."""
        h = _handle(connection={**adapter.CONNECTION, "output_root": "/tmp/out"})
        with (
            self._capturing(),
            patch.object(_readers, "get_selected_job", return_value=None),
            self.assertRaisesRegex(RuntimeError, "set_state"),
        ):
            adapter.acquire(
                h,
                acquisition_type="acquisition",
                output_folder=None,
                position_label="A1",
            )

    def test_explicit_label_does_not_consume_counter(self):
        h = _handle(connection={**adapter.CONNECTION, "output_root": "/tmp/out"})
        calls = {}
        env = self._fake_acquire_env(calls)
        with ExitStack() as standing_in:
            for stand_in in env:
                standing_in.enter_context(stand_in)
            adapter.acquire(
                h,
                acquisition_type="acquisition",
                output_folder=None,
                position_label="named",
            )  # must not bump the counter
            record = adapter.acquire(
                h, acquisition_type="acquisition", output_folder=None
            )  # still gets 000000
        self.assertEqual(calls["namings"][0].stem, "acquisition_named")
        self.assertEqual(record["position_label"], "000000")

    def test_acquire_runs_backlash_correction_before_capture_when_enabled(self):
        """The driver's own setting: with rounds set, the slack is taken up right before the capture."""
        h = _handle(connection={**adapter.CONNECTION, "output_root": "/tmp/out"})
        order = []

        def fake_correct_backlash(client, **kwargs):
            order.append(("backlash", client, kwargs["passes"]))
            return {"success": True, "confirmed": True}

        def fake_capture(client, job, **kwargs):
            order.append(("capture", job))
            return _a_capture(job)

        def fake_save(client, acq, output_root, naming, **kwargs):
            return SimpleNamespace(
                image_paths={0: Path("/tmp/out/img.ome.tif")},
                xml_paths={0: Path("/tmp/out/img.xml")},
                naming=naming,
            )

        patches = _patch_position(job="HiRes")
        with (
            patch.object(_acquisition, "ACQUISITION_BACKLASH_DEFAULT_ROUNDS", 2),
            patch.object(_readers, "get_jobs", return_value=self._jobs()),
            patch.object(_motion, "correct_backlash", fake_correct_backlash),
            patch.object(_capture, "acquire", fake_capture),
            patch.object(_save, "save", fake_save),
            patches[2],
        ):
            record = adapter.acquire(
                h,
                acquisition_type="acquisition",
                output_folder=None,
                position_label="7",
            )
        # backlash takeup -> capture, in that order: it pins the slack right
        # before the image is taken.
        self.assertEqual(order, [("backlash", h.client, 2), ("capture", "HiRes")])
        self.assertEqual(record["settle"], "backlash-corrected")
        self.assertEqual(record["backlash_rounds"], 2)

    def test_zero_backlash_rounds_skips_correction(self):
        calls = []
        h = _handle(connection={**adapter.CONNECTION, "output_root": "/tmp/out"})
        patches = _patch_position(job="Overview")
        with (
            patch.object(_readers, "get_jobs", return_value=self._jobs()),
            patch.object(_motion, "correct_backlash", side_effect=calls.append),
            patch.object(
                _capture,
                "acquire",
                return_value=_a_capture("Overview"),
            ),
            patch.object(
                _save,
                "save",
                return_value=SimpleNamespace(image_paths={}, xml_paths={}, naming=None),
            ),
            patches[2],
        ):
            record = adapter.acquire(
                h,
                acquisition_type="acquisition",
                output_folder=None,
                position_label="7",
            )
        self.assertEqual(calls, [])
        self.assertEqual(record["settle"], "direct")
        self.assertEqual(record["backlash_rounds"], 0)

    def _acquire_and_list_the_calls(self, options=None):
        """Run acquire with every step stubbed out; returns the calls it made, in order."""
        h = _handle(connection={**adapter.CONNECTION, "output_root": "/tmp/out"})
        calls = []
        patches = _patch_position(job="Overview")
        with (
            patch.object(_readers, "get_jobs", return_value=self._jobs()),
            patch.object(_commands, "select_job", return_value={"success": True}),
            patch.object(
                _motion,
                "correct_backlash",
                lambda client, **k: {"success": True, "confirmed": True},
            ),
            patch.object(
                _capture,
                "acquire",
                side_effect=lambda client, job, **k: (
                    calls.append("capture") or _a_capture(job)
                ),
            ),
            patch.object(
                _save,
                "save",
                return_value=SimpleNamespace(image_paths={}, xml_paths={}, naming=None),
            ),
            patches[2],
        ):
            adapter.acquire(
                h,
                acquisition_type="acquisition",
                output_folder=None,
                position_label="1",
                acquisition_settings=options,
            )
        return calls

    def test_acquire_only_captures_and_saves(self):
        """Positions are never made in the Navigator Expert, so an acquisition is one capture."""
        self.assertEqual(self._acquire_and_list_the_calls(), ["capture"])

    def test_strip_scan_fields_is_not_an_acquisition_setting(self):
        with self.assertRaisesRegex(
            ValueError, "unknown acquisition setting 'strip_scan_fields'"
        ):
            self._acquire_and_list_the_calls({"strip_scan_fields": False})


class TestStateAndProcedures(unittest.TestCase):
    _HW = {
        "SerialNumber": "STELLARIS-1234",
        "SystemType": "CONFOCAL",
        "Microscope": {
            "name": "DM Manual-6",
            "objectives": [
                {"slotIndex": 0, "objectiveNumber": 506511},
                {"slotIndex": 1, "objectiveNumber": 506513},
            ],
        },
    }

    def _state_patches(self, jobs=("Overview", "HiRes"), af_jobs=("AF Job",)):
        catalog = [
            {"Name": n, "IsSelected": n == "Overview", "IsAutofocus": False}
            for n in jobs
        ] + [{"Name": n, "IsSelected": False, "IsAutofocus": True} for n in af_jobs]
        return (
            patch.object(_readers, "get_hardware_info", return_value=dict(self._HW)),
            patch.object(
                _readers,
                "get_selected_job",
                return_value={"Name": "Overview", "IsSelected": True},
            ),
            patch.object(_readers, "get_jobs", return_value=catalog),
        )

    def test_state_shape_changeable_first_then_observed(self):
        h = _handle()
        p = self._state_patches()
        with (
            p[0] as hardware,
            p[1] as selected,
            p[2] as jobs,
            patch.object(_readers, "get_job_settings", return_value={}) as settings,
        ):
            state = adapter.get_state(h)
        self.assertEqual(list(state), ["changeable", "observed"])  # changeable first
        self.assertEqual(state["changeable"], {"job": "Overview"})
        self.assertNotIn("mode", hardware.call_args.kwargs)
        self.assertNotIn("mode", selected.call_args.kwargs)
        self.assertNotIn("mode", jobs.call_args.kwargs)
        self.assertNotIn("mode", settings.call_args.kwargs)
        observed = state["observed"]
        self.assertEqual(observed["vendor"], "leica")
        self.assertEqual(observed["microscope"], "stellaris5-y42h93")
        self.assertEqual(observed["serial_number"], "STELLARIS-1234")
        self.assertEqual(observed["system_type"], "CONFOCAL")
        self.assertEqual(observed["stand"], "DM Manual-6")
        self.assertEqual(observed["objectives"], [[0, 506511], [1, 506513]])
        # the rich readings ride along: the full selected-job record + catalog,
        # with autofocus jobs as their own category
        self.assertEqual(observed["job"]["Name"], "Overview")
        self.assertEqual([j["Name"] for j in observed["jobs"]], ["Overview", "HiRes"])
        self.assertEqual([j["Name"] for j in observed["autofocus_jobs"]], ["AF Job"])

    def test_state_reports_current_xy_pixel_size(self):
        h = _handle()
        p = self._state_patches()
        settings = {
            "imageSize": "100 um x 80 um",
            "pixelSize": "0.10 um x 0.20 um",
            "format": "1000 x 400",
        }
        with (
            p[0],
            p[1],
            p[2],
            patch.object(_readers, "get_job_settings", return_value=settings),
        ):
            observed = adapter.get_state(h)["observed"]
        self.assertEqual(
            observed["pixel_size"],
            {"x": 0.1, "y": 0.2, "unit": "um"},
        )

    def test_set_state_refuses_an_autofocus_job(self):
        h = _handle()
        p = self._state_patches()
        with p[0], p[1], p[2], patch.object(_commands, "select_job") as select:
            with self.assertRaisesRegex(ValueError, "autofocus"):
                adapter.set_state(h, {"changeable": {"job": "AF Job"}})
        select.assert_not_called()

    def test_set_state_applies_changeable_ignoring_observed(self):
        h = _handle()
        p = self._state_patches()
        with (
            p[0],
            p[1],
            p[2],
            patch.object(
                _commands, "select_job", return_value={"success": True}
            ) as select,
        ):
            captured = adapter.get_state(h)
            captured["changeable"]["job"] = "HiRes"
            # observed is a report, never an instruction: even a wildly
            # mismatching observed part does not block the apply.
            captured["observed"]["serial_number"] = "SOMETHING-ELSE"
            result = adapter.set_state(h, captured)
            self.assertEqual(result["applied"], {"job": "HiRes"})
            select.assert_called_once()

    def test_set_state_refuses_a_job_that_no_longer_exists(self):
        h = _handle()
        p = self._state_patches(jobs=("Overview",))
        with p[0], p[1], p[2], patch.object(_commands, "select_job") as select:
            with self.assertRaisesRegex(ValueError, "no longer exists"):
                adapter.set_state(h, {"changeable": {"job": "Gone"}})
        select.assert_not_called()

    def test_procedures(self):
        h = _handle()
        p = self._state_patches()
        with p[2]:
            procedures = adapter.get_procedures(h)
        self.assertIn("backlash_takeup", procedures)
        self.assertNotIn("get_root", procedures)
        self.assertNotIn("get_positions", procedures)
        self.assertNotIn("get_focus_points", procedures)
        self.assertEqual(procedures["autofocus"]["jobs"], ["AF Job"])
        with patch.object(
            _motion,
            "correct_backlash",
            lambda client, **k: {"success": True, "confirmed": True},
        ):
            self.assertEqual(
                adapter.run_procedure(h, {"name": "backlash_takeup"})["ran"]["name"],
                "backlash_takeup",
            )
        with self.assertRaises(ValueError):
            adapter.run_procedure(h, {"name": "nope"})

    def test_autofocus_runs_capture_only_and_restores_the_selection(self):
        from types import SimpleNamespace

        h = _handle()
        calls = []
        p = self._state_patches()
        position = _patch_position(z_wide_um=42.0, z_galvo_um=1.5)
        with (
            p[2],  # job catalog (AF Job flagged)
            position[0],
            position[1],
            position[2],
            position[3],
            patch.object(
                _commands,
                "select_job",
                side_effect=lambda client, job, **k: (
                    calls.append(("select", job)) or {"success": True}
                ),
            ),
            patch.object(
                _capture,
                "acquire",
                side_effect=lambda client, job, **k: (
                    calls.append(("acquire", job))
                    or SimpleNamespace(job=job, started_at=1.0, finished_at=3.5)
                ),
            ),
        ):
            result = adapter.run_procedure(
                h, {"name": "autofocus"}
            )  # single AF job: no arg
        # select AF -> capture -> restore the original selection, in order
        self.assertEqual(
            calls, [("select", "AF Job"), ("acquire", "AF Job"), ("select", "Overview")]
        )
        self.assertEqual(result["ran"], "autofocus")
        self.assertEqual(result["job"], "AF Job")
        self.assertEqual(result["focus_um"], 43.5)  # 42.0 + 1.5, read before restore
        self.assertEqual(result["frame_z_um"], 43.5)  # all-zero origin
        self.assertEqual(result["duration_s"], 2.5)

    def test_autofocus_runs_on_its_autofocus_job(self):
        h = _handle()
        p = self._state_patches()
        position = _patch_position()
        with (
            p[2],
            position[0],
            position[1],
            position[2],
            position[3],
            patch.object(_commands, "select_job", return_value={"success": True}),
            patch.object(
                _capture,
                "acquire",
                return_value=SimpleNamespace(
                    job="AF Job", started_at=0.0, finished_at=1.0
                ),
            ),
        ):
            result = adapter.run_procedure(h, {"name": "autofocus"})
        self.assertEqual(result["ran"], "autofocus")

    def test_autofocus_rejects_a_normal_job(self):
        h = _handle()
        p = self._state_patches()
        with p[2]:
            with self.assertRaisesRegex(ValueError, "not an autofocus job"):
                adapter.run_procedure(h, {"name": "autofocus", "job": "Overview"})

    def test_autofocus_requires_a_choice_when_several_exist(self):
        h = _handle()
        p = self._state_patches(af_jobs=("AF Job", "AF Fine"))
        with p[2]:
            with self.assertRaisesRegex(ValueError, "multiple autofocus jobs"):
                adapter.run_procedure(h, {"name": "autofocus"})

    def test_autofocus_without_af_jobs_is_a_clear_error(self):
        h = _handle()
        p = self._state_patches(af_jobs=())
        with p[2]:
            with self.assertRaisesRegex(RuntimeError, "no autofocus job"):
                adapter.run_procedure(h, {"name": "autofocus"})


class TestConnectCardCalibrationLine(unittest.TestCase):
    """The Connect card says whether the objective calibration is usable.

    The session holds a calibration record even when loading failed, so the
    line has to come from what the record says was loaded, not from whether a
    record exists.
    """

    def test_a_loaded_calibration_shows_its_name(self):
        record = {"enabled": True, "loaded": True, "name": "water_lens_setup"}

        self.assertEqual(_instrument._calibration_status(record), "water_lens_setup")

    def test_a_loaded_calibration_without_a_name_says_found(self):
        record = {"enabled": True, "loaded": True, "name": None}

        self.assertEqual(_instrument._calibration_status(record), "found")

    def test_a_calibration_that_failed_to_load_says_so_even_when_it_has_a_name(self):
        record = {"enabled": True, "loaded": False, "name": "water_lens_setup"}

        line = _instrument._calibration_status(record)

        self.assertTrue(line.startswith("failed"))
        self.assertIn("objective changes are refused", line)

    def test_a_calibration_that_was_switched_off_says_so(self):
        record = {"enabled": False, "loaded": False, "name": None}

        self.assertEqual(_instrument._calibration_status(record), "switched off")

    def test_no_record_at_all_says_none_found(self):
        self.assertEqual(_instrument._calibration_status(None), "failed — none found")


class TestGetInfoReportsNoPositions(unittest.TestCase):
    """get_info reports no tile or focus positions.

    Positions are never made in the Navigator Expert, so there are none to report.
    """

    def test_get_info_reports_no_positions(self):
        h = _handle()
        h.connection["output_root"] = "/tmp/zmart-test"
        with (
            patch.object(_info, "selected_job_name", return_value="Overview"),
        ):
            info = adapter.get_info(h)
        self.assertNotIn("tile_positions", info)
        self.assertNotIn("focus_positions", info)
        self.assertEqual(info["selected_job"], "Overview")


class TestObjectiveCompensation(unittest.TestCase):
    """Cross-objective frame math: ΔT = T[current] − T[origin's objective].

    Uses the EXISTING calibration translation totals (no schema change,
    operator decision 2026-07-02); the driver assumes x/y apply to the motoric
    stage and objective-translation z applies through z-wide.
    """

    def setUp(self):
        _wide_limits()

    def tearDown(self):
        _clear_limits()

    def _cross_handle(self):
        return _handle(
            origin=_origin(
                x_um=1000.0,
                y_um=2000.0,
                z_wide_um=30.0,
                z_galvo_um=0.0,
                z_focus_um=30.0,
                objective={"name": "10x", "slotIndex": 1},
            ),
            translations={1: (0.0, 0.0, 0.0), 2: (100.0, 50.0, 10.0)},
        )

    def test_cross_objective_read_applies_translation(self):
        h = self._cross_handle()
        patches = _patch_position(
            x_um=1110.0, y_um=2060.0, z_wide_um=45.0, z_galvo_um=0.0, slot=2
        )
        with patches[0], patches[1], patches[2], patches[3]:
            pos = adapter.get_xyz(h)
        self.assertEqual(pos["x"]["position"], 10.0)  # 1110 − 1000 − 100
        self.assertEqual(pos["y"]["position"], 10.0)  # 2060 − 2000 − 50
        self.assertEqual(pos["z"]["position"], 5.0)  # 45 − 30 − 10
        # The motors' readings stay untranslated: the stage's own numbers.
        self.assertEqual(pos["x"]["actuators"], {"motoric": 1110.0})
        self.assertEqual(pos["z"]["actuators"], {"z-wide": 45.0, "z-galvo": 0.0})
        self.assertEqual(pos["objective_translation_um"], [100.0, 50.0, 10.0])

    def test_cross_objective_move_targets_include_translation(self):
        h = self._cross_handle()
        moves = {"z": []}

        def fake_xy(client, x_um, y_um, **kwargs):
            moves["xy"] = (x_um, y_um)
            return {"success": True, "confirmed": True}

        def fake_z(client, job, z, unit="um", z_mode="galvo", **kwargs):
            moves["z"].append((z, z_mode))
            return {"success": True, "confirmed": True}

        patches = _patch_position(z_wide_um=40.0, z_galvo_um=0.0, slot=2)
        with (
            patch.object(_motion, "arrive_xy", fake_xy),
            patch.object(_commands, "move_z", fake_z),
            patches[0],
            patches[1],
            patches[2],
            patches[3],
        ):
            record = adapter.set_xyz(
                h, 10.0, 10.0, 5.0, with_actuators={"z": "z-galvo"}
            )
        self.assertEqual(moves["xy"], (1110.0, 2060.0))  # ref + F + ΔT
        # ΔT.z=10 is pinned absolutely on z-wide (30+10), while the
        # ordinary requested frame z=5 is realized by z-galvo (0+5).
        self.assertEqual(moves["z"], [(40.0, "zwide"), (5.0, "galvo")])
        self.assertEqual(record["objective_translation_um"], [100.0, 50.0, 10.0])
        # The answer is read back from the (patched) microscope, like get_xyz.
        self.assertEqual(record["z"]["actuators"], {"z-wide": 40.0, "z-galvo": 0.0})
        self.assertEqual(record["z"]["position"], 0.0)  # 40 − 30 − 10

    def test_job_change_that_swaps_the_objective_is_compensated(self):
        """A Navigator Expert job change can bring a different objective with it.

        The frame math must not assume the objective only changes via the
        turret under one job: every read and move re-reads the selected job
        and takes the objective from that job's settings, so a job switch is
        compensated exactly like a direct objective switch — and the z moves
        address the newly selected job, not the one the origin was set under.
        """
        h = self._cross_handle()  # origin recorded under slot 1
        moves = {"z": []}

        def fake_xy(client, x_um, y_um, **kwargs):
            moves["xy"] = (x_um, y_um)
            return {"success": True, "confirmed": True}

        def fake_z(client, job, z, unit="um", z_mode="galvo", **kwargs):
            moves["z"].append((job, z, z_mode))
            return {"success": True, "confirmed": True}

        # The selected job is now "HiRes", whose settings carry objective
        # slot 2 — the operator switched jobs, not the turret directly.
        patches = _patch_position(
            x_um=1110.0,
            y_um=2060.0,
            z_wide_um=45.0,
            z_galvo_um=0.0,
            job="HiRes",
            slot=2,
        )
        with (
            patch.object(_motion, "arrive_xy", fake_xy),
            patch.object(_commands, "move_z", fake_z),
            patches[0],
            patches[1],
            patches[2],
            patches[3],
        ):
            pos = adapter.get_xyz(h)
            record = adapter.set_xyz(
                h, 10.0, 10.0, 5.0, with_actuators={"z": "z-galvo"}
            )

        # Read: compensated with ΔT taken from the NEW job's objective.
        self.assertEqual(pos["x"]["position"], 10.0)  # 1110 − 1000 − 100
        self.assertEqual(pos["y"]["position"], 10.0)  # 2060 − 2000 − 50
        self.assertEqual(pos["z"]["position"], 5.0)  # 45 − 30 − 10
        # Move: targets include ΔT, and z is driven on the selected job.
        self.assertEqual(moves["xy"], (1110.0, 2060.0))
        self.assertEqual(
            moves["z"], [("HiRes", 40.0, "zwide"), ("HiRes", 5.0, "galvo")]
        )
        self.assertEqual(record["objective_translation_um"], [100.0, 50.0, 10.0])

    def test_cross_objective_galvo_moves_do_not_accumulate_translation_on_zwide(self):
        h = self._cross_handle()
        moves = []

        def fake_z(client, job, z, unit="um", z_mode="galvo", **kwargs):
            moves.append((z, z_mode))
            return {"success": True, "confirmed": True}

        patches = _patch_position(z_wide_um=40.0, z_galvo_um=0.0, slot=2)
        with (
            patch.object(
                _motion,
                "arrive_xy",
                return_value={"success": True, "confirmed": True},
            ),
            patch.object(_commands, "move_z", fake_z),
            patches[0],
            patches[1],
            patches[2],
            patches[3],
        ):
            adapter.set_xyz(h, 0.0, 0.0, 4.0, with_actuators={"z": "z-galvo"})
            adapter.set_xyz(h, 1.0, 1.0, 4.0, with_actuators={"z": "z-galvo"})

        assert moves == [
            (40.0, "zwide"),
            (4.0, "galvo"),
            (40.0, "zwide"),
            (4.0, "galvo"),
        ]

    def test_cross_objective_zwide_translation_refuses_with_the_offset_hint(self):
        """The doomed z-wide leg is refused inside the real move_z (the
        commands layer owns all checking); the adapter's job is only the
        actionable message on top."""
        h = self._cross_handle()
        install_permissive_limits(
            h.client,
            z_wide_um={"min": 0.0, "max": 35.0},
            z_galvo_um={"min": -250.0, "max": 250.0},
        )
        patches = _patch_position(z_wide_um=40.0, z_galvo_um=0.0, slot=2)
        with (
            patch.object(_motion, "arrive_xy") as xy,
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            self.assertRaisesRegex(RuntimeError, "z_wide_um") as ctx,
        ):
            adapter.set_xyz(h, 0.0, 0.0, 4.0, with_actuators={"z": "z-galvo"})
        xy.assert_called_once()  # XY arrived (checked); the z leg then refused
        # The failing leg is the calibrated objective offset, which only
        # z-wide can realize — suggesting the other actuator would send the
        # operator in a circle, so the message must not do that.
        message = str(ctx.exception)
        self.assertIn("calibrated objective offset", message)
        self.assertNotIn("with_actuators", message)

    def test_round_trip_property_across_objectives(self):
        """get_xyz(set_xyz(F)) == F — commanded hardware read back through the frame."""
        h = self._cross_handle()
        state = {"x": 0.0, "y": 0.0, "z_wide": 40.0, "z_galvo": 0.0}

        def fake_xy(client, x_um, y_um, **kwargs):
            state["x"], state["y"] = x_um, y_um
            return {"success": True, "confirmed": True}

        def fake_z(client, job, z, unit="um", z_mode="galvo", **kwargs):
            state["z_galvo" if z_mode == "galvo" else "z_wide"] = z
            return {"success": True, "confirmed": True}

        with (
            patch.object(_motion, "arrive_xy", fake_xy),
            patch.object(_commands, "move_z", fake_z),
            patch.object(
                _readers,
                "get_xy",
                side_effect=lambda client, **kw: {
                    "x_um": state["x"],
                    "y_um": state["y"],
                },
            ),
            patch.object(
                _readers,
                "get_job_settings",
                side_effect=lambda client, job, **kw: _settings(
                    z_wide_um=state["z_wide"], z_galvo_um=state["z_galvo"], slot=2
                ),
            ),
            patch.object(
                _readers,
                "get_selected_job",
                return_value={"Name": "Overview", "IsSelected": True},
            ),
            patch.object(
                _readers_parsing, "make_changeable_copy", side_effect=lambda s: s
            ),
        ):
            moved = adapter.set_xyz(h, 12.0, -7.0, 4.0, with_actuators={"z": "z-galvo"})
            pos = adapter.get_xyz(h)
        self.assertAlmostEqual(pos["x"]["position"], 12.0)
        self.assertAlmostEqual(pos["y"]["position"], -7.0)
        self.assertAlmostEqual(pos["z"]["position"], 4.0)
        # set_xyz answered the same read-back, so no second call is needed;
        # only the motor each answer used differs, as each was asked for.
        self.assertEqual(moved["z"].pop("actuator"), "z-galvo")
        self.assertEqual(pos["z"].pop("actuator"), "z-wide")
        self.assertEqual(moved, pos)

    def test_cross_objective_move_without_translations_refuses(self):
        h = _handle(origin=_origin(objective={"name": "10x", "slotIndex": 1}))
        patches = _patch_position(slot=2)
        with (
            patch.object(_motion, "arrive_xy") as xy,
            patches[0],
            patches[1],
            patches[2],
            patches[3],
        ):
            with self.assertRaisesRegex(RuntimeError, "translation"):
                adapter.set_xyz(h, 1.0, 1.0, 0.0)
        xy.assert_not_called()  # refused before any motion

    def test_cross_objective_read_without_translations_warns_uncompensated(self):
        h = _handle(
            origin=_origin(x_um=1000.0, objective={"name": "10x", "slotIndex": 1})
        )
        patches = _patch_position(x_um=1110.0, slot=2)
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            self.assertLogs(_frame.log, level="WARNING"),
        ):
            pos = adapter.get_xyz(h)
        self.assertEqual(pos["x"]["position"], 110.0)  # uncompensated, but loud

    def test_same_objective_needs_no_calibration(self):
        h = _handle(
            origin=_origin(x_um=1000.0, objective={"name": "63x", "slotIndex": 3})
        )
        patches = _patch_position(x_um=1010.0, slot=3)
        with patches[0], patches[1], patches[2], patches[3]:
            pos = adapter.get_xyz(h)
        self.assertEqual(pos["x"]["position"], 10.0)  # ΔT = 0, translations unused

    def test_out_of_range_galvo_refuses_with_the_actuator_hint(self):
        """An out-of-range galvo target is refused inside the real move_z;
        the adapter adds the try-the-other-actuator hint."""
        h = _handle(
            origin=_origin(
                x_um=10000.0, y_um=10000.0, objective={"name": "63x", "slotIndex": 3}
            )
        )
        patches = _patch_position(z_wide_um=0.0, z_galvo_um=0.0, slot=3)
        with (
            patch.object(_motion, "arrive_xy") as xy,
            patches[0],
            patches[1],
            patches[2],
            patches[3],
        ):
            with self.assertRaisesRegex(RuntimeError, "z-wide"):
                adapter.set_xyz(h, 10.0, 10.0, 300.0, with_actuators={"z": "z-galvo"})
        xy.assert_called_once()


class TestDriverSetupReadiness(unittest.TestCase):
    measured_limits = {"source": "machine", "path": "limits.json", "is_fallback": False}

    def test_ready_requires_measured_orientation_loaded_calibration_and_active_slot(
        self,
    ):
        h = _handle(origin=_origin(objective={"slotIndex": 1, "name": "10x"}))
        adapter._session_state.install(
            h.client,
            adapter._session_state.SessionConfig(
                orientation=_orientation.Orientation(rotate_deg=0),
                translations={1: (0.0, 0.0, 0.0), 2: (1.0, 2.0, 3.0)},
                calibration_name="water_lens_setup",
                orientation_info={"loaded": True, "measured": True, "rotate_deg": 0},
                calibration_info={
                    "loaded": True,
                    "name": "water_lens_setup",
                    "slots": [1, 2],
                    "measured_slots": [1, 2],
                },
            ),
        )
        self.addCleanup(adapter._session_state.uninstall, h.client)

        ready = _instrument.setup_readiness(
            h, {"slotIndex": 2, "name": "20x"}, self.measured_limits
        )
        self.assertTrue(ready["ready"])
        self.assertEqual(ready["issues"], [])

        missing = _instrument.setup_readiness(
            h, {"slotIndex": 3, "name": "40x"}, self.measured_limits
        )
        self.assertFalse(missing["ready"])
        self.assertIn("slot 3", missing["issues"][0])

        h.origin["objective"] = {"slotIndex": 4, "name": "uncalibrated"}
        bad_origin = _instrument.setup_readiness(
            h, {"slotIndex": 2, "name": "20x"}, self.measured_limits
        )
        self.assertFalse(bad_origin["ready"])
        self.assertIn("run-origin objective slot 4", bad_origin["issues"][0])

    def test_seeded_placeholder_calibration_is_not_ready_even_though_it_loads(self):
        """A bundled-default calibration loads fine but was never measured HERE.

        Its entries carry no session provenance (``measured_slots`` empty), so
        the verdict must refuse — otherwise a never-calibrated microscope would
        compensate objective changes with the repository's placeholder numbers.
        """
        h = _handle(origin=_origin(objective={"slotIndex": 1, "name": "10x"}))
        adapter._session_state.install(
            h.client,
            adapter._session_state.SessionConfig(
                orientation=_orientation.Orientation(rotate_deg=0),
                translations={1: (0.0, 0.0, 0.0), 2: (-6.5, 21.5, -3.7)},
                calibration_name="water_lens_setup",
                orientation_info={"loaded": True, "measured": True, "rotate_deg": 0},
                calibration_info={
                    "loaded": True,
                    "name": "water_lens_setup",
                    "slots": [1, 2],
                    "measured_slots": [],  # seeded from the bundled placeholder
                },
            ),
        )
        self.addCleanup(adapter._session_state.uninstall, h.client)

        setup = _instrument.setup_readiness(
            h, {"slotIndex": 2, "name": "20x"}, self.measured_limits
        )

        self.assertFalse(setup["ready"])
        self.assertTrue(any("placeholder" in issue for issue in setup["issues"]))
        self.assertTrue(
            any("calibrate_objective_pair" in issue for issue in setup["issues"])
        )

    def test_calibration_info_without_provenance_field_fails_closed(self):
        """An info dict lacking measured_slots must count as unmeasured."""
        h = _handle(origin=_origin(objective={"slotIndex": 1, "name": "10x"}))
        adapter._session_state.install(
            h.client,
            adapter._session_state.SessionConfig(
                orientation=_orientation.Orientation(rotate_deg=0),
                translations={1: (0.0, 0.0, 0.0)},
                orientation_info={"loaded": True, "measured": True, "rotate_deg": 0},
                calibration_info={"loaded": True, "slots": [1]},
            ),
        )
        self.addCleanup(adapter._session_state.uninstall, h.client)

        setup = _instrument.setup_readiness(
            h, {"slotIndex": 1, "name": "10x"}, self.measured_limits
        )

        self.assertFalse(setup["ready"])
        self.assertTrue(any("placeholder" in issue for issue in setup["issues"]))

    def test_placeholder_orientation_is_not_ready_even_when_rotation_is_zero(self):
        h = _handle(origin=_origin(objective={"slotIndex": 1, "name": "10x"}))
        adapter._session_state.install(
            h.client,
            adapter._session_state.SessionConfig(
                orientation=_orientation.Orientation(rotate_deg=0),
                translations={1: (0.0, 0.0, 0.0)},
                orientation_info={"loaded": True, "measured": False, "rotate_deg": 0},
                calibration_info={"loaded": True, "slots": [1]},
            ),
        )
        self.addCleanup(adapter._session_state.uninstall, h.client)

        setup = _instrument.setup_readiness(h, {"slotIndex": 1}, self.measured_limits)

        self.assertFalse(setup["ready"])
        self.assertIn("orientation", setup["issues"][0])

    def test_fallback_limits_are_part_of_the_single_driver_readiness_verdict(self):
        h = _handle(origin=_origin(objective={"slotIndex": 1, "name": "10x"}))
        adapter._session_state.install(
            h.client,
            adapter._session_state.SessionConfig(
                orientation=_orientation.Orientation(rotate_deg=0),
                translations={1: (0.0, 0.0, 0.0)},
                orientation_info={"loaded": True, "measured": True, "rotate_deg": 0},
                calibration_info={"loaded": True, "slots": [1], "measured_slots": [1]},
            ),
        )
        self.addCleanup(adapter._session_state.uninstall, h.client)

        setup = _instrument.setup_readiness(
            h,
            {"slotIndex": 1},
            {"source": "defaults", "path": "limits.json", "is_fallback": True},
        )

        self.assertFalse(setup["ready"])
        self.assertIn("machine-specific limits", setup["issues"][0])
        self.assertIn("set_limits.ipynb", setup["issues"][0])


class TestLifecycle(unittest.TestCase):
    def test_ops_after_disconnect_raise(self):
        h = _handle()
        adapter.disconnect(h)
        with self.assertRaisesRegex(RuntimeError, "disconnected"):
            adapter.get_actuators(h)

    def test_disconnect_uninstalls_the_commands_layer_gate_state(self):
        h = _handle()
        self.assertIsNotNone(adapter._gate.state_for(h.client))
        adapter.disconnect(h)
        self.assertIsNone(adapter._gate.state_for(h.client))

    def test_connect_runs_the_limits_handshake(self):
        """connect must run the machine-local limits handshake, or set_xyz can't move.

        move_xy/move_z refuse until the stage envelope is applied and the
        function-keyed gate is installed, and the controller has no limits
        hook -- so the adapter's connect must do it, against REAL machine-local
        files, seeding ProgramData defaults first when needed.
        """
        import os

        _clear_limits()
        self.addCleanup(_clear_limits)
        provision_machine_limits(os.environ["ZMART_MICROSCOPY_ROOT"])
        with patch.object(
            adapter._session, "connect_python_client", return_value=object()
        ):
            h = adapter.connect(dict(adapter.CONNECTION))
        self.assertIsInstance(h, adapter.ZmartHandle)
        # the stage envelope was applied from the ProgramData snapshot...
        self.assertEqual(limits_checks.get_stage_limits()["x_max"], 130000.0)
        # ...and the commands-layer gate governs this client (with provenance)
        described = _gate.describe(h.client)
        self.assertEqual(described["source"], "machine")
        self.assertFalse(described["is_fallback"])

    def test_connect_uses_packaged_fallback_when_programdata_is_empty(self):
        """Empty ProgramData uses defaults without creating a limits snapshot."""
        with patch.object(
            adapter._session, "connect_python_client", return_value=object()
        ):
            h = adapter.connect(dict(adapter.CONNECTION))
        self.assertIsInstance(h, adapter.ZmartHandle)
        self.assertFalse(h.closed)
        described = _gate.describe(h.client)
        self.assertEqual(described["source"], "defaults")
        self.assertTrue(described["is_fallback"])
        self.assertIsNone(_machine.MACHINE.latest_snapshot("limits"))
        state = _gate.state_for(h.client)
        self.assertTrue(state.ok)

    def test_full_controller_session_flow(self):
        """End to end through a real zmart_controller Session (real handshake)."""
        import os

        import zmart_controller

        from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.for_zmart_controller import (
            zmart_controller_plugin as driver,
        )

        _clear_limits()
        self.addCleanup(_clear_limits)
        provision_machine_limits(os.environ["ZMART_MICROSCOPY_ROOT"])
        patches = _patch_position(x_um=1000.0, y_um=2000.0, z_wide_um=30.0)
        with (
            patch.object(
                adapter._session, "connect_python_client", return_value=object()
            ),
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patch.object(
                _motion,
                "arrive_xy",
                return_value={"success": True, "confirmed": True},
            ),
            patch.object(
                _commands,
                "move_z",
                return_value={"success": True, "confirmed": True},
            ),
        ):
            session = zmart_controller.ZmartController(driver)
            try:
                # Capturing the origin is a driver setup step, not a controller
                # command, so it is called on the adapter with the driver handle.
                adapter.set_origin(session._handle)
                answer = session.set_xyz(10, 20, 5, with_actuators={"z": "z-galvo"})
                self.assertIs(answer["success"], True)
                # The move answers a read-back in get_xyz's shape. The readers
                # are mocked and never move, so it reads the origin itself.
                moved = answer["content"]
                self.assertEqual(
                    list(moved["x"]),
                    ["position", "unit", "actuator", "actuators", "canvas"],
                )
                self.assertEqual(moved["x"]["position"], 0.0)
                self.assertEqual(
                    moved["z"]["actuators"], {"z-wide": 30.0, "z-galvo": 0.0}
                )
                xyz = session.get_xyz(with_actuators={"z": "z-galvo"})
                self.assertEqual(xyz["content"], moved)
            finally:
                session.disconnect()

    def test_every_command_answers_in_the_controller_shape(self):
        """Read through the controller, every answer is {"success", "content"}.

        The controller documents this shape for every driver, and a workflow
        reads ``answer["content"]``. Each command here is called through a real
        controller Session and checked against the adapter function it wraps.
        """
        import zmart_controller

        patches = _patch_position(x_um=1000.0, y_um=2000.0, z_wide_um=30.0)
        with patches[0], patches[1], patches[2], patches[3]:
            handle = _handle()
            # A module driver whose connect hands over this ready-made handle,
            # so the controller wraps the ops table without a real connect.
            ops = adapter.ops_table()
            ops["connect"] = lambda connection: handle
            driver = SimpleNamespace(__name__="stellaris", **ops)
            session = zmart_controller.ZmartController(driver, {})
            for name in ("get_actuators", "get_xyz"):
                with self.subTest(command=name):
                    answer = getattr(session, name)()
                    self.assertEqual(set(answer), {"success", "content"})
                    self.assertIs(answer["success"], True)
                    self.assertEqual(answer["content"], getattr(adapter, name)(handle))

    def test_the_ops_table_wraps_every_answering_command(self):
        """connect and disconnect are handed over as they are; the rest are wrapped."""
        from zmart_controller.zmart_controller import COMMANDS

        table = adapter.ops_table()
        self.assertIs(table["connect"], adapter.connect)
        self.assertIs(table["disconnect"], adapter.disconnect)
        for name in COMMANDS:
            if name == "disconnect":
                continue
            with self.subTest(command=name):
                self.assertIsNot(table[name], getattr(adapter, name))
                self.assertIs(table[name].__wrapped__, getattr(adapter, name))


class TestFunctionLimits(unittest.TestCase):
    """The commands-layer function-keyed gate as seen through the adapter.

    The gate itself lives in ``commands/gate.py`` (and is exhaustively
    attacked in test_limits_adversarial.py); these tests pin the adapter's
    contract on top of it: the adapter does no checking of its own, refusals
    surface as actionable RuntimeErrors, and provenance is reported.
    """

    def test_bundled_template_covers_every_declared_key(self):
        """THE completeness guard on the template: a new key cannot ship absent."""
        from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.configuration.limits import (
            config as limits_config,
        )

        path = _machine.MACHINE.bundled_default_path(_machine.LIMITS_FILENAME)
        payload = limits_config.validate_payload(
            json.loads(path.read_text(encoding="utf-8"))
        )
        self.assertEqual(set(payload), set(limits_config._REQUIRED_FILE_KEYS))
        self.assertEqual(payload["objective_slot"], [])

    def test_set_xyz_refuses_beyond_function_limits_at_the_commands_layer(self):
        """The function-keyed gate fires inside the real move_xy (through
        arrive_xy) — no adapter check involved, and no motion happens
        because the very first leg is the one refused."""
        _wide_limits()  # Phase A permissive, so the function-limits layer is what fires
        self.addCleanup(_clear_limits)
        h = _handle(gate_constraints={"x_um": {"min": 0, "max": 500}})
        patches = _patch_position(x_um=0.0, y_um=0.0)
        with (
            patch.object(_commands, "move_z") as mz,
            patches[0],
            patches[1],
            patches[2],
            patches[3],
        ):
            with self.assertRaisesRegex(RuntimeError, r"x_um=.*outside range"):
                # (1000, 10000) is inside envelope and backstop, so the
                # function-keyed gate is the only layer that can refuse it.
                adapter.set_xyz(h, 1000.0, 10000.0, 0.0)
        mz.assert_not_called()

    def test_z_leg_function_limit_violation_keeps_the_actuator_hint(self):
        _wide_limits()
        self.addCleanup(_clear_limits)
        h = _handle(
            gate_constraints={"z_galvo_um": {"min": -250, "max": 250}},
            origin=_origin(x_um=10000.0, y_um=10000.0),
        )
        patches = _patch_position(z_wide_um=0.0, z_galvo_um=0.0)
        with (
            patch.object(_motion, "arrive_xy") as xy,
            patches[0],
            patches[1],
            patches[2],
            patches[3],
        ):
            with self.assertRaisesRegex(
                RuntimeError, r"with_actuators=\{'z': 'z-wide'\}"
            ):
                adapter.set_xyz(h, 10.0, 10.0, 300.0, with_actuators={"z": "z-galvo"})
        xy.assert_called_once()

    def test_mutating_ops_refuse_without_a_limits_handshake(self):
        """Fail-closed below the adapter: no gate state means no mutations.

        The refusal comes from the commands layer and surfaces as the ops
        contract's RuntimeError; read-only ops still work. (set_origin fires
        no native command — it captures a reference — so it is deliberately
        not among the refusals.)
        """
        _wide_limits()  # the stage envelope alone must NOT be enough
        self.addCleanup(_clear_limits)
        h = _handle(gated=False)
        patches = _patch_position()
        catalog = [
            {"Name": "Overview", "IsSelected": True, "IsAutofocus": False},
            {"Name": "HiRes", "IsSelected": False, "IsAutofocus": False},
        ]
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patch.object(_readers, "get_jobs", return_value=catalog),
        ):
            with self.assertRaisesRegex(RuntimeError, "refused"):
                adapter.set_xyz(h, 0.0, 0.0, 0.0)
            with self.assertRaisesRegex(RuntimeError, "refused"):
                adapter.set_state(h, {"changeable": {"job": "HiRes"}})
            with self.assertRaisesRegex(RuntimeError, "refused"):
                adapter.run_procedure(h, {"name": "backlash_takeup"})
            self.assertIn("backlash_takeup", adapter.get_procedures(h))  # reads fine

    def test_get_state_reports_limits_provenance(self):
        h = _handle()
        with (
            patch.object(
                _readers,
                "get_hardware_info",
                return_value={"SerialNumber": "S", "SystemType": "T", "Microscope": {}},
            ),
            patch.object(
                _readers,
                "get_selected_job",
                return_value={"Name": "Overview", "IsSelected": True},
            ),
            patch.object(_readers, "get_jobs", return_value=[]),
        ):
            observed = adapter.get_state(h)["observed"]
        self.assertEqual(
            observed["limits"],
            {"source": "test", "path": None, "is_fallback": False},
        )

    def test_machine_stage_envelope_governs_the_gate(self):
        """The snapshot's envelope governs moves, never a stale template copy."""
        import os

        _clear_limits()
        self.addCleanup(_clear_limits)
        provision_machine_limits(
            os.environ["ZMART_MICROSCOPY_ROOT"],
            stage_um=dict(DEFAULT_STAGE_UM, x=[2000.0, 50000.0]),
        )
        client = object()
        state = _gate.connect_handshake(client)
        self.assertTrue(state.ok)
        self.assertIsNone(_gate.check_refusal(client, "move_xy", {"x_um": 2500.0}))
        # Inside the template envelope, outside the machine's envelope.
        self.assertIn(
            "outside range",
            _gate.check_refusal(client, "move_xy", {"x_um": 1500.0}),
        )

    def test_unknown_machine_axis_falls_back_to_defaults(self):
        """An envelope the schema can't represent falls back to bundled defaults."""
        import os

        profile = provision_machine_limits(os.environ["ZMART_MICROSCOPY_ROOT"])
        payload = json.loads(
            (profile.latest_snapshot("limits") / "limits.json").read_text(
                encoding="utf-8"
            )
        )
        payload["theta_um"] = [0.0, 360.0]
        (profile.latest_snapshot("limits") / "limits.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        client = object()
        state = _gate.connect_handshake(client)
        # The invalid machine file is not used; the session is governed by the
        # bundled DEFAULT limits (loudly), never left fail-closed.
        self.assertTrue(state.ok)
        self.assertTrue(state.limits.describe()["is_fallback"])
        # A move inside the default envelope is allowed; one outside is refused.
        self.assertIsNone(
            _gate.check_refusal(client, "move_xy", {"x_um": 5000.0, "y_um": 5000.0})
        )
        self.assertIsNotNone(
            _gate.check_refusal(client, "move_xy", {"x_um": 999999.0, "y_um": 5000.0})
        )


if __name__ == "__main__":
    unittest.main()
