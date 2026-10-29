"""The driver over the real wire: ZEISS's zen_api classes, gRPC and TLS, against a small gateway in this test.

The stand-in in ``helpers/mock_zen_api.py`` replaces the services at the
client. This test replaces nothing in the driver: the request messages are
the ``zen_api`` 2025.10.1 wheel's own, the connection is grpclib's over TLS
with a certificate made for the test, and the control token travels as the
gateway expects it. The server side is built from the wheel's own service
classes (``StageServiceBase`` and the others), so a field name the driver
got wrong fails here. It needs the ``zeiss`` extra and ``cryptography``, and
is skipped without them.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import asyncio
import datetime
import ipaddress
import ssl
import threading
from pathlib import Path

import pytest

pytest.importorskip("zen_api", reason="ZEISS's zen_api package is not installed")
pytest.importorskip("grpclib")
pytest.importorskip("cryptography")

from grpclib import GRPCError
from grpclib.const import Status
from grpclib.events import RecvRequest, listen
from grpclib.server import Server
from mock_zen_api import FakeExperiment, write_czi
from setup_fixtures import provision
from zen_api.acquisition import v1beta as acq
from zen_api.lm.acquisition import v1 as lm_acq
from zen_api.lm.hardware import v2 as hw

from zmart_drivers.zeiss.light_microscope.zen_api_grpc import methods
from zmart_drivers.zeiss.light_microscope.zen_api_grpc.primitives.gateway import (
    client as gateway_client,
)

TOKEN = "the-control-token"


def make_certificates(folder: Path) -> tuple[Path, Path, Path]:
    """A root certificate and a server certificate for 127.0.0.1 signed by it; ``(root, cert, key)``."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    now = datetime.datetime.now(datetime.UTC)
    root_key = ec.generate_private_key(ec.SECP256R1())
    root_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test root")])
    root = (
        x509.CertificateBuilder()
        .subject_name(root_name)
        .issuer_name(root_name)
        .public_key(root_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(root_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")]))
        .issuer_name(root_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(root_key, hashes.SHA256())
    )
    paths = folder / "root.pem", folder / "server.pem", folder / "server.key"
    paths[0].write_bytes(root.public_bytes(serialization.Encoding.PEM))
    paths[1].write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    paths[2].write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return paths


class Microscope:
    """The little instrument behind the test gateway."""

    def __init__(self, images: Path):
        self.x, self.y, self.z, self.objective = 0.02, 0.015, 0.001, 1
        self.images = images
        self.experiments = {"ZMART_Snap": FakeExperiment(channels=["DAPI", "GFP"])}
        self.loaded: dict[str, str] = {}
        self.finished: set[str] = set()
        self.refuse_moves = False


def services(scope: Microscope):
    class Stage(hw.StageServiceBase):
        async def get_position(self, request):
            return hw.StageServiceGetPositionResponse(x=scope.x, y=scope.y)

        async def move_to(self, request):
            if scope.refuse_moves:
                raise GRPCError(
                    Status.PERMISSION_DENIED,
                    "Permission denied - Execution of API methods that can change the system state is currently not allowed",
                )
            if request.x is not None:
                scope.x = request.x
            if request.y is not None:
                scope.y = request.y
            return hw.StageServiceMoveToResponse(position_changed=True)

    class Focus(hw.FocusServiceBase):
        async def get_position(self, request):
            return hw.FocusServiceGetPositionResponse(value=scope.z)

        async def move_to(self, request):
            scope.z = request.value
            return hw.FocusServiceMoveToResponse(position_changed=True)

    class Objectives(hw.ObjectiveChangerServiceBase):
        async def get_objectives(self, request):
            return hw.ObjectiveChangerServiceGetObjectivesResponse(
                objectives=[
                    hw.ObjectiveData(
                        name="Plan-Apochromat 10x/0.45",
                        na=0.45,
                        magnification=10.0,
                        position=1,
                    ),
                    hw.ObjectiveData(
                        name="Plan-Apochromat 20x/0.8",
                        na=0.8,
                        magnification=20.0,
                        position=2,
                    ),
                ]
            )

        async def get_position(self, request):
            return hw.ObjectiveChangerServiceGetPositionResponse(value=scope.objective)

        async def move_to(self, request):
            scope.objective = request.position_index
            return hw.ObjectiveChangerServiceMoveToResponse()

    class Experiments(acq.ExperimentServiceBase):
        async def get_available_experiments(self, request):
            return acq.ExperimentServiceGetAvailableExperimentsResponse(
                experiments=[
                    acq.ExperimentDescriptor(name=name) for name in scope.experiments
                ]
            )

        async def get_image_output_path(self, request):
            return acq.ExperimentServiceGetImageOutputPathResponse(
                image_output_path=str(scope.images)
            )

        async def load(self, request):
            if request.experiment_name not in scope.experiments:
                raise GRPCError(
                    Status.NOT_FOUND, f"experiment {request.experiment_name} not found"
                )
            experiment_id = f"id-{len(scope.loaded) + 1}"
            scope.loaded[experiment_id] = request.experiment_name
            return acq.ExperimentServiceLoadResponse(experiment_id=experiment_id)

        async def run_snap(self, request):
            experiment = scope.experiments[scope.loaded[request.experiment_id]]
            write_czi(
                scope.images / f"{request.output_name}.czi",
                experiment,
                pixel_um=0.65,
                at_um=(scope.x * 1e6, scope.y * 1e6),
            )
            scope.finished.add(request.experiment_id)
            return acq.ExperimentServiceRunSnapResponse(output_name=request.output_name)

        async def get_status(self, request):
            if not request.experiment_id:
                raise GRPCError(
                    Status.FAILED_PRECONDITION, "there is no active experiment"
                )
            done = request.experiment_id in scope.finished
            return acq.ExperimentServiceGetStatusResponse(
                status=acq.ExperimentStatus(
                    is_experiment_running=False,
                    is_acquisition_running=False,
                    images_acquired_index=2 if done else 0,
                    images_count=2,
                    channels_count=2,
                )
            )

    class Tracks(lm_acq.TrackServiceBase):
        async def get_track_info(self, request):
            experiment = scope.experiments[scope.loaded[request.experiment_id]]
            return lm_acq.TrackServiceGetTrackInfoResponse(
                track_info=[
                    lm_acq.TrackInfo(
                        is_activated=True,
                        channels=[
                            lm_acq.ChannelInfo(name=n, is_activated=True)
                            for n in experiment.channels
                        ],
                    )
                ]
            )

    return [Stage(), Focus(), Objectives(), Experiments(), Tracks()]


@pytest.fixture
def gateway(tmp_path):
    """A gateway on 127.0.0.1 over TLS that checks the control token; ``(port, root certificate, microscope)``."""
    root, cert, key = make_certificates(tmp_path)
    images = tmp_path / "zen_images"
    images.mkdir()
    scope = Microscope(images)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    context.set_alpn_protocols(["h2"])
    loop = asyncio.new_event_loop()
    started = threading.Event()
    box = {}

    async def serve():
        server = Server(services(scope))

        async def check_token(event: RecvRequest):
            if dict(event.metadata).get("control-token") != TOKEN:
                raise GRPCError(
                    Status.UNAUTHENTICATED, "missing or wrong control token"
                )

        listen(server, RecvRequest, check_token)
        await server.start("127.0.0.1", 0, ssl=context)
        box["server"] = server
        box["port"] = server._server.sockets[0].getsockname()[1]
        started.set()

    thread = threading.Thread(
        target=lambda: (loop.run_until_complete(serve()), loop.run_forever()),
        daemon=True,
    )
    thread.start()
    assert started.wait(10)
    yield box["port"], root, scope
    loop.call_soon_threadsafe(box["server"].close)
    loop.call_soon_threadsafe(loop.stop)
    thread.join(5)


def test_the_driver_connects_moves_and_snaps_over_the_real_wire(
    gateway, monkeypatch, tmp_path
):
    pytest.importorskip("pylibCZIrw")
    port, root, scope = gateway
    provision()
    monkeypatch.setattr(
        methods,
        "open_connection",
        lambda c: gateway_client.open_gateway(
            c["host"], c["port"], token=c["password"], cert_file=c["cert_file"]
        ),
    )

    microscope = methods.connect(
        host="127.0.0.1",
        port=port,
        password=TOKEN,
        cert_file=str(root),
        experiment="ZMART_Snap",
        output_root=str(tmp_path / "runs"),
    )
    try:
        moved = microscope.move_to(25000.0, 16000.0, 1200.0)
        assert [moved[a]["position"] for a in "xyz"] == pytest.approx(
            [25000.0, 16000.0, 1200.0]
        )
        assert scope.x == pytest.approx(0.025)

        microscope.change_objective(position=2)
        assert scope.objective == 2

        microscope.acquire("A1", acquisition_settings={"trigger": "snap"})
        (saved,) = microscope.save()
        assert Path(saved["store"]).name == "acquisition_A1.ome.zarr"
        assert Path(saved["czi"]).name == "acquisition_A1.czi"
        assert [p["channel"] for p in saved["planes"]] == ["DAPI", "GFP"]

        scope.refuse_moves = True
        with pytest.raises(RuntimeError, match="Unsupervised API Mode"):
            microscope.move_to(26000.0, 16000.0, 1200.0)
        assert microscope.handle.client.runtime["certificate"] == str(root)
    finally:
        microscope.close()


def test_a_wrong_control_token_is_refused_by_the_gateway(gateway):
    port, root, _ = gateway
    client = gateway_client.open_gateway(
        "127.0.0.1", port, token="wrong", cert_file=root
    )
    try:
        from zmart_drivers.zeiss.light_microscope.zen_api_grpc.primitives.gateway import (
            reads,
        )

        with pytest.raises(GRPCError) as refused:
            reads.get_xy(client)
        assert refused.value.status is Status.UNAUTHENTICATED
    finally:
        client.close()


def test_every_request_message_the_driver_names_exists_in_the_wheel():
    from zmart_drivers.zeiss.light_microscope.zen_api_grpc.primitives.gateway.transfer import (
        CALLS,
    )

    for name, call in CALLS.items():
        message = gateway_client.real_message(call.message)
        module, service = gateway_client.SERVICES[call.service]
        stub = getattr(gateway_client.zen_api_module(module), service)
        assert hasattr(stub, call.method), f"{name}: {service} has no {call.method}"
        assert message is not None
