"""A stand-in for ZEN and its API Gateway, for tests on any computer.

The driver talks to ZEN through ``ZenClient`` (``primitives/gateway/client.py``),
which hands each call to a service object. This file builds a real
``ZenClient`` with fake services behind it, so the loop thread, the waiting
and every procedure above the client run for real; only ZEN is pretended.
It needs neither ZEISS's ``zen_api`` package nor a gateway.

The pretend instrument (:class:`FakeScope`) has an XY stage and a focus drive
that arrive where they are sent (ZEN's moves answer when done, so the fake
does too, after ``travel_s``), an objective changer with three objectives, a
few saved experiments with a z-stack and tracks, and an image folder where
every acquisition is written as a ``.czi`` file: a real one when pylibCZIrw
is installed, a placeholder otherwise. It can be told to refuse the next
calls with a gRPC status, to show that a temporary problem is sent again and
a permanent one is not, and it counts every call, so a test can check that a
picture is taken once.

The response shapes follow the ``zen_api`` 2025.10.1 package: the stage's
``x`` and ``y`` and the focus's ``value`` in metres, the objective changer's
``value`` (a position, counted from 1), an experiment's ``experiment_id``,
``output_name`` and ``status``.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from zmart_drivers.zeiss.light_microscope.zen_api_grpc.primitives.gateway.client import (
    ZenClient,
)


class FakeGRPCError(Exception):
    """What a ZEN call raises when it fails: a status with a name, and a message, like grpclib's."""

    def __init__(self, status_name: str, message: str = ""):
        super().__init__(f"{status_name}: {message}")
        self.status = SimpleNamespace(name=status_name)
        self.message = message


def _status(running: bool, **fields) -> SimpleNamespace:
    """An ``ExperimentStatus`` message with the counts ZEN gives for a plain experiment."""
    values = {
        "tiles_index": -1,
        "tiles_count": 1,
        "cumulated_tiles_count": 1,
        "scenes_index": -1,
        "scenes_count": 1,
        "time_points_index": 0,
        "time_points_count": 1,
        "zstack_slices_index": 0,
        "zstack_slices_count": 1,
        "channels_index": 0,
        "channels_count": 1,
        "images_acquired_index": 0 if running else 1,
        "images_count": 1,
        "is_experiment_running": running,
        "is_acquisition_running": running,
        "total_elapsed_time": None,
    }
    values.update(fields)
    return SimpleNamespace(**values)


@dataclass
class FakeExperiment:
    """One saved experiment of the pretend ZEN: its channels and, when it has one, its z-stack."""

    channels: list[str] = field(default_factory=lambda: ["DAPI"])
    zstack: dict | None = None  # {"first_um", "last_um", "interval_um"}
    time_points: int = 1
    #: The frame ZEN's camera takes with this experiment, and the pixel size at 10x.
    frame_px: tuple[int, int] = (128, 96)
    pixel_um_at_10x: float = 0.65

    @property
    def slices(self) -> int:
        if not self.zstack:
            return 1
        span = self.zstack["last_um"] - self.zstack["first_um"]
        return int(round(span / self.zstack["interval_um"])) + 1


@dataclass
class FakeScope:
    """The pretend instrument: what every fake service reads and changes."""

    x_um: float = 20000.0
    y_um: float = 15000.0
    z_um: float = 1000.0
    objective_position: int = 1
    objectives: list = field(
        default_factory=lambda: [
            {
                "position": 1,
                "name": "Plan-Apochromat 10x/0.45",
                "magnification": 10.0,
                "na": 0.45,
            },
            {
                "position": 2,
                "name": "Plan-Apochromat 20x/0.8",
                "magnification": 20.0,
                "na": 0.8,
            },
            {
                "position": 3,
                "name": "Plan-Apochromat 63x/1.4 Oil",
                "magnification": 63.0,
                "na": 1.4,
            },
        ]
    )
    experiments: dict = field(
        default_factory=lambda: {
            "ZMART_Snap": FakeExperiment(channels=["DAPI", "GFP"]),
            "ZMART_ZStack": FakeExperiment(
                channels=["DAPI"],
                zstack={"first_um": 990.0, "last_um": 1010.0, "interval_um": 5.0},
            ),
            "ZMART_TimeLapse": FakeExperiment(channels=["GFP"], time_points=3),
        }
    )
    #: Where the pretend ZEN writes its CZI files; set by the test, usually a tmp folder.
    image_output_folder: str = ""
    #: How long a move takes before the call answers, in seconds; 0 answers at once.
    travel_s: float = 0.0
    #: How long an acquisition takes before the call answers, in seconds.
    exposure_s: float = 0.0
    loaded: dict = field(default_factory=dict)  # experiment id -> name
    status_by_id: dict = field(default_factory=dict)
    active_experiment: str | None = None
    surface_z_um: float = 1020.0
    best_focus_z_um: float = 1005.0
    stored_focus_um: float | None = None
    #: How the pretend camera is turned against the stage: the stage direction
    #: of +1 column and of +1 row, as the orientation item writes it.
    camera_image_to_stage: tuple = ((1, 0), (0, 1))
    #: Every call that reached the pretend ZEN, in order, as (name, arguments).
    calls: list = field(default_factory=list)
    #: The next calls of an operation are refused with this error: {"stage.move_to": [FakeGRPCError, ...]}.
    refusals: dict = field(default_factory=dict)
    #: The files the pretend ZEN wrote, in order.
    written: list = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _refuse(self, operation: str) -> None:
        """Raise the next refusal queued for *operation*, if any."""
        queued = self.refusals.get(operation)
        if queued:
            raise queued.pop(0)

    def _note(self, operation: str, **arguments) -> None:
        self.calls.append((operation, arguments))

    def experiment_of(self, experiment_id: str) -> FakeExperiment:
        name = self.loaded.get(experiment_id)
        if name is None:
            raise FakeGRPCError(
                "NOT_FOUND", f"no loaded experiment with id {experiment_id!r}"
            )
        return self.experiments[name]

    def magnification(self) -> float:
        found = next(
            (o for o in self.objectives if o["position"] == self.objective_position),
            None,
        )
        return float(found["magnification"]) if found else 10.0

    def write_czi(self, experiment_id: str, output_name: str) -> Path | None:
        """Write what ZEN would write: ``<output_name>.czi`` in the image folder."""
        if not self.image_output_folder:
            return None
        experiment = self.experiment_of(experiment_id)
        folder = Path(self.image_output_folder)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{output_name}.czi"
        pixel_um = experiment.pixel_um_at_10x * 10.0 / self.magnification()
        write_czi(
            path,
            experiment,
            pixel_um=pixel_um,
            at_um=(self.x_um, self.y_um),
            image_to_stage=self.camera_image_to_stage,
        )
        self.written.append(path)
        return path


def sample_field(x_um, y_um):
    """The pretend sample: a pattern of patches about 3 micrometres wide, the same wherever it is looked at from."""
    import numpy as np

    cell_um = 3.0
    ix, iy = np.floor(x_um / cell_um), np.floor(y_um / cell_um)
    noise = np.sin(ix * 12.9898 + iy * 78.233) * 43758.5453
    return (noise - np.floor(noise)) * 1000.0


def write_czi(
    path: Path,
    experiment: FakeExperiment,
    *,
    pixel_um: float,
    at_um=(0.0, 0.0),
    image_to_stage=((1, 0), (0, 1)),
) -> None:
    """A CZI with the experiment's channels, slices and time points: real when pylibCZIrw is here, else a placeholder.

    Each plane shows the pretend sample where the stage stands, seen through
    a camera turned by *image_to_stage*, so a stage move shifts the picture
    as it does on a microscope.
    """
    try:
        import numpy as np
        from pylibCZIrw import czi as pyczi
    except ImportError:
        path.write_bytes(
            b"ZISRAWFILE placeholder written by the pretend ZEN: " + path.name.encode()
        )
        return
    width, height = experiment.frame_px
    cols, rows = np.meshgrid(
        np.arange(width) - width / 2, np.arange(height) - height / 2
    )
    (a, b), (c, d) = image_to_stage
    x_um = at_um[0] + pixel_um * (a * cols + b * rows)
    y_um = at_um[1] + pixel_um * (c * cols + d * rows)
    sample = sample_field(x_um, y_um)
    with pyczi.create_czi(str(path), exist_ok=True) as writer:
        for t in range(experiment.time_points):
            for ch in range(len(experiment.channels)):
                for z in range(experiment.slices):
                    plane = (sample + 100 * t + 10 * ch + z).astype(np.uint16)
                    writer.write(data=plane[..., None], plane={"C": ch, "Z": z, "T": t})
        step_m = (experiment.zstack["interval_um"] / 1e6) if experiment.zstack else 0.0
        writer.write_metadata(
            document_name=path.stem,
            channel_names=dict(enumerate(experiment.channels)),
            scale_x=pixel_um / 1e6,
            scale_y=pixel_um / 1e6,
            scale_z=step_m,
        )


# =============================================================================
# The fake services, with the method names of the zen_api 2025.10.1 stubs
# =============================================================================


def _obj(**fields) -> SimpleNamespace:
    return SimpleNamespace(**fields)


class FakeStageService:
    def __init__(self, scope: FakeScope):
        self.scope = scope

    async def get_position(self, request):
        self.scope._refuse("stage.get_position")
        return _obj(x=self.scope.x_um / 1e6, y=self.scope.y_um / 1e6)

    async def move_to(self, request):
        scope = self.scope
        scope._refuse("stage.move_to")
        scope._note("stage.move_to", x=request.x, y=request.y)
        if scope.travel_s:
            await asyncio.sleep(scope.travel_s)
        with scope.lock:
            if request.x is not None:
                scope.x_um = float(request.x) * 1e6
            if request.y is not None:
                scope.y_um = float(request.y) * 1e6
        return _obj(position_changed=True)


class FakeFocusService:
    def __init__(self, scope: FakeScope):
        self.scope = scope

    async def get_position(self, request):
        self.scope._refuse("focus.get_position")
        return _obj(value=self.scope.z_um / 1e6)

    async def move_to(self, request):
        scope = self.scope
        scope._refuse("focus.move_to")
        scope._note("focus.move_to", value=request.value)
        if scope.travel_s:
            await asyncio.sleep(scope.travel_s)
        with scope.lock:
            scope.z_um = float(request.value) * 1e6
        return _obj(position_changed=True)


class FakeObjectiveChangerService:
    def __init__(self, scope: FakeScope):
        self.scope = scope

    async def get_position(self, request):
        self.scope._refuse("objective.get_position")
        return _obj(value=self.scope.objective_position)

    async def get_objectives(self, request):
        return _obj(
            objectives=[
                _obj(immersion_type=SimpleNamespace(name="AIR"), **one)
                for one in self.scope.objectives
            ]
        )

    async def move_to(self, request):
        scope = self.scope
        scope._refuse("objective.move_to")
        scope._note("objective.move_to", position_index=request.position_index)
        if not any(o["position"] == request.position_index for o in scope.objectives):
            raise FakeGRPCError(
                "OUT_OF_RANGE", f"no objective at position {request.position_index}"
            )
        if scope.travel_s:
            await asyncio.sleep(scope.travel_s)
        scope.objective_position = int(request.position_index)
        return _obj()


class FakeExperimentService:
    def __init__(self, scope: FakeScope):
        self.scope = scope

    async def get_available_experiments(self, request):
        return _obj(experiments=[_obj(name=name) for name in self.scope.experiments])

    async def get_image_output_path(self, request):
        return _obj(image_output_path=self.scope.image_output_folder)

    async def load(self, request):
        scope = self.scope
        scope._refuse("experiment.load")
        scope._note("experiment.load", experiment_name=request.experiment_name)
        if request.experiment_name not in scope.experiments:
            raise FakeGRPCError(
                "NOT_FOUND", f"experiment {request.experiment_name!r} not found"
            )
        experiment_id = f"{request.experiment_name}#{len(scope.loaded) + 1}"
        scope.loaded[experiment_id] = request.experiment_name
        return _obj(experiment_id=experiment_id)

    def _run(self, operation: str, request):
        scope = self.scope
        scope._refuse(operation)
        experiment = scope.experiment_of(request.experiment_id)
        output_name = request.output_name or f"auto_{len(scope.written) + 1}"
        scope._note(
            operation, experiment_id=request.experiment_id, output_name=output_name
        )
        if scope.active_experiment is not None:
            raise FakeGRPCError(
                "FAILED_PRECONDITION", "an experiment is already running"
            )
        scope.active_experiment = request.experiment_id
        scope.status_by_id[request.experiment_id] = _status(True)
        try:
            if scope.exposure_s:
                time.sleep(scope.exposure_s)
            scope.write_czi(request.experiment_id, output_name)
        finally:
            scope.active_experiment = None
        slices = experiment.slices if operation == "experiment.run_experiment" else 1
        time_points = (
            experiment.time_points if operation == "experiment.run_experiment" else 1
        )
        images = len(experiment.channels) * slices * time_points
        scope.status_by_id[request.experiment_id] = _status(
            False,
            images_acquired_index=images,
            images_count=images,
            channels_count=len(experiment.channels),
            zstack_slices_count=slices,
            time_points_count=time_points,
        )
        return _obj(output_name=output_name)

    async def run_experiment(self, request):
        return self._run("experiment.run_experiment", request)

    async def run_snap(self, request):
        return self._run("experiment.run_snap", request)

    async def start_experiment(self, request):
        scope = self.scope
        scope._note("experiment.start_experiment", experiment_id=request.experiment_id)
        scope.active_experiment = request.experiment_id
        scope.status_by_id[request.experiment_id] = _status(True)
        scope.write_czi(request.experiment_id, request.output_name or "auto")
        return _obj(output_name=request.output_name or "auto")

    async def stop(self, request):
        scope = self.scope
        stopped = request.experiment_id or scope.active_experiment or ""
        scope._note("experiment.stop", experiment_id=stopped)
        if stopped:
            scope.status_by_id[stopped] = _status(False)
        scope.active_experiment = None
        return _obj(experiment_id=stopped)

    async def get_status(self, request):
        scope = self.scope
        scope._refuse("experiment.get_status")
        if request.experiment_id:
            status = scope.status_by_id.get(request.experiment_id)
            if status is None:
                scope.experiment_of(
                    request.experiment_id
                )  # raises NOT_FOUND for an unknown id
                status = _status(False, images_acquired_index=0)
            return _obj(status=status)
        if scope.active_experiment is None:
            raise FakeGRPCError("FAILED_PRECONDITION", "there is no active experiment")
        return _obj(status=scope.status_by_id[scope.active_experiment])

    def register_on_status_changed(self, request):
        scope = self.scope
        experiment_id = request.experiment_id or scope.active_experiment

        async def updates():
            yield _obj(status=_status(True))
            yield _obj(status=scope.status_by_id.get(experiment_id, _status(False)))

        return updates()


class FakeZStackService:
    def __init__(self, scope: FakeScope):
        self.scope = scope

    async def is_z_stack_experiment(self, request):
        return _obj(
            is_zstack_experiment=self.scope.experiment_of(request.experiment_id).zstack
            is not None
        )

    async def get_z_stack_info(self, request):
        experiment = self.scope.experiment_of(request.experiment_id)
        if experiment.zstack is None:
            raise FakeGRPCError("FAILED_PRECONDITION", "the experiment has no z-stack")
        stack = experiment.zstack
        return _obj(
            interval=stack["interval_um"] / 1e6,
            first_slice=stack["first_um"] / 1e6,
            last_slice=stack["last_um"] / 1e6,
            range=(stack["last_um"] - stack["first_um"]) / 1e6,
            num_slices=experiment.slices,
            is_center_mode=False,
            offset=0.0,
        )

    async def modify_z_stack_first_last(self, request):
        scope = self.scope
        scope._refuse("zstack.modify_first_last")
        experiment = scope.experiment_of(request.experiment_id)
        scope._note(
            "zstack.modify_first_last",
            first=request.first,
            last=request.last,
            interval=request.interval,
        )
        if experiment.zstack is None:
            raise FakeGRPCError("FAILED_PRECONDITION", "the experiment has no z-stack")
        for key, value in (
            ("first_um", request.first),
            ("last_um", request.last),
            ("interval_um", request.interval),
        ):
            if value is not None:
                experiment.zstack[key] = float(value) * 1e6
        return _obj()


class FakeTrackService:
    def __init__(self, scope: FakeScope):
        self.scope = scope

    async def get_track_info(self, request):
        experiment = self.scope.experiment_of(request.experiment_id)
        return _obj(
            track_info=[
                _obj(
                    is_activated=True,
                    channels=[
                        _obj(name=name, is_activated=True)
                        for name in experiment.channels
                    ],
                )
            ]
        )


class FakeAutofocusService:
    def __init__(self, scope: FakeScope):
        self.scope = scope

    async def find_auto_focus(self, request):
        scope = self.scope
        scope._refuse("autofocus.find")
        scope.experiment_of(request.experiment_id)
        scope._note(
            "autofocus.find",
            experiment_id=request.experiment_id,
            timeout=request.timeout,
        )
        scope.z_um = scope.best_focus_z_um
        return _obj(focus_position=scope.z_um / 1e6)


class FakeDefiniteFocusService:
    def __init__(self, scope: FakeScope):
        self.scope = scope

    async def find_surface(self, request):
        scope = self.scope
        scope._refuse("definite_focus.find_surface")
        scope._note("definite_focus.find_surface")
        scope.z_um = scope.surface_z_um
        return _obj(zposition=scope.z_um / 1e6)

    async def store_focus(self, request):
        self.scope._note("definite_focus.store")
        self.scope.stored_focus_um = self.scope.z_um
        return _obj()

    async def recall_focus(self, request):
        scope = self.scope
        scope._note("definite_focus.recall")
        if scope.stored_focus_um is None:
            raise FakeGRPCError("FAILED_PRECONDITION", "no focus was stored")
        scope.z_um = scope.stored_focus_um
        return _obj(zposition=scope.z_um / 1e6)


SERVICE_FAKES = {
    "stage": FakeStageService,
    "focus": FakeFocusService,
    "objective": FakeObjectiveChangerService,
    "experiment": FakeExperimentService,
    "zstack": FakeZStackService,
    "tracks": FakeTrackService,
    "autofocus": FakeAutofocusService,
    "definite_focus": FakeDefiniteFocusService,
}

#: The fields of each request message the driver fills in, with their empty values.
MESSAGE_FIELDS = {
    "StageServiceMoveToRequest": {"x": None, "y": None},
    "FocusServiceMoveToRequest": {"value": 0.0},
    "ObjectiveChangerServiceMoveToRequest": {"position_index": 0},
    "ExperimentServiceLoadRequest": {"experiment_name": ""},
    "ExperimentServiceRunExperimentRequest": {"experiment_id": "", "output_name": ""},
    "ExperimentServiceRunSnapRequest": {"experiment_id": "", "output_name": ""},
    "ExperimentServiceStopRequest": {"experiment_id": ""},
    "ExperimentServiceGetStatusRequest": {"experiment_id": ""},
    "ExperimentServiceRegisterOnStatusChangedRequest": {"experiment_id": ""},
    "ZStackServiceModifyZStackFirstLastRequest": {
        "experiment_id": "",
        "first": None,
        "last": None,
        "interval": None,
    },
    "ZStackServiceIsZStackExperimentRequest": {"experiment_id": ""},
    "ZStackServiceGetZStackInfoRequest": {"experiment_id": ""},
    "TrackServiceGetTrackInfoRequest": {"experiment_id": ""},
    "ExperimentSwAutofocusServiceFindAutoFocusRequest": {
        "experiment_id": "",
        "timeout": None,
    },
}


def fake_message(path: str) -> SimpleNamespace:
    """An empty request message with the fields the real one has (``"lm.hardware.v2:FocusServiceMoveToRequest"``)."""
    name = path.rpartition(":")[2]
    return SimpleNamespace(**MESSAGE_FIELDS.get(name, {}))


class FakeChannel:
    """The connection: nothing to open, and closing is a coroutine as grpclib's is."""

    async def close(self):
        return None


def build_fake_client(
    scope: FakeScope | None = None, *, call_timeout_s: float = 5.0
) -> tuple[ZenClient, FakeScope]:
    """A real ``ZenClient`` wired to the pretend ZEN; ``(client, scope)``. Close the client when done."""
    scope = scope if scope is not None else FakeScope()
    services = {key: fake(scope) for key, fake in SERVICE_FAKES.items()}
    client = ZenClient(
        metadata=[("control-token", "pretend")],
        channel_factory=FakeChannel,
        stub_factory=lambda key, channel, metadata: services[key],
        message_factory=fake_message,
        call_timeout_s=call_timeout_s,
    )
    client.runtime = {
        "host": "pretend",
        "port": 0,
        "certificate": None,
        "zen_api_version": "pretend",
        "services": {key: "pretend" for key in SERVICE_FAKES},
    }
    client.scope = scope
    return client, scope
