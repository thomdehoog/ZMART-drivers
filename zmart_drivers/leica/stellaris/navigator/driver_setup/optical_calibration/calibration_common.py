"""What the setup measurements share: folders, the scan's geometry, moves, captures, plots.

The orientation measurement and the objective calibration both take
pictures, move the stage and read how big a pixel is. This module holds
those helpers once, written against this driver's own parts: the readings
(``procedures/readings.py``), the changes through the limits gate
(``procedures/changes.py``) and saving from LAS X's cache
(``procedures/save_data``). LAS X's plain Navigator has no jobs, so the
scan's settings are what the Navigator window shows now: the setup
notebooks and the wizard ask you to set the scan up in LAS X first and
then leave it alone while they measure.

It also owns the layout of a measurement's folder (``SessionPaths``), a
safe way to write a JSON file, and the two plots the measurements show:
the two objectives overlaid in magenta and green, and the focus curve.

Path convention: the paths written into a report stay relative to the
measurement's folder, so the folder can be moved; the paths handed back to
a notebook are absolute, so they can be opened.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from zmart_drivers.leica.stellaris.navigator.procedures import changes
from zmart_drivers.leica.stellaris.navigator.procedures import readings as readers
from zmart_drivers.leica.stellaris.navigator.procedures.save_data import save as saving

from ...primitives.direct import client as hardware_server
from ...procedures.save_data.naming import Naming, run_hash
from .. import orientation as _orientation

# matplotlib is imported lazily inside the plot helpers so test imports
# (and headless environments) do not pull in a display backend on import.


# -- Constants ---------------------------------------------------------

STAGING_SCHEMA_VERSION: int = 1
MIN_FOCUS_STACK_SECTIONS: int = 5


# -- Dataclasses -------------------------------------------------------


@dataclass(frozen=True)
class SessionPaths:
    session_root: Path
    session_dir: Path
    configs_dir: Path
    reports_dir: Path
    data_dir: Path


@dataclass
class ImageGeometry:
    image_size_px: tuple[int, int]
    format_px: tuple[int, int]
    pixel_size_um: float
    pixel_w_um: float
    pixel_h_um: float


# -- Path / naming helpers ---------------------------------------------


def make_session_paths(
    session_id: str,
    sessions_root: str | Path,
    *,
    acquisition_name: str | None = None,
) -> SessionPaths:
    # absolute(), not resolve(): mapped-drive letters and symlinks stay
    # as the operator spelled them. resolve() once turned Z:\ into a UNC
    # path on the rig and broke acquisition writes.
    root = Path(sessions_root).absolute()
    session_root = root / session_id
    session_dir = (
        session_root if acquisition_name is None else session_root / acquisition_name
    )
    paths = SessionPaths(
        session_root=session_root,
        session_dir=session_dir,
        configs_dir=session_dir / "configs",
        reports_dir=session_dir / "reports",
        data_dir=session_dir / "data",
    )
    directories = [paths.reports_dir, paths.data_dir]
    if acquisition_name is None:
        directories.append(paths.configs_dir)
    for p in directories:
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RuntimeError(
                f"cannot create calibration session directory {p}: {exc}"
            ) from exc
    return paths


# -- Connecting for a measurement --------------------------------------


def connect_for_setup(client_name: str = "ZMART") -> Any:
    """Reach LAS X's hardware server for a setup measurement, with this machine's limits installed.

    The measurements move the stage through the same limits gate as any
    other change, so they need the limits handshake like a normal session.
    Raises ``RuntimeError`` when LAS X does not answer or the limits cannot
    be installed.
    """
    from ..limits import handshake

    client = hardware_server.connect(client_name)
    limits_state = handshake.connect_handshake(client)
    if not limits_state.ok:
        raise RuntimeError(limits_state.error)
    if readers.get_hardware_info(client) is None:
        raise RuntimeError("LAS X did not describe the instrument; is LAS X running with its Navigator shown?")
    return client


# -- The scan's geometry -------------------------------------------------


def read_scan_geometry(
    client: Any,
    image: np.ndarray | None = None,
) -> ImageGeometry:
    """How big a pixel is and how many there are, as LAS X is set now; square pixels only.

    With *image*, its own height and width are recorded as the image size;
    without one, the format LAS X reports is used.
    """
    pixel = readers.get_pixel_size(client)
    fmt = readers.get_format(client)
    if pixel is None or fmt is None:
        raise ValueError("LAS X did not report the pixel size and the image format")
    pixel_w, pixel_h = float(pixel[0]) * 1e6, float(pixel[1]) * 1e6
    if not np.isclose(pixel_w, pixel_h, rtol=0, atol=1e-9):
        raise ValueError(
            f"non-square pixels are not supported "
            f"(pixel_w_um={pixel_w}, pixel_h_um={pixel_h})"
        )
    try:
        columns, rows = (int(v) for v in str(fmt).lower().split("x"))
    except ValueError:
        raise ValueError(f"the image format {fmt!r} is not 'columns x rows'") from None
    format_px = (rows, columns)
    if image is not None:
        h, w = image.shape[-2:]
        image_size_px = (int(h), int(w))
    else:
        image_size_px = format_px
    return ImageGeometry(
        image_size_px=image_size_px,
        format_px=format_px,
        pixel_size_um=pixel_w,
        pixel_w_um=pixel_w,
        pixel_h_um=pixel_h,
    )


def assert_geometry_matches(
    actual: ImageGeometry,
    expected_size_px: tuple[int, int],
    expected_pixel_size_um: float,
    *,
    context: str,
) -> None:
    if tuple(actual.image_size_px) != tuple(expected_size_px):
        raise ValueError(
            f"{context}: image size mismatch "
            f"({tuple(actual.image_size_px)} vs {tuple(expected_size_px)})"
        )
    if not np.isclose(actual.pixel_size_um, expected_pixel_size_um, rtol=0, atol=1e-9):
        raise ValueError(
            f"{context}: pixel size mismatch ({actual.pixel_size_um} vs {expected_pixel_size_um})"
        )


# -- Move helpers ------------------------------------------------------


def move_xy_and_verify(
    client: Any,
    x_um: float,
    y_um: float,
    *,
    settle_s: float = 0.5,
    tolerance_um: float = 0.5,
) -> None:
    result = changes.move_xy(client, x_um, y_um, tolerance=tolerance_um)
    if not result or not result.get("success"):
        raise RuntimeError(f"move_xy failed: {result.get('message') if result else result}")
    if settle_s > 0:
        time.sleep(settle_s)
    xy = readers.get_xy(client) or {}
    if ("x_um" not in xy) or ("y_um" not in xy):
        raise RuntimeError(f"get_xy returned no readback: {xy}")
    if abs(xy["x_um"] - x_um) > tolerance_um or abs(xy["y_um"] - y_um) > tolerance_um:
        raise RuntimeError(
            f"stage readback outside tolerance: requested "
            f"({x_um}, {y_um}), got ({xy['x_um']}, {xy['y_um']})"
        )


def move_zwide_and_verify(
    client: Any,
    z_um: float,
    *,
    tolerance_um: float = 1.0,
) -> None:
    """Move z-wide to *z_um* and insist that LAS X read it back there."""
    result = changes.move_z(client, "z-wide", z_um, tolerance=tolerance_um)
    if not result or not result.get("success"):
        raise RuntimeError(f"move_z zwide failed: {result.get('message') if result else result}")
    if result.get("confirmed") is not True:
        raise RuntimeError(f"move_z zwide remained unconfirmed: {result}")


def read_active_objective(
    client: Any,
    known_names: dict[int, str] | None = None,
) -> tuple[int, str]:
    """The objective in the light path now, as ``(slot, name)``, changing nothing.

    *known_names* gives a name per slot for when LAS X reports none.
    """
    objective = readers.get_objective(client) or {}
    slot = objective.get("slotIndex")
    if slot is None:
        raise RuntimeError("could not read which objective is in the light path")
    slot = int(slot)
    name = str(objective.get("name") or (known_names or {}).get(slot) or "").strip()
    if not name:
        raise RuntimeError(f"could not read the name of the objective in slot {slot}")
    return slot, name


# -- Acquisition helper ------------------------------------------------


def acquire_frame_to(
    session: Any,
    name: str,
    *,
    orientation=None,
) -> np.ndarray:
    """Acquire one frame, save into ``session.paths.data_dir``, track paths.

    Any parent directories implied by ``name`` are created as needed.
    ``orientation`` is threaded to the driver save path; ``None`` defaults
    to the rig orientation. Pass ``Orientation()`` for a raw, unreoriented
    frame (the orientation measurement acquires raw frames so it always
    measures the physical rig).
    """
    saved = _capture_for_calibration(
        session,
        name=name,
        # Short: the session already lies in the calibration folder, and its
        # stores' chunks lie deep (Windows stops at 260 characters).
        folder="frame",
        orientation=orientation,
    )
    img = _single_plane_image(saved, context=name)
    _idx, exported_path = next(iter(saved.image_paths.items()))
    rel = str(Path(exported_path).relative_to(session.paths.session_dir)).replace(
        "\\", "/"
    )
    session.raw_files[name] = rel
    session.exported_files[name] = str(exported_path)
    return img


def acquire_stack_to(
    session: Any,
    dirname: str,
    *,
    orientation=None,
) -> np.ndarray:
    """Take the z-stack set up in LAS X's window, save its slices, track their paths.

    The measurement never sets the stack up (its range, step and number of
    sections): it only takes the picture LAS X is set to take, and keeps
    what comes back. The saved picture must be ``(slices, height, width)``;
    anything else is an error, with the likely cause in words.

    Canonical planes and metadata land below
    ``session.paths.data_dir / dirname`` and are tracked under
    acquisition-relative ``raw_files`` keys.
    ``orientation`` is threaded to the driver save path (``None`` = rig
    orientation).
    """
    saved = _capture_for_calibration(
        session,
        name=dirname,
        folder="stack",
        orientation=orientation,
    )
    arr = _stack_from_saved_planes(saved, context=dirname)
    if arr.ndim != 3:
        raise ValueError(
            f"acquire_stack expected 3-D (slices, H, W); got shape "
            f"{arr.shape!r}. LAS X may not be set to take a z-stack (its scan "
            "mode and the z-stack's begin and end), or the picture is not laid out as expected."
        )
    for i, (_idx, slice_path) in enumerate(sorted(saved.image_paths.items())):
        rel = str(Path(slice_path).relative_to(session.paths.session_dir)).replace(
            "\\", "/"
        )
        session.raw_files[f"{dirname}/z_{i:03d}"] = rel
    session.exported_files[dirname] = ";".join(
        str(p) for _idx, p in sorted(saved.image_paths.items())
    )
    return arr


def _capture_for_calibration(
    session: Any,
    *,
    name: str,
    folder: str,
    orientation=None,
):
    """One capture through the driver's own capture and save, placed where the stage stands."""
    if orientation is None:
        orientation = _orientation.rig_orientation()
    position_label = f"{len(session.exported_files):06d}"
    naming = Naming(stem=f"{folder}_{run_hash()}_{position_label}")
    at = stage_now(session.client)
    acq = changes.take_picture(session.client)
    (saved,) = saving.save(
        acq,
        session.paths.data_dir / name,
        [naming],
        orientation=orientation,
        place=saving.placed_at({"x_um": at["x"], "y_um": at["y"], "z_um": at["z"]}),
    )
    return saved


def stage_now(client: Any) -> dict:
    """Where the stage stands, in its own readings: ``x``, ``y``, ``z`` and the two z drives.

    The setup runs before there is an origin, so its captures are placed by
    the stage's own numbers; ``z`` is the focus, the sum of the two z drives.
    Read just before a capture, so the save can say where it was taken.
    """
    xy = readers.get_xy(client) or {}
    if ("x_um" not in xy) or ("y_um" not in xy):
        raise RuntimeError(f"get_xy returned no readback: {xy}")
    z_wide = readers.read_zwide_um(client)
    z_galvo = readers.read_z_um(client, "z-galvo")
    if z_wide is None or z_galvo is None:
        raise RuntimeError("LAS X did not report where its z drives stand")
    return {
        "x": float(xy["x_um"]),
        "y": float(xy["y_um"]),
        "z": z_wide + z_galvo,
        "z_wide_um": z_wide,
        "z_galvo_um": z_galvo,
    }


def _single_plane_image(saved: Any, *, context: str) -> np.ndarray:
    if len(saved.image_paths) != 1:
        raise ValueError(
            f"{context} expected one saved plane; got {len(saved.image_paths)}"
        )
    index = next(iter(saved.image_paths))
    img = np.asarray(saved.read_plane(index))
    if img.ndim == 3 and img.shape[0] == 1:
        img = img[0]
    if img.ndim != 2:
        raise ValueError(f"{context} expected 2-D image; got shape {img.shape!r}")
    return img


def _stack_from_saved_planes(saved: Any, *, context: str) -> np.ndarray:
    if not saved.image_paths:
        raise ValueError(f"{context} saved no image planes")
    channels = sorted({idx.c for idx in saved.image_paths})
    times = sorted({idx.t for idx in saved.image_paths})
    if len(channels) != 1 or len(times) != 1:
        raise ValueError(
            f"{context} expected one channel and one timepoint; "
            f"got channels={channels}, times={times}"
        )
    planes = [
        idx for idx in saved.image_paths if idx.c == channels[0] and idx.t == times[0]
    ]
    arr = np.asarray(
        [saved.read_plane(idx) for idx in sorted(planes, key=lambda i: i.z)]
    )
    return arr


def read_stack_z_positions(
    client: Any,
    expected_slices: int,
    *,
    override: list[float] | None = None,
) -> list[float]:
    """Where each slice of the z-stack was taken: z-wide positions in micrometres, in slice order.

    The positions are never guessed from the number of slices alone. With
    *override* they are taken as given (and checked). Otherwise the z-stack
    LAS X is set to is read from its state tree (``readings.get_z_stack``):
    its ``begin``, ``end`` and ``sections``, spaced evenly, which keeps a
    stack that runs downwards (``begin > end``) as it is. The stack must be
    on z-wide, because the calibration records focus as a z-wide position.
    """
    if override is not None:
        if len(override) != expected_slices:
            raise ValueError(
                f"override z_positions_um has length {len(override)}, "
                f"expected {expected_slices} (one per acquired slice)."
            )
        if len(override) < MIN_FOCUS_STACK_SECTIONS:
            raise ValueError(
                f"z-stack must have at least {MIN_FOCUS_STACK_SECTIONS} "
                "positions for the trimmed parabolic "
                f"peak refinement; override has {len(override)}."
            )
        return [float(z) for z in override]

    stack = readers.get_z_stack(client)
    if not stack:
        raise RuntimeError(
            "LAS X is not set to take a z-stack. Set one up in LAS X's window "
            "(begin, end and sections on z-wide), or pass z_positions_um=[...] to override."
        )
    begin, end, sections = stack.get("begin"), stack.get("end"), stack.get("sections")
    if begin is None or end is None or sections is None:
        raise RuntimeError(
            "the z-stack LAS X reports is missing its begin, end or sections "
            f"(got begin={begin!r}, end={end!r}, sections={sections!r}). "
            "Re-check the z-stack in LAS X."
        )
    try:
        sections_int = int(sections)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"z-stack sections {sections!r} is not an integer.") from exc
    if sections_int != expected_slices:
        raise RuntimeError(
            f"LAS X reports a z-stack of {sections_int} sections, but the picture "
            f"has {expected_slices} slices. Re-check the z-stack in LAS X."
        )
    if sections_int < MIN_FOCUS_STACK_SECTIONS:
        raise RuntimeError(
            f"z-stack must have at least {MIN_FOCUS_STACK_SECTIONS} slices "
            "for trimmed parabolic peak refinement; LAS X reports "
            f"sections={sections_int}. Increase the stack sections in LAS X."
        )
    drive = stack.get("mode")
    if drive is not None and "wide" not in str(drive).lower():
        raise RuntimeError(
            f"Calibration needs the z-stack on z-wide; LAS X reports it on {drive!r}. "
            "Change the stack's drive in LAS X."
        )
    positions = np.linspace(float(begin), float(end), sections_int)
    return [float(z) for z in positions]


# -- JSON I/O ----------------------------------------------------------


def write_json_atomic(path: Path, payload: dict) -> None:
    """Atomic JSON write. ``allow_nan=False`` so NaN/inf never reach disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# -- Visualization helpers --------------------------------------------


def _overlay_rgb(ref_norm: np.ndarray, tgt_norm: np.ndarray) -> np.ndarray:
    """Combine two normalised images into one colour picture.

    The reference is drawn in magenta and the target in green. Where the
    two images carry the same structure the colours add up to white/grey,
    so any misalignment stands out as coloured fringes.
    """
    h = max(ref_norm.shape[0], tgt_norm.shape[0])
    w = max(ref_norm.shape[1], tgt_norm.shape[1])
    rgb = np.zeros((h, w, 3), dtype=np.float64)
    rgb[: ref_norm.shape[0], : ref_norm.shape[1], 0] = ref_norm  # magenta = R + B
    rgb[: ref_norm.shape[0], : ref_norm.shape[1], 2] = ref_norm
    rgb[: tgt_norm.shape[0], : tgt_norm.shape[1], 1] = tgt_norm  # green
    return np.clip(rgb, 0.0, 1.0)


def plot_overlay(
    ref: np.ndarray,
    tgt: np.ndarray,
    title: str,
    *,
    subtitle: str | None = None,
    corrected_target: np.ndarray | None = None,
    corrected_title: str = "Acquisition after stage correction",
):
    """Magenta = reference, green = target. Returns the matplotlib Figure.

    When *corrected_target* is given, the second panel shows that separately
    acquired image. This helper never shifts image pixels to imitate a stage
    correction.
    """
    import matplotlib.pyplot as plt

    def _norm(img: np.ndarray) -> np.ndarray:
        a = img.astype(np.float64)
        lo, hi = float(a.min()), float(a.max())
        if hi - lo < 1e-12:
            return np.zeros_like(a)
        return (a - lo) / (hi - lo)

    r = _norm(ref)
    t = _norm(tgt)

    if corrected_target is not None:
        fig, (ax, ax_corrected) = plt.subplots(1, 2, figsize=(12, 6))
    else:
        fig, ax = plt.subplots(figsize=(6, 6))

    ax.imshow(_overlay_rgb(r, t), origin="upper")
    ax.set_title(title if subtitle is None else f"{title}\n{subtitle}")
    ax.set_axis_off()

    if corrected_target is not None:
        corrected = _norm(corrected_target)
        ax_corrected.imshow(_overlay_rgb(r, corrected), origin="upper")
        ax_corrected.set_title(corrected_title)
        ax_corrected.set_axis_off()

    fig.tight_layout()
    return fig


def plot_brenner_curve(
    z_positions_um: list[float],
    scores: list[float],
    peak_z_um: float,
    *,
    focus_image: np.ndarray | None = None,
):
    """Plot Brenner score vs. z and mark the peak. Returns the Figure.

    When *focus_image* is given (the stack slice closest to the fitted
    peak), it is shown next to the curve so the operator can see the
    picture the microscope considered sharpest. That is a quick sanity
    check: the slice should look like the sample in focus, not like an
    empty field or an artifact.
    """
    import matplotlib.pyplot as plt

    if focus_image is None:
        fig, ax = plt.subplots(figsize=(9, 6))
    else:
        fig, (ax, ax_img) = plt.subplots(1, 2, figsize=(16, 7))
    ax.plot(z_positions_um, scores, marker="o")
    ax.axvline(
        peak_z_um, color="red", linestyle="--", label=f"peak z = {peak_z_um:.3f} um"
    )
    ax.set_xlabel("z-wide (um, absolute)")
    ax.set_ylabel("Brenner Gradient Score")
    ax.set_title("Software Autofocus")
    ax.legend(loc="best")
    if focus_image is not None:
        ax_img.imshow(focus_image, cmap="gray", origin="upper")
        ax_img.set_title(f"Focus positions (Z = {peak_z_um:.2f} µm)")
        ax_img.set_axis_off()
    fig.tight_layout()
    return fig
