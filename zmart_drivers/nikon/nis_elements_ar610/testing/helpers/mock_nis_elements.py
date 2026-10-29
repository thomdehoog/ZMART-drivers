"""A mock NIS-Elements behind the real bridge server, for the offline suite.

The bridge ships a fake NIS-Elements of its own (``nis_bridge.fake.FakeNisApi``):
a microscope in memory whose limits, objectives and optical configurations
match the Ti2 simulator. :class:`MockNisElements` builds on it and adds what
the driver's tests need to see the driver work:

- a stage that takes a moment to arrive, so a read-back taken too early
  sees the old position and the confirmation windows do real work;
- a sample: a fixed texture under the objective, so a picture shows a part
  of it that depends on where the stage is, through a chosen camera turn,
  and a stage step shifts the picture as it does at a microscope;
- a way to refuse the next changes as busy, and to make the stage never
  arrive, so the dispatcher's retries and the unconfirmed path can be tested.

:func:`served` puts the real bridge server in front of a mock on a free local
port, exactly as the bridge does inside NIS-Elements, so the driver is tested
end to end over the socket, with the real protocol.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

import numpy as np
import tifffile
from nis_bridge.fake import FakeNisApi, running_bridge
from nis_bridge.nis_dll import NisError

#: The camera's picture, rows then columns.
IMAGE_SHAPE = (48, 64)
#: The pixel size on the sample under each objective of the fake nosepiece,
#: in micrometres: 6.5 um camera pixels behind a 10x, 20x and 60x lens.
PIXEL_SIZE_UM = {1: 0.65, 2: 0.325, 4: 0.108}
#: The sample texture: one texel per micrometre, a few millimetres across.
TEXTURE_SHAPE = (2400, 2400)
TEXTURE_UM_PER_TEXEL = 1.0


#: What the mock writes as the picture's own description, standing in for NIS-Elements'.
NIS_DESCRIPTION = "<mock NIS-Elements picture description>"


class MockNisElements(FakeNisApi):
    """The bridge's fake Ti2, with a stage that travels, a textured sample and ways to misbehave.

    ``travel_s`` is how long a move takes to arrive. ``turn`` is the camera's
    mounting: ``(degrees, mirrored)``, as ``driver_setup/orientation`` names
    it. ``busy_answers`` refuses that many of the next changes as busy.
    ``never_arrives`` makes every move stop short, so it is never confirmed.
    ``calls`` and ``captures`` count what reached the microscope.
    """

    def __init__(
        self,
        *,
        pfs: bool = True,
        calibrated: bool = True,
        travel_s: float = 0.05,
        turn=(0, False),
    ):
        super().__init__(pfs=pfs, calibrated=calibrated)
        self.travel_s = travel_s
        self.turn = turn
        self.busy_answers = 0
        self.never_arrives = False
        self.image_shape = IMAGE_SHAPE
        self._from = dict(self.position)
        self._to = dict(self.position)
        self._moved_at = time.monotonic() - 10.0
        self.texture = np.random.default_rng(7).random(TEXTURE_SHAPE)

    # --- the stage, on its way -----------------------------------------------------

    def get_position(self) -> dict[str, float]:
        """Where the stage is now: where it was until the move's time has passed, then the target.

        The stage arrives in one step rather than gliding, so a test can
        compare positions exactly once it has arrived, while a read-back
        taken too early still sees the old position, as at a microscope.
        """
        arrived = time.monotonic() - self._moved_at >= self.travel_s
        now = dict(self._to if arrived else self._from)
        self.position = now
        return dict(now)

    def _start_move(self, **target: float) -> None:
        if self.busy_answers > 0:
            self.busy_answers -= 1
            raise NisError("StgMove: the stage is busy (-1)")
        self._refuse_beyond_limits(**target)
        here = self.get_position()
        self._from = dict(here)
        self._to = {**here, **target}
        if self.never_arrives:
            # Stops a little short of every target, on every axis asked.
            self._to = {
                axis: here[axis] + (self._to[axis] - here[axis]) * 0.5
                for axis in self._to
            }
        self._moved_at = time.monotonic()

    def move_xyz(self, x: float, y: float, z: float) -> None:
        self.calls.append(f"move_xyz({x:g},{y:g},{z:g})")
        self._start_move(x=x, y=y, z=z)

    def move_xy(self, x: float, y: float) -> None:
        self.calls.append(f"move_xy({x:g},{y:g})")
        self._start_move(x=x, y=y)

    def move_z(self, z: float) -> None:
        self.calls.append(f"move_z({z:g})")
        self._start_move(z=z)

    def wait_for_pfs(self, timeout_s: float) -> None:
        self._from = self._to = {**self.get_position(), "z": self.focal_plane_z}

    def autofocus(self, range_um: float, speed: int) -> int:
        self.calls.append(f"autofocus({range_um:g},{speed})")
        if self.autofocus_result == 1:
            here = self.get_position()
            self._from = self._to = {**here, "z": here["z"] + 3.0}
        return self.autofocus_result

    # --- the sample under the camera -------------------------------------------------

    def pixel_size_um(self) -> float:
        return PIXEL_SIZE_UM.get(self.nosepiece, 0.0) if self.calibrated else 0.0

    def picture(self) -> np.ndarray:
        """What the camera sees now: the part of the sample under the objective, through the camera's turn.

        Columns of the sample view run with stage x and rows with stage y;
        the camera's turn is then undone on it, so the raw picture is what a
        camera mounted that way would give. Without a pixel calibration the
        view is rendered at the 10x pixel size anyway.
        """
        pixel_um = PIXEL_SIZE_UM.get(self.nosepiece, 0.65)
        here = self.get_position()
        rows, columns = self.image_shape
        # The sample's texels under each pixel, from the field's centre.
        ys = (
            here["y"] + (np.arange(rows) - (rows - 1) / 2) * pixel_um
        ) / TEXTURE_UM_PER_TEXEL
        xs = (
            here["x"] + (np.arange(columns) - (columns - 1) / 2) * pixel_um
        ) / TEXTURE_UM_PER_TEXEL
        rows_at = np.clip(
            np.round(ys).astype(int) + TEXTURE_SHAPE[0] // 2, 0, TEXTURE_SHAPE[0] - 1
        )
        columns_at = np.clip(
            np.round(xs).astype(int) + TEXTURE_SHAPE[1] // 2, 0, TEXTURE_SHAPE[1] - 1
        )
        aligned = self.texture[np.ix_(rows_at, columns_at)]
        degrees, mirrored = self.turn
        raw = np.rot90(
            aligned, k=degrees // 90
        )  # undo a clockwise turn: turn counter-clockwise
        if mirrored:
            raw = np.fliplr(raw)
        return (raw * 4000 + 100).astype(np.uint16)

    def save_tiff(self, path: str) -> None:
        if not self.open_images:
            raise NisError("ImageSaveAs: no image is open")
        image = self.frame if self.frame is not None else self.picture()
        # NIS-Elements writes its own description into the picture; the mock writes a short one.
        tifffile.imwrite(
            path,
            np.asarray(image).astype(np.uint16),
            description=NIS_DESCRIPTION,
            metadata=None,
        )


@contextmanager
def served(mock: MockNisElements) -> Iterator[int]:
    """The real bridge server on a free local port with *mock* behind it; yields the port."""
    with running_bridge(mock) as server:
        yield server.server_address[1]
