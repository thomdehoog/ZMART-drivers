"""Offline tests for the ZMART Controller adapter.

The driver layers underneath (readers, commands, capture, save, motion)
are patched; what is under test is the adapter's contract with
``zmart_controller``: registration, frame math, actuator mapping,
option validation, and closed-handle semantics — including a full
end-to-end pass through a real controller ``Session``.
"""

import json
import re
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from limits_fixtures import (
    DEFAULT_STAGE_UM,
    install_permissive_limits,
    provision_machine_limits,
)

from zmart_drivers.leica.stellaris.navigator_expert.driver_setup.limits import (
    handshake as _handshake,
)

from zmart_drivers.leica.stellaris.navigator_expert.procedures.save_data.product import PlaneIndex

from zmart_drivers.leica.stellaris.navigator_expert.dispatcher import limits as _gate
from zmart_drivers.leica.stellaris.navigator_expert.primitives.cam_api import parsing as _readers_parsing
from zmart_drivers.leica.stellaris.navigator_expert.driver_setup import (
    orientation as _orientation,
)
from zmart_drivers.leica.stellaris.navigator_expert.driver_setup import (
    store as _machine,
)
from zmart_drivers.leica.stellaris.navigator_expert.procedures.save_data import naming as _log
from zmart_drivers.leica.stellaris.navigator_expert.procedures.clear_cache import clear as _release
from zmart_drivers.leica.stellaris.navigator_expert.procedures.save_data import (
    save_waiting as _save_waiting,
)
from zmart_drivers.leica.stellaris.navigator_expert.procedures.save_data import (
    save as _save,
)

from zmart_drivers.leica.stellaris.navigator_expert.procedures import acquire as _acquisition

from zmart_drivers.leica.stellaris.navigator_expert.procedures import keep_in_view as _commands_comp
from zmart_drivers.leica.stellaris.navigator_expert.procedures import absolute_coordinate_system as _frame
from zmart_drivers.leica.stellaris.navigator_expert.zmart_controller_plugin import info as _controller_info
from zmart_drivers.leica.stellaris.navigator_expert.zmart_controller_plugin import procedure_menu as _procedure_menu
from zmart_drivers.leica.stellaris.navigator_expert.procedures import state as _state_record

from zmart_drivers.leica.stellaris.navigator_expert.procedures import readings as _readers

from zmart_drivers.leica.stellaris.navigator_expert.primitives.cam_api import client as _connect_module
from zmart_drivers.leica.stellaris.navigator_expert.methods import DEFAULT_CONNECTION, Microscope
from zmart_drivers.leica.stellaris.navigator_expert.zmart_controller_plugin.zmart_controller_plugin import ZmartControllerPlugin
from zmart_drivers.leica.stellaris.navigator_expert.methods import Session


def answering_client():
    """A stand-in LAS X client that answers the ping after connecting, and nothing else."""
    return SimpleNamespace(PyApiPing=SimpleNamespace(UpdateAwaitReceipt=lambda window_s: True))


def _connect_handle(connection=None):
    """Connect the way the controller does, by making the driver, and return its session handle."""
    return ZmartControllerPlugin(connection).microscope.handle


def _disconnect_handle(handle):
    """Disconnect a session handle the way ``Microscope.close`` does."""
    Microscope.close(SimpleNamespace(handle=handle))


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
    h = Session(
        client=object(), connection=dict(DEFAULT_CONNECTION), hash6="000abc"
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
    limit_rules.set_stage_limits(
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
    limit_rules._stage_limits.update(dict.fromkeys(limit_rules._stage_limits, None))


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

from zmart_drivers.leica.stellaris.navigator_expert.driver_setup.origin import (
    set_origin as _set_origin,
)
from zmart_drivers.leica.stellaris.navigator_expert.operations.changes import acquire as acquire_change
from zmart_drivers.leica.stellaris.navigator_expert.driver_setup.limits import limit_rules
from zmart_drivers.leica.stellaris.navigator_expert.procedures.save_data import save_waiting as save_settings
from zmart_drivers.leica.stellaris.navigator_expert.operations.changes import stage


def _acquire_and_save(handle, **given):
    """Acquire, then save what was acquired, as the controller's acquire does; answer the saved record."""
    taken = _acquisition.acquire(handle, **given)
    (record,) = _save_waiting.save_waiting(handle, taken["position_label"])
    return record


class TestRegistration(unittest.TestCase):
    def test_connection_profile_does_not_select_operator_calibration(self):
        self.assertNotIn("calibration_name", DEFAULT_CONNECTION)

    def test_the_driver_class_holds_every_command(self):
        from zmart_controller.zmart_controller import COMMANDS

        for command in COMMANDS:
            if command == "connect":
                continue
            with self.subTest(command=command):
                self.assertTrue(callable(getattr(ZmartControllerPlugin, command)))

    def test_importing_the_driver_registers_nothing(self):
        import zmart_drivers.leica.stellaris.navigator_expert.zmart_controller_plugin.zmart_controller_plugin as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        self.assertIsNone(
            re.search(r"^(from|import) zmart_controller", source, re.MULTILINE)
        )

    def test_set_origin_is_a_driver_step_not_a_controller_op(self):
        """The origin is driver configuration: the controller cannot set it."""
        from zmart_controller.zmart_controller import COMMANDS

        self.assertNotIn("set_origin", COMMANDS)
        self.assertFalse(hasattr(ZmartControllerPlugin, "set_origin"))
        self.assertTrue(callable(Microscope.set_origin))


class TestCalibrationSelection(unittest.TestCase):
    def test_named_connection_calibration_is_used_for_objective_translations(self):
        # The driver now owns loading: the named calibration flows through
        # the driver's connection -> model.load_objective_calibration, where one
        # exact document supplies both translations and readiness provenance.
        from zmart_drivers.leica.stellaris.navigator_expert.driver_setup import (
            optical_calibration as drv_session,
        )
        from zmart_drivers.leica.stellaris.navigator_expert.driver_setup.optical_calibration import (
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
            translations, info = drv_session.model.load_objective_calibration("lens_A")

        load.assert_called_once_with(path.absolute())
        self.assertEqual(translations[2], (12.0, 17.0, 3.0))
        self.assertEqual(info["measured_slots"], [1, 2])

    def test_translations_and_provenance_come_from_one_calibration_read(self):
        """A snapshot adoption during connect cannot mix old math with new proof."""
        from zmart_drivers.leica.stellaris.navigator_expert.driver_setup import (
            optical_calibration as drv_session,
        )
        from zmart_drivers.leica.stellaris.navigator_expert.driver_setup.optical_calibration import (
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
            translations, info = drv_session.model.load_objective_calibration("lens_A")

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
            record = _set_origin.set_origin(h)
            self.assertEqual(record["origin"], {"x": 0.0, "y": 0.0, "z": 0.0})
            self.assertTrue(record["origin_file"].endswith("origin.json"))
            self.assertEqual(record["reference"]["z_focus_um"], 30.0)
            pos = _frame.get_xyz(h)
        self.assertEqual(pos["x"]["position"], 0.0)
        self.assertEqual(pos["z"]["position"], 0.0)
        # Each axis carries the five entries of the controller's contract, in
        # this order, says which motor this answer used, and names every motor
        # that can move it.
        self.assertEqual(
            list(pos["x"]), ["position", "unit", "used_actuator", "actuator_readings", "canvas"]
        )
        self.assertEqual(pos["z"]["used_actuator"], "z-wide")
        self.assertEqual(pos["x"]["unit"], "micrometer")
        self.assertEqual(pos["x"]["actuator_readings"], {"motoric": 1000.0})
        self.assertEqual(pos["z"]["actuator_readings"], {"z-wide": 30.0, "z-galvo": 0.0})

    def test_set_origin_persists_into_its_own_origin_folder(self):
        import json
        import tempfile

        from zmart_drivers.leica.stellaris.navigator_expert.driver_setup.store import (
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
                record = _set_origin.set_origin(h)
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
                _connect_module, "connect_python_client", return_value=answering_client()
            ),
        ):
            return _connect_handle(connection or dict(DEFAULT_CONNECTION))

    def test_connect_restores_the_saved_origin(self):
        """The newest saved origin, objective included, becomes the session's frame."""
        import tempfile

        from zmart_drivers.leica.stellaris.navigator_expert.driver_setup.store import (
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

        from zmart_drivers.leica.stellaris.navigator_expert.driver_setup.store import (
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
                _set_origin.set_origin(h)
            fresh = self._connect_with_profile(profile)
        self.assertEqual(fresh.origin, h.origin)

    def test_connect_without_a_saved_origin_uses_stage_coordinates(self):
        """No origin saved yet: the frame is the absolute stage, with a warning."""
        import tempfile

        from zmart_drivers.leica.stellaris.navigator_expert.driver_setup.store import (
            MachineProfile,
        )

        with tempfile.TemporaryDirectory() as tmp:
            profile = MachineProfile(programdata_root=Path(tmp))
            with self.assertLogs(
                "zmart_drivers.leica.stellaris.navigator_expert.driver_setup.origin.saved_origin",
                level="WARNING",
            ) as logs:
                h = self._connect_with_profile(profile)
        self.assertEqual(h.origin, _origin())
        self.assertIn("no frame origin has been saved", "\n".join(logs.output))

    def test_connect_refuses_a_corrupt_origin_file(self):
        """A damaged origin file stops connect before LAS X is contacted."""
        import tempfile

        from zmart_drivers.leica.stellaris.navigator_expert.driver_setup.store import (
            MachineProfile,
        )

        with tempfile.TemporaryDirectory() as tmp:
            profile = MachineProfile(programdata_root=Path(tmp))
            path = profile.write_origin({"origin": _origin(x_um=5.0)})
            path.write_text("{not json", encoding="utf-8")
            with (
                patch.object(_machine, "MACHINE", profile),
                patch.object(
                    _connect_module, "connect_python_client"
                ) as connect_microscope,
            ):
                with self.assertRaisesRegex(RuntimeError, "could not be read"):
                    _connect_handle(dict(DEFAULT_CONNECTION))
            connect_microscope.assert_not_called()

    def test_connect_refuses_an_incomplete_origin_file(self):
        """An origin missing a value or its objective is refused, not zero-filled."""
        import tempfile

        from zmart_drivers.leica.stellaris.navigator_expert.driver_setup.store import (
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

        from zmart_drivers.leica.stellaris.navigator_expert.driver_setup.store import (
            MachineProfile,
        )

        with tempfile.TemporaryDirectory() as tmp:
            profile = MachineProfile(programdata_root=Path(tmp))
            path = profile.write_origin({"origin": _origin(x_um=5.0)})
            path.write_text("{not json", encoding="utf-8")
            h = self._connect_with_profile(
                profile, {**DEFAULT_CONNECTION, "load_origin": False}
            )
        self.assertEqual(h.origin, _origin())

    def test_get_xyz_is_origin_relative(self):
        h = _handle(origin=_origin(x_um=1000.0, y_um=2000.0, z_focus_um=30.0))
        patches = _patch_position(
            x_um=1010.0, y_um=1990.0, z_wide_um=32.0, z_galvo_um=3.0
        )
        with patches[0], patches[1], patches[2], patches[3]:
            pos = _frame.get_xyz(h)
        self.assertEqual(pos["x"]["position"], 10.0)
        self.assertEqual(pos["y"]["position"], -10.0)
        self.assertEqual(pos["z"]["position"], 5.0)  # (32 + 3) - 30 focus sum
        # The motors' own readings are the stage's numbers, with nothing
        # subtracted: neither the origin nor an objective translation.
        self.assertEqual(pos["x"]["actuator_readings"], {"motoric": 1010.0})
        self.assertEqual(pos["y"]["actuator_readings"], {"motoric": 1990.0})
        self.assertEqual(pos["z"]["actuator_readings"], {"z-wide": 32.0, "z-galvo": 3.0})
        for axis in ("x", "y", "z"):
            self.assertEqual(pos[axis]["unit"], "micrometer")
        self.assertNotIn("hardware", pos)
        # What it read is what a capture is labelled with, so a capture asks nothing more.
        self.assertEqual(
            h.driven_to,
            {"x": 10.0, "y": -10.0, "z": 5.0, "z_wide_um": 32.0, "z_galvo_um": 3.0},
        )

    def test_get_xyz_names_every_motor_get_actuators_lists(self):
        """Every motor in the menu is reported with its own reading, so a workflow
        can look it up by the name it would pass to with_actuators. The z-galvo
        is reported too, though it is never used to move."""
        h = _handle()
        patches = _patch_position()
        with patches[0], patches[1], patches[2], patches[3]:
            pos = _frame.get_xyz(h)
            menu = _frame.get_actuators(h)
        for axis in ("x", "y"):
            self.assertEqual(list(pos[axis]["actuator_readings"]), menu[axis]["options"])
        for axis in ("x", "y", "z"):
            self.assertEqual(pos[axis]["used_actuator"], menu[axis]["default"])
        self.assertEqual(list(pos["z"]["actuator_readings"]), ["z-wide", "z-galvo"])
        # the z-galvo cannot be chosen to move with
        with self.assertRaises(ValueError):
            _frame.get_xyz(h, with_actuators={"z": "z-galvo"})

    def test_set_xyz_says_so_when_the_jobs_stack_is_on_the_galvo(self):
        h = _handle(origin=_origin(x_um=1000.0, y_um=2000.0, z_focus_um=30.0))
        patches = _patch_position(z_wide_um=32.0, z_galvo_um=0.0)
        stack_on_galvo = {**_settings(z_wide_um=32.0), "stack": {"mode": "z-galvo"}}
        with (
            patch.object(
                stage, "move_xy", return_value={"success": True, "confirmed": True}
            ),
            patch.object(
                stage, "move_z", return_value={"success": True, "confirmed": True}
            ),
            patches[0],
            patch.object(_readers, "get_job_settings", return_value=stack_on_galvo),
            patches[2],
            patches[3],
        ):
            _frame.set_xyz(h, 0.0, 0.0, 5.0)
        self.assertEqual(
            [n["what"] for n in h.unconfirmed],
            ["set_xyz: the job's z-stack is on the z-galvo"],
        )

    def test_set_xyz_parks_a_galvo_that_is_not_at_zero_first(self):
        h = _handle(origin=_origin(x_um=1000.0, y_um=2000.0, z_focus_um=30.0))
        moves = []

        def fake_move_z(client, job, z, unit="um", z_mode="galvo", **kwargs):
            moves.append((z, z_mode))
            return {"success": True, "confirmed": True}

        # z-wide at 32, z-galvo at 3: z-wide takes the 3 over, then the galvo goes to 0
        patches = _patch_position(z_wide_um=32.0, z_galvo_um=3.0)
        with (
            patch.object(
                stage, "move_xy", return_value={"success": True, "confirmed": True}
            ),
            patch.object(stage, "move_z", fake_move_z),
            patches[0],
            patches[1],
            patches[2],
            patches[3],
        ):
            _frame.set_xyz(h, 0.0, 0.0, 5.0)
        self.assertEqual(moves[:2], [(35.0, "zwide"), (0.0, "galvo")])

    def test_set_xyz_moves_to_absolute_with_z_wide_alone(self):
        h = _handle(origin=_origin(x_um=1000.0, y_um=2000.0, z_focus_um=30.0))
        moves = {}

        def fake_move_xy(client, x_um, y_um, **kwargs):
            moves["xy"] = (x_um, y_um)
            return {"success": True, "confirmed": True}

        def fake_move_z(client, job, z, unit="um", z_mode="galvo", **kwargs):
            moves.setdefault("z", []).append((z, z_mode))
            return {"success": True, "confirmed": True}

        # current hardware: z-wide at 32, z-galvo parked at 0
        patches = _patch_position(z_wide_um=32.0, z_galvo_um=0.0)
        with (
            patch.object(stage, "move_xy", fake_move_xy),
            patch.object(stage, "move_z", fake_move_z),
            patches[0],
            patches[1],
            patches[2],
            patches[3],
        ):
            record = _frame.set_xyz(h, 10.0, 20.0, 5.0)
        self.assertEqual(moves["xy"], (1010.0, 2020.0))
        # target focus = 30 + 5 = 35, all of it on z-wide; the galvo is not moved
        self.assertEqual(moves["z"], [(35.0, "zwide")])
        # The answer is a fresh get_xyz: the (patched) readings, not the targets.
        self.assertEqual(list(record), ["x", "y", "z", "objective_translation_um"])
        self.assertEqual(
            list(record["z"]), ["position", "unit", "used_actuator", "actuator_readings", "canvas"]
        )
        self.assertEqual(record["z"]["used_actuator"], "z-wide")
        self.assertEqual(record["z"]["actuator_readings"], {"z-wide": 32.0, "z-galvo": 0.0})
        self.assertEqual(
            record["z"]["position"], 2.0
        )  # (32 + 0) − 30 (patched readings)
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
                stage,
                "move_xy",
                return_value={"success": True, "confirmed": True},
            ),
            patch.object(stage, "move_z", fake_move_z),
            patches[0],
            patches[1],
            patches[2],
            patches[3],
        ):
            _frame.set_xyz(h, 0.0, 0.0, 10.0)  # default z actuator: z-wide
        # target focus = 30 + 10 = 40; the parked galvo offset (4) is kept
        self.assertEqual(moves["z"], (36.0, "zwide"))

    def test_unconfirmed_z_move_is_recorded_and_the_position_is_read_back(self):
        """A leg that was accepted but never confirmed does not stop the workflow:
        it is written down, and the answer is what the microscope reads back."""
        h = _handle()
        patches = _patch_position()
        with (
            patch.object(
                stage,
                "move_xy",
                return_value={"success": True, "confirmed": True},
            ),
            patch.object(
                stage,
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
            answer = _frame.set_xyz(h, 0.0, 0.0, 0.0)
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
                stage,
                "move_xy",
                return_value={"success": True, "confirmed": True},
            ),
            patch.object(
                stage,
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
            _frame.set_xyz(h, 0.0, 0.0, 0.0)
        self.assertEqual(h.unconfirmed, [])

    def test_unknown_actuator_rejected(self):
        h = _handle()
        with self.assertRaises(ValueError):
            _frame.get_actuators(h) and _frame.set_xyz(
                h, 0, 0, 0, with_actuators={"z": "hovercraft"}
            )

    def test_actuator_menu(self):
        """The driver names its default per axis: the focus drive that reaches the whole travel for z."""
        h = _handle()
        self.assertEqual(
            _frame.get_actuators(h),
            {
                "x": {"options": ["motoric"], "default": "motoric"},
                "y": {"options": ["motoric"], "default": "motoric"},
                "z": {"options": ["z-wide"], "default": "z-wide"},
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
            _acquisition.get_acquisition_settings(h),
            {"trigger": {"options": ["full"], "default": "full", "active": "full"}},
        )

    def test_unknown_or_invalid_option_rejected(self):
        h = _handle(
            connection={**DEFAULT_CONNECTION, "output_root": "/tmp/out"}
        )
        with (
            patch.object(_readers, "get_jobs", return_value=self._jobs()),
            patch.object(
                _readers,
                "get_selected_job",
                return_value={"Name": "Overview", "IsSelected": True},
            ),
        ):
            with self.assertRaisesRegex(ValueError, "unknown acquisition setting"):
                _acquire_and_save(
                    h,
                    acquisition_type="acquisition",
                    output_folder=None,
                    position_label="A1",
                    acquisition_settings={"fromat": "x"},
                )
            with self.assertRaisesRegex(ValueError, "invalid value"):
                _acquire_and_save(
                    h,
                    acquisition_type="acquisition",
                    output_folder=None,
                    position_label="A1",
                    acquisition_settings={"trigger": "snap"},
                )
            for gone in ("job", "folder", "format", "save_to"):
                with (
                    self.subTest(gone=gone),
                    self.assertRaisesRegex(ValueError, "unknown acquisition setting"),
                ):
                    _acquire_and_save(
                        h,
                        acquisition_type="acquisition",
                        output_folder=None,
                        position_label="A1",
                        acquisition_settings={gone: "x"},
                    )

    def test_without_a_data_location_nothing_is_saved_and_the_notebook_is_named(self):
        h = _handle()
        with self.assertRaisesRegex(RuntimeError, "set_data_location"):
            _acquire_and_save(
                h,
                acquisition_type="acquisition",
                output_folder=None,
                position_label="A1",
            )

    def test_the_set_up_data_location_is_where_captures_are_staged(self):
        """Where this microscope saves is set up once and said in ``setup``, not asked per capture."""
        with tempfile.TemporaryDirectory() as tmp:
            h = _handle(data_location=Path(tmp))
            with patch.object(Session, "selected_job_name", return_value="Overview"):
                info = _controller_info.get_info(h)
            self.assertNotIn("output_root", info)
            self.assertTrue(info["setup"]["data_location"]["done"])
            self.assertIn(str(Path(tmp)), info["setup"]["data_location"]["says"])
            with self._capturing(output_root=None) as calls:
                _acquire_and_save(
                    h,
                    acquisition_type="acquisition",
                    output_folder=None,
                    position_label="A1",
                )
            self.assertEqual(calls["output_root"].parent.parent, Path(tmp).resolve())

    def test_a_sessions_own_output_root_wins_over_the_set_up_one(self):
        h = _handle(
            connection={
                **DEFAULT_CONNECTION,
                "output_root": "/chosen/zmart",
            },
            data_location=Path("/elsewhere"),
        )
        with self._capturing(output_root=None) as calls:
            _acquire_and_save(
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
        self,
        *,
        image_paths=None,
        projections=None,
        output_root=Path("/tmp/out"),
        release_target=None,
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
                release_target=release_target,
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
                    _commands_comp, "select_job", lambda c, j, **k: {"success": True}
                ),
                patch.object(acquire_change, "take_picture", lambda c, j, **k: _a_capture(j)),
                patch.object(_save, "save", fake_save),
                patch.object(_log, "write", fake_log),
                *patches,
            ):
                standing_in.enter_context(stand_in)
            # Where to save is otherwise this microscope's set-up data location.
            if output_root is not None:
                standing_in.enter_context(
                    patch.object(Session, "output_root", return_value=output_root)
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
        gate = _gate.state_of(h.client)
        _gate.install(
            h.client,
            _gate.GateState(
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
            patch.object(Session, "selected_job_name", return_value="Overview"),
            patch.object(Session, "output_root", return_value=Path("/runs")),
            patch.object(_readers, "ping", return_value=True),
            patches[0],
            patches[1],
            patches[2],
            patches[3],
        ):
            info = _controller_info.get_info(h)

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

        h = _handle(
            connection={**DEFAULT_CONNECTION, "output_root": "/tmp/out"}
        )
        calls = {}

        def fake_capture(client, job, **kwargs):
            calls["captured"] = job
            return _a_capture(job)

        def fake_save(client, acq, output_root, naming, **kwargs):
            calls["saved"] = (str(output_root), naming)
            calls["lineage"] = kwargs.get("lineage")
            calls["notes"] = kwargs.get("notes")
            return SimpleNamespace(
                image_paths={0: Path("/tmp/out/img.ome.tif")},
                naming=naming,
            )

        patches = _patch_position(job="HiRes")
        with (
            patch.object(_readers, "get_jobs", return_value=self._jobs()),
            patch.object(_readers, "get_hardware_info", return_value={}),
            patch.object(acquire_change, "take_picture", fake_capture),
            patch.object(_save, "save", fake_save),
            patches[0],
            patches[1],
            patches[2],
            patches[3],
        ):
            record = _acquire_and_save(
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
        # What the store says about the capture is noted at the capture and threaded into save()
        notes = calls["notes"]
        self.assertEqual(notes.capture_record["position_label"], "prescan_a1b2c3_well-7")
        self.assertEqual(notes.capture_record["acquisition_settings"]["used"]["trigger"], "full")
        self.assertEqual(notes.vendor_facts["stem"], "acquisition_prescan_a1b2c3_well-7")
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
                    "x_um": 100.0,
                    "y_um": 200.0,
                    "z_um": 50.0,
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
            h = _handle(
                connection={**DEFAULT_CONNECTION, "output_root": str(out)}
            )

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
                    _commands_comp, "select_job", lambda *a, **k: {"success": True}
                ),
                patch.object(
                    acquire_change,
                    "take_picture",
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
                record = _acquire_and_save(
                    h,
                    acquisition_type="acquisition",
                    output_folder=None,
                    position_label="well-7",
                )

            self.assertEqual(
                record["files"], [str(image), str(vendor), str(state), *record["log"]]
            )
            self.assertEqual(
                check_acquire_answer({"success": True, "output": record}), []
            )

    def test_saving_follows_this_microscopes_profile(self):
        """The projection is set up once.

        They are this microscope's ``IMAGE_SAVE`` profile, the same for every
        capture, never a choice made per capture.
        """
        h = _handle()
        h.driven_to = {"x": 0.0, "y": 0.0, "z": 0.0, "z_wide_um": 0.0}
        with self._capturing() as calls:
            record = _acquire_and_save(
                h,
                acquisition_type="acquisition",
                output_folder=None,
                position_label="A1",
            )
        self.assertNotIn(
            "source", calls["save"]
        )  # always LAS X's cache: nothing to choose
        # Captures are always saved as OME-Zarr: there is no format to choose.
        self.assertNotIn("format", calls["save"])
        self.assertEqual(
            calls["save"]["projection"], _save_waiting.IMAGE_SAVE.projection
        )
        self.assertNotIn("cleanup_cache", calls["save"])
        self.assertEqual(record["format"], "ome-zarr")

    def test_the_capture_is_saved_in_the_output_folder_it_is_given(self):
        """The driver writes the files itself (from LAS X's cache), so it saves where it is told.

        The output folder replaces the session's staging folder under the
        data location; nothing else about the capture changes.
        """
        h = _handle()
        h.driven_to = {"x": 0.0, "y": 0.0, "z": 0.0, "z_wide_um": 0.0}
        with self._capturing(output_root=None) as calls:
            _acquire_and_save(
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
            record = _acquire_and_save(
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

    def test_acquiring_writes_nothing_and_leaves_the_cache_alone(self):
        """Taking a picture only keeps a note of it; saving and clearing are steps of their own."""
        h = _handle()
        h.driven_to = {"x": 0.0, "y": 0.0, "z": 0.0, "z_wide_um": 0.0}
        prepared = []
        with (
            self._capturing() as calls,
            patch.object(_release, "prepare", lambda: prepared.append(1)),
        ):
            taken = _acquisition.acquire(
                h,
                acquisition_type="acquisition",
                output_folder=None,
                position_label="A1",
            )
        self.assertEqual(taken["position_label"], "A1")
        self.assertNotIn("save", calls)
        self.assertEqual([c.label for c in h.unsaved], ["A1"])
        self.assertEqual(prepared, [])

    def test_saving_empties_the_waiting_list_and_notes_what_the_cache_holds(self):
        h = _handle()
        h.driven_to = {"x": 0.0, "y": 0.0, "z": 0.0, "z_wide_um": 0.0}
        prepared = []
        with (
            self._capturing(release_target=(7, "HiRes007")),
            patch.object(_release, "prepare", lambda: prepared.append(1)),
        ):
            _acquisition.acquire(
                h,
                acquisition_type="acquisition",
                output_folder=None,
                position_label="A1",
            )
            (record,) = _save_waiting.save_waiting(h)
        self.assertEqual(record["position_label"], "A1")
        self.assertEqual(h.unsaved, [])
        self.assertEqual(h.in_cache, [(7, "HiRes007")])
        self.assertEqual(prepared, [])  # saving never touches the cache

    def test_a_capture_that_fails_to_save_stays_waiting(self):
        h = _handle()
        h.driven_to = {"x": 0.0, "y": 0.0, "z": 0.0, "z_wide_um": 0.0}
        with self._capturing():
            _acquisition.acquire(
                h,
                acquisition_type="acquisition",
                output_folder=None,
                position_label="A1",
            )
            with patch.object(_save, "save", side_effect=RuntimeError("disk full")):
                with self.assertRaises(RuntimeError):
                    _save_waiting.save_waiting(h)
        self.assertEqual([c.label for c in h.unsaved], ["A1"])
        self.assertEqual(h.in_cache, [])

    def test_a_label_still_waiting_to_be_saved_is_not_taken_twice(self):
        h = _handle()
        h.driven_to = {"x": 0.0, "y": 0.0, "z": 0.0, "z_wide_um": 0.0}
        with self._capturing():
            _acquisition.acquire(
                h,
                acquisition_type="acquisition",
                output_folder=None,
                position_label="A1",
            )
            with self.assertRaisesRegex(ValueError, "still waiting to be saved"):
                _acquisition.acquire(
                    h,
                    acquisition_type="acquisition",
                    output_folder=None,
                    position_label="A1",
                )

    def test_saving_a_label_nobody_acquired_is_refused(self):
        h = _handle()
        with self.assertRaisesRegex(ValueError, "no capture waiting"):
            _save_waiting.save_waiting(h, "nothing")

    def test_captures_come_from_the_cache_and_clearing_is_not_a_saving_setting(self):
        """Every capture is read from LAS X's cache (AutoSave is not used, Thom, 2026-10-14);
        whether LAS X lets go of it is the clear_cache procedure, never a setting of the save."""
        self.assertFalse(hasattr(save_settings.ImageSaveProfile(), "source"))
        self.assertFalse(hasattr(save_settings.ImageSaveProfile(), "cleanup_cache"))

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
            _acquire_and_save(
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
            record = _acquire_and_save(
                h,
                acquisition_type="acquisition",
                output_folder=None,
                position_label="A1",
            )

        self.assertEqual(
            calls["save"]["projection"], _save_waiting.IMAGE_SAVE.projection
        )
        self.assertEqual(record["projections"], [str(projection)])
        self.assertIn(str(projection), record["files"])

    def test_every_capture_leaves_a_log_of_what_was_asked_and_done(self):
        h = _handle()
        h.driven_to = {
            "x": 100.0,
            "y": 200.0,
            "z": 50.0,
            "z_wide_um": 50.0,
            "z_galvo_um": 0.0,
        }
        image = Path("/tmp/out/.staging") / h.hash6 / "data" / "img.ome.tif"
        with self._capturing(image_paths={0: image}) as calls:
            record = _acquire_and_save(
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

        def read_the_stage(handle):
            handle.driven_to = {"x": 1200.0, "y": -450.0, "z": 33.5, "z_wide_um": 900.0}
            return {}

        with (
            self._capturing() as calls,
            patch.object(_acquisition, "get_xyz", read_the_stage),
        ):
            record = _acquire_and_save(
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
            record = _acquire_and_save(
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
            record = _acquire_and_save(
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
            record = _acquire_and_save(
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

    def test_a_stage_changed_in_las_x_is_noted_and_the_fresh_reading_is_used(self):
        h = _handle()
        h.driven_to = {"x": 0.0, "y": 0.0, "z": 0.0, "z_wide_um": 50.0}
        with self._capturing():
            record = _acquire_and_save(
                h,
                acquisition_type="acquisition",
                output_folder=None,
                position_label="A1",
            )
        self.assertEqual(
            [(p["x_um"], p["y_um"]) for p in record["planes"]], [(100.0, 200.0)]
        )
        self.assertEqual(
            [n["what"] for n in record["unconfirmed"]],
            ["acquire: the stage or the objective changed"],
        )

    def test_an_unchanged_stage_adds_no_note(self):
        h = _handle()
        h.driven_to = {"x": 100.0, "y": 200.0, "z": 50.0, "z_wide_um": 50.0}
        with self._capturing():
            record = _acquire_and_save(
                h,
                acquisition_type="acquisition",
                output_folder=None,
                position_label="A1",
            )
        self.assertEqual(record["unconfirmed"], [])

    def test_a_capture_before_the_stage_was_read_is_refused(self):
        """Every plane says where it is; with no position read or set yet, there is none to say."""
        h = _handle(driven_to=None)
        with (
            self._capturing(),
            patch.object(
                _acquisition, "get_xyz", side_effect=RuntimeError("no answer")
            ),
            self.assertRaisesRegex(RuntimeError, "where the stage is"),
        ):
            _acquire_and_save(
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

        from zmart_drivers.leica.stellaris.navigator_expert.driver_setup.store import (
            MachineProfile,
        )

        h = _handle(
            connection={**DEFAULT_CONNECTION, "output_root": "/tmp/out"}
        )
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
                    _commands_comp,
                    "select_job",
                    lambda client, job, **k: {"success": True},
                ),
                patch.object(
                    acquire_change,
                    "take_picture",
                    lambda client, job, **k: _a_capture(job),
                ),
                patch.object(_save, "save", fake_save),
                patches[0],
                patches[1],
                patches[2],
                patches[3],
            ):
                _acquire_and_save(
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
            patch.object(_commands_comp, "select_job", return_value={"success": True}),
            patch.object(acquire_change, "take_picture", lambda client, job, **k: _a_capture(job)),
            patch.object(_save, "save", fake_save),
            patches[0],
            patches[1],
            patches[2],
            patches[3],
        )

    def test_an_unlabelled_capture_is_named_by_the_session_counter(self):
        h = _handle(
            connection={**DEFAULT_CONNECTION, "output_root": "/tmp/out"}
        )
        calls = {}
        with ExitStack() as standing_in:
            for stand_in in self._fake_acquire_env(calls):
                standing_in.enter_context(stand_in)
            record0 = _acquire_and_save(
                h, acquisition_type="acquisition", output_folder=None
            )
            record1 = _acquire_and_save(
                h, acquisition_type="acquisition", output_folder=None
            )
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
            h = _handle(
                connection={**DEFAULT_CONNECTION, "output_root": tmp}
            )
            staged = Path(tmp).resolve() / ".staging" / h.hash6 / "data"
            staged.mkdir(parents=True)
            (staged / "acquisition_A1_T000000_C00_Z00000.ome.tiff").write_bytes(
                b"the first capture"
            )
            calls = {}
            with ExitStack() as standing_in:
                for stand_in in self._fake_acquire_env(calls):
                    standing_in.enter_context(stand_in)
                _acquire_and_save(
                    h,
                    acquisition_type="acquisition",
                    output_folder=None,
                    position_label="A1",
                )
        self.assertEqual(calls["namings"][0].stem, "acquisition_A1_001")

    def test_without_a_selected_job_nothing_is_captured(self):
        """The job is state: a capture runs the selected one, and refuses when none can be read."""
        h = _handle(
            connection={**DEFAULT_CONNECTION, "output_root": "/tmp/out"}
        )
        with (
            self._capturing(),
            patch.object(_readers, "get_selected_job", return_value=None),
            self.assertRaisesRegex(RuntimeError, "set_state"),
        ):
            _acquire_and_save(
                h,
                acquisition_type="acquisition",
                output_folder=None,
                position_label="A1",
            )

    def test_explicit_label_does_not_consume_counter(self):
        h = _handle(
            connection={**DEFAULT_CONNECTION, "output_root": "/tmp/out"}
        )
        calls = {}
        env = self._fake_acquire_env(calls)
        with ExitStack() as standing_in:
            for stand_in in env:
                standing_in.enter_context(stand_in)
            _acquire_and_save(
                h,
                acquisition_type="acquisition",
                output_folder=None,
                position_label="named",
            )  # must not bump the counter
            record = _acquire_and_save(
                h, acquisition_type="acquisition", output_folder=None
            )  # still gets 000000
        self.assertEqual(calls["namings"][0].stem, "acquisition_named")
        self.assertEqual(record["position_label"], "000000")

    def _acquire_and_list_the_calls(self, options=None):
        """Run acquire with every step stubbed out; returns the calls it made, in order."""
        h = _handle(
            connection={**DEFAULT_CONNECTION, "output_root": "/tmp/out"}
        )
        calls = []
        patches = _patch_position(job="Overview")
        with (
            patch.object(_readers, "get_jobs", return_value=self._jobs()),
            patch.object(_commands_comp, "select_job", return_value={"success": True}),
            patch.object(
                acquire_change,
                "take_picture",
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
            _acquire_and_save(
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
            state = _state_record.get_state(h)
        self.assertEqual(list(state), ["changeable", "observed"])  # changeable first
        self.assertEqual(state["changeable"], {"job": "Overview"})
        self.assertNotIn("mode", hardware.call_args.kwargs)
        self.assertNotIn("mode", selected.call_args.kwargs)
        self.assertNotIn("mode", jobs.call_args.kwargs)
        self.assertNotIn("mode", settings.call_args.kwargs)
        observed = state["observed"]
        self.assertEqual(observed["vendor"], "leica")
        self.assertEqual(observed["microscope"], "stellaris-y42h93")
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
            observed = _state_record.get_state(h)["observed"]
        self.assertEqual(
            observed["pixel_size"],
            {"x": 0.1, "y": 0.2, "unit": "um"},
        )
        # The contract's own keys: the image size and pixel size of these settings.
        self.assertEqual(observed["image_size_px"], [1000, 400])
        self.assertEqual(observed["pixel_size_um"], [0.1, 0.2])

    def test_the_image_size_is_the_picture_as_saved_turned_with_it(self):
        """A quarter turn of the pixels swaps width and height, as in the saved picture."""
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
            patch.object(
                _state_record,
                "_saved_orientation",
                return_value=_orientation.Orientation(rotate_deg=90),
            ),
        ):
            observed = _state_record.get_state(h)["observed"]
        self.assertEqual(observed["image_size_px"], [400, 1000])
        self.assertEqual(observed["pixel_size_um"], [0.2, 0.1])

    def test_set_state_refuses_an_autofocus_job(self):
        h = _handle()
        p = self._state_patches()
        with p[0], p[1], p[2], patch.object(_commands_comp, "select_job") as select:
            with self.assertRaisesRegex(ValueError, "autofocus"):
                _state_record.set_state(h, {"changeable": {"job": "AF Job"}})
        select.assert_not_called()

    def test_set_state_applies_changeable_ignoring_observed(self):
        h = _handle()
        p = self._state_patches()
        with (
            p[0],
            p[1],
            p[2],
            patch.object(
                _commands_comp, "select_job", return_value={"success": True}
            ) as select,
        ):
            captured = _state_record.get_state(h)
            captured["changeable"]["job"] = "HiRes"
            # observed is a report, never an instruction: even a wildly
            # mismatching observed part does not block the apply.
            captured["observed"]["serial_number"] = "SOMETHING-ELSE"
            result = _state_record.set_state(h, captured)
            self.assertEqual(result["applied"], {"job": "HiRes"})
            select.assert_called_once()

    def test_set_state_refuses_a_job_that_no_longer_exists(self):
        h = _handle()
        p = self._state_patches(jobs=("Overview",))
        with p[0], p[1], p[2], patch.object(_commands_comp, "select_job") as select:
            with self.assertRaisesRegex(ValueError, "no longer exists"):
                _state_record.set_state(h, {"changeable": {"job": "Gone"}})
        select.assert_not_called()

    def test_procedures(self):
        h = _handle()
        p = self._state_patches()
        with p[2]:
            procedures = _procedure_menu.get_procedures(h)
        self.assertIn("zero_z_galvo", procedures)
        self.assertNotIn("get_root", procedures)
        self.assertNotIn("get_positions", procedures)
        self.assertNotIn("get_focus_points", procedures)
        self.assertNotIn("autofocus", procedures)
        with self.assertRaises(ValueError):
            _procedure_menu.run_procedure(h, {"name": "nope"})

class TestConnectCardCalibrationLine(unittest.TestCase):
    """The Connect card says whether the objective calibration is usable.

    The session holds a calibration record even when loading failed, so the
    line has to come from what the record says was loaded, not from whether a
    record exists.
    """

    def test_a_loaded_calibration_shows_its_name(self):
        record = {"enabled": True, "loaded": True, "name": "water_lens_setup"}

        self.assertEqual(
            _controller_info._calibration_status(record), "water_lens_setup"
        )

    def test_a_loaded_calibration_without_a_name_says_found(self):
        record = {"enabled": True, "loaded": True, "name": None}

        self.assertEqual(_controller_info._calibration_status(record), "found")

    def test_a_calibration_that_failed_to_load_says_so_even_when_it_has_a_name(self):
        record = {"enabled": True, "loaded": False, "name": "water_lens_setup"}

        line = _controller_info._calibration_status(record)

        self.assertTrue(line.startswith("failed"))
        self.assertIn("objective changes are refused", line)

    def test_a_calibration_that_was_switched_off_says_so(self):
        record = {"enabled": False, "loaded": False, "name": None}

        self.assertEqual(_controller_info._calibration_status(record), "switched off")

    def test_no_record_at_all_says_none_found(self):
        self.assertEqual(
            _controller_info._calibration_status(None), "failed — none found"
        )


class TestGetInfoReportsNoPositions(unittest.TestCase):
    """get_info reports no tile or focus positions.

    Positions are never made in the Navigator Expert, so there are none to report.
    """

    def test_get_info_reports_no_positions(self):
        h = _handle()
        h.connection["output_root"] = "/tmp/zmart-test"
        with (
            patch.object(Session, "selected_job_name", return_value="Overview"),
        ):
            info = _controller_info.get_info(h)
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
            pos = _frame.get_xyz(h)
        self.assertEqual(pos["x"]["position"], 10.0)  # 1110 − 1000 − 100
        self.assertEqual(pos["y"]["position"], 10.0)  # 2060 − 2000 − 50
        self.assertEqual(pos["z"]["position"], 5.0)  # 45 − 30 − 10
        # The motors' readings stay untranslated: the stage's own numbers.
        self.assertEqual(pos["x"]["actuator_readings"], {"motoric": 1110.0})
        self.assertEqual(pos["z"]["actuator_readings"], {"z-wide": 45.0, "z-galvo": 0.0})
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
            patch.object(stage, "move_xy", fake_xy),
            patch.object(stage, "move_z", fake_z),
            patches[0],
            patches[1],
            patches[2],
            patches[3],
        ):
            record = _frame.set_xyz(h, 10.0, 10.0, 5.0)
        self.assertEqual(moves["xy"], (1110.0, 2060.0))  # ref + F + ΔT
        # One z-wide move holds the requested frame z=5 and ΔT.z=10 (30+5+10);
        # the z-galvo stays parked at 0.
        self.assertEqual(moves["z"], [(45.0, "zwide")])
        self.assertEqual(record["objective_translation_um"], [100.0, 50.0, 10.0])
        # The answer is read back from the (patched) microscope, like get_xyz.
        self.assertEqual(record["z"]["actuator_readings"], {"z-wide": 40.0, "z-galvo": 0.0})
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
            patch.object(stage, "move_xy", fake_xy),
            patch.object(stage, "move_z", fake_z),
            patches[0],
            patches[1],
            patches[2],
            patches[3],
        ):
            pos = _frame.get_xyz(h)
            record = _frame.set_xyz(h, 10.0, 10.0, 5.0)

        # Read: compensated with ΔT taken from the NEW job's objective.
        self.assertEqual(pos["x"]["position"], 10.0)  # 1110 − 1000 − 100
        self.assertEqual(pos["y"]["position"], 10.0)  # 2060 − 2000 − 50
        self.assertEqual(pos["z"]["position"], 5.0)  # 45 − 30 − 10
        # Move: targets include ΔT, and z is driven on the selected job.
        self.assertEqual(moves["xy"], (1110.0, 2060.0))
        self.assertEqual(moves["z"], [("HiRes", 45.0, "zwide")])
        self.assertEqual(record["objective_translation_um"], [100.0, 50.0, 10.0])

    def test_repeated_cross_objective_moves_do_not_accumulate_the_translation(self):
        h = self._cross_handle()
        moves = []

        def fake_z(client, job, z, unit="um", z_mode="galvo", **kwargs):
            moves.append((z, z_mode))
            return {"success": True, "confirmed": True}

        patches = _patch_position(z_wide_um=40.0, z_galvo_um=0.0, slot=2)
        with (
            patch.object(
                stage,
                "move_xy",
                return_value={"success": True, "confirmed": True},
            ),
            patch.object(stage, "move_z", fake_z),
            patches[0],
            patches[1],
            patches[2],
            patches[3],
        ):
            _frame.set_xyz(h, 0.0, 0.0, 4.0)
            _frame.set_xyz(h, 1.0, 1.0, 4.0)

        # The same absolute z-wide target (30 + 4 + 10) both times.
        assert moves == [(44.0, "zwide"), (44.0, "zwide")]

    def test_cross_objective_zwide_target_beyond_the_limit_is_refused(self):
        """The z-wide move is refused inside the real move_z (the commands
        layer owns all checking), after the XY move has arrived."""
        h = self._cross_handle()
        install_permissive_limits(
            h.client,
            z_wide_um={"min": 0.0, "max": 35.0},
            z_galvo_um={"min": -250.0, "max": 250.0},
        )
        patches = _patch_position(z_wide_um=40.0, z_galvo_um=0.0, slot=2)
        with (
            patch.object(stage, "move_xy") as xy,
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            self.assertRaisesRegex(RuntimeError, "z_wide_um") as ctx,
        ):
            _frame.set_xyz(h, 0.0, 0.0, 4.0)
        xy.assert_called_once()  # XY arrived (checked); the z move then refused
        self.assertNotIn("with_actuators", str(ctx.exception))

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
            patch.object(stage, "move_xy", fake_xy),
            patch.object(stage, "move_z", fake_z),
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
            moved = _frame.set_xyz(h, 12.0, -7.0, 4.0)
            pos = _frame.get_xyz(h)
        self.assertAlmostEqual(pos["x"]["position"], 12.0)
        self.assertAlmostEqual(pos["y"]["position"], -7.0)
        self.assertAlmostEqual(pos["z"]["position"], 4.0)
        # set_xyz answered the same read-back, so no second call is needed;
        # only the motor each answer used differs, as each was asked for.
        self.assertEqual(moved, pos)

    def test_cross_objective_move_without_translations_refuses(self):
        h = _handle(origin=_origin(objective={"name": "10x", "slotIndex": 1}))
        patches = _patch_position(slot=2)
        with (
            patch.object(stage, "move_xy") as xy,
            patches[0],
            patches[1],
            patches[2],
            patches[3],
        ):
            with self.assertRaisesRegex(RuntimeError, "translation"):
                _frame.set_xyz(h, 1.0, 1.0, 0.0)
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
            pos = _frame.get_xyz(h)
        self.assertEqual(pos["x"]["position"], 110.0)  # uncompensated, but loud

    def test_same_objective_needs_no_calibration(self):
        h = _handle(
            origin=_origin(x_um=1000.0, objective={"name": "63x", "slotIndex": 3})
        )
        patches = _patch_position(x_um=1010.0, slot=3)
        with patches[0], patches[1], patches[2], patches[3]:
            pos = _frame.get_xyz(h)
        self.assertEqual(pos["x"]["position"], 10.0)  # ΔT = 0, translations unused

    def test_out_of_range_focus_is_refused(self):
        """An out-of-range focus target is refused inside the real move_z."""
        h = _handle(
            origin=_origin(
                x_um=10000.0, y_um=10000.0, objective={"name": "63x", "slotIndex": 3}
            )
        )
        patches = _patch_position(z_wide_um=0.0, z_galvo_um=0.0, slot=3)
        with (
            patch.object(stage, "move_xy") as xy,
            patches[0],
            patches[1],
            patches[2],
            patches[3],
        ):
            with self.assertRaisesRegex(RuntimeError, "zwide"):
                _frame.set_xyz(h, 10.0, 10.0, 1_000_000.0)
        xy.assert_called_once()


class TestDriverSetupReadiness(unittest.TestCase):
    measured_limits = {"source": "machine", "path": "limits.json", "is_fallback": False}

    def test_ready_requires_measured_orientation_loaded_calibration_and_active_slot(
        self,
    ):
        h = _handle(origin=_origin(objective={"slotIndex": 1, "name": "10x"}))
        h.orientation = _orientation.Orientation(rotate_deg=0)
        h.translations = {1: (0.0, 0.0, 0.0), 2: (1.0, 2.0, 3.0)}
        h.calibration_name = "water_lens_setup"
        h.orientation_info = {"loaded": True, "measured": True, "rotate_deg": 0}
        h.calibration_info = {
                "loaded": True,
                "name": "water_lens_setup",
                "slots": [1, 2],
                "measured_slots": [1, 2],
            }

        ready = _state_record.setup_readiness(
            h, {"slotIndex": 2, "name": "20x"}, self.measured_limits
        )
        self.assertTrue(ready["ready"])
        self.assertEqual(ready["issues"], [])

        missing = _state_record.setup_readiness(
            h, {"slotIndex": 3, "name": "40x"}, self.measured_limits
        )
        self.assertFalse(missing["ready"])
        self.assertIn("slot 3", missing["issues"][0])

        h.origin["objective"] = {"slotIndex": 4, "name": "uncalibrated"}
        bad_origin = _state_record.setup_readiness(
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
        h.orientation = _orientation.Orientation(rotate_deg=0)
        h.translations = {1: (0.0, 0.0, 0.0), 2: (-6.5, 21.5, -3.7)}
        h.calibration_name = "water_lens_setup"
        h.orientation_info = {"loaded": True, "measured": True, "rotate_deg": 0}
        h.calibration_info = {
                "loaded": True,
                "name": "water_lens_setup",
                "slots": [1, 2],
                "measured_slots": [],  # seeded from the bundled placeholder
            }

        setup = _state_record.setup_readiness(
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
        h.orientation = _orientation.Orientation(rotate_deg=0)
        h.translations = {1: (0.0, 0.0, 0.0)}
        h.orientation_info = {"loaded": True, "measured": True, "rotate_deg": 0}
        h.calibration_info = {"loaded": True, "slots": [1]}

        setup = _state_record.setup_readiness(
            h, {"slotIndex": 1, "name": "10x"}, self.measured_limits
        )

        self.assertFalse(setup["ready"])
        self.assertTrue(any("placeholder" in issue for issue in setup["issues"]))

    def test_placeholder_orientation_is_not_ready_even_when_rotation_is_zero(self):
        h = _handle(origin=_origin(objective={"slotIndex": 1, "name": "10x"}))
        h.orientation = _orientation.Orientation(rotate_deg=0)
        h.translations = {1: (0.0, 0.0, 0.0)}
        h.orientation_info = {"loaded": True, "measured": False, "rotate_deg": 0}
        h.calibration_info = {"loaded": True, "slots": [1]}

        setup = _state_record.setup_readiness(
            h, {"slotIndex": 1}, self.measured_limits
        )

        self.assertFalse(setup["ready"])
        self.assertIn("orientation", setup["issues"][0])

    def test_fallback_limits_are_part_of_the_single_driver_readiness_verdict(self):
        h = _handle(origin=_origin(objective={"slotIndex": 1, "name": "10x"}))
        h.orientation = _orientation.Orientation(rotate_deg=0)
        h.translations = {1: (0.0, 0.0, 0.0)}
        h.orientation_info = {"loaded": True, "measured": True, "rotate_deg": 0}
        h.calibration_info = {"loaded": True, "slots": [1], "measured_slots": [1]}

        setup = _state_record.setup_readiness(
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
        _disconnect_handle(h)
        with self.assertRaisesRegex(RuntimeError, "disconnected"):
            _frame.get_actuators(h)

    def test_disconnect_uninstalls_the_commands_layer_gate_state(self):
        h = _handle()
        self.assertIsNotNone(_gate.state_for(h.client))
        _disconnect_handle(h)
        self.assertIsNone(_gate.state_for(h.client))

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
            _connect_module, "connect_python_client", return_value=answering_client()
        ):
            h = _connect_handle(dict(DEFAULT_CONNECTION))
        self.assertIsInstance(h, Session)
        # the stage envelope was applied from the ProgramData snapshot...
        self.assertEqual(limit_rules.get_stage_limits()["x_max"], 130000.0)
        # ...and the commands-layer gate governs this client (with provenance)
        described = _gate.describe(h.client)
        self.assertEqual(described["source"], "machine")
        self.assertFalse(described["is_fallback"])

    def test_connect_uses_packaged_fallback_when_programdata_is_empty(self):
        """Empty ProgramData uses defaults without creating a limits snapshot."""
        with patch.object(
            _connect_module, "connect_python_client", return_value=answering_client()
        ):
            h = _connect_handle(dict(DEFAULT_CONNECTION))
        self.assertIsInstance(h, Session)
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

        from zmart_drivers.leica.stellaris.navigator_expert.zmart_controller_plugin.zmart_controller_plugin import ZmartControllerPlugin as driver

        _clear_limits()
        self.addCleanup(_clear_limits)
        provision_machine_limits(os.environ["ZMART_MICROSCOPY_ROOT"])
        patches = _patch_position(x_um=1000.0, y_um=2000.0, z_wide_um=30.0)
        with (
            patch.object(
                _connect_module, "connect_python_client", return_value=answering_client()
            ),
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patch.object(
                stage,
                "move_xy",
                return_value={"success": True, "confirmed": True},
            ),
            patch.object(
                stage,
                "move_z",
                return_value={"success": True, "confirmed": True},
            ),
        ):
            session = zmart_controller.ZmartController(driver)
            try:
                # Capturing the origin is a driver setup step, not a controller
                # command, so it is called with the driver's own session handle.
                _set_origin.set_origin(session.plugin.microscope.handle)
                answer = session.set_xyz(10, 20, 5)
                self.assertIs(answer["success"], True)
                # The move answers a read-back in get_xyz's shape. The readers
                # are mocked and never move, so it reads the origin itself.
                moved = answer["output"]
                self.assertEqual(
                    list(moved["x"]),
                    ["position", "unit", "used_actuator", "actuator_readings", "canvas"],
                )
                self.assertEqual(moved["x"]["position"], 0.0)
                self.assertEqual(
                    moved["z"]["actuator_readings"], {"z-wide": 30.0, "z-galvo": 0.0}
                )
                xyz = session.get_xyz()
                self.assertEqual(xyz["output"], moved)
            finally:
                session.disconnect()


class TestFunctionLimits(unittest.TestCase):
    """The commands-layer function-keyed gate as seen through the adapter.

    The gate itself lives in ``commands/gate.py`` (and is exhaustively
    attacked in test_limits_adversarial.py); these tests pin the adapter's
    contract on top of it: the adapter does no checking of its own, refusals
    surface as actionable RuntimeErrors, and provenance is reported.
    """

    def test_bundled_template_covers_every_declared_key(self):
        """THE completeness guard on the template: a new key cannot ship absent."""
        from zmart_drivers.leica.stellaris.navigator_expert.driver_setup.limits import (
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
            patch.object(stage, "move_z") as mz,
            patches[0],
            patches[1],
            patches[2],
            patches[3],
        ):
            with self.assertRaisesRegex(RuntimeError, r"x_um=.*outside range"):
                # (1000, 10000) is inside envelope and backstop, so the
                # function-keyed gate is the only layer that can refuse it.
                _frame.set_xyz(h, 1000.0, 10000.0, 0.0)
        mz.assert_not_called()

    def test_z_move_beyond_the_function_limits_is_refused(self):
        _wide_limits()
        self.addCleanup(_clear_limits)
        h = _handle(
            gate_constraints={"z_wide_um": {"min": -250, "max": 250}},
            origin=_origin(x_um=10000.0, y_um=10000.0),
        )
        patches = _patch_position(z_wide_um=0.0, z_galvo_um=0.0)
        with (
            patch.object(stage, "move_xy") as xy,
            patches[0],
            patches[1],
            patches[2],
            patches[3],
        ):
            with self.assertRaisesRegex(RuntimeError, "zwide"):
                _frame.set_xyz(h, 10.0, 10.0, 300.0)
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
                _frame.set_xyz(h, 0.0, 0.0, 0.0)
            with self.assertRaisesRegex(RuntimeError, "refused"):
                _state_record.set_state(h, {"changeable": {"job": "HiRes"}})
            self.assertIn(
                "zero_z_galvo", _procedure_menu.get_procedures(h)
            )  # reads fine

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
            observed = _state_record.get_state(h)["observed"]
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
        state = _handshake.connect_handshake(client)
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
        state = _handshake.connect_handshake(client)
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
