"""What the picture-based setup measurements share: their folders, a verified move, and a capture read back.

The orientation measurement and the objective calibration both take pictures,
move the stage between them and compare what they see. This file is the one
place where they touch the microscope, so each measurement reads the same way
whichever microscope it runs on:

- ``SessionPaths``: the folders of one measurement (its data and its reports);
- ``move_xy_and_verify`` and ``move_focus_and_verify``: a move through the
  driver's limits, then a read-back that it arrived;
- ``acquire_frame_to`` and ``acquire_stack_to``: run the imaging set up in
  FLUOVIEW, read the picture back, save it as OME-Zarr in the measurement's
  folder (the same save every capture gets), and answer its pixels;
- ``read_capture_geometry`` and ``read_stack_z_positions``: what the picture
  itself says about its pixel size and the height of each slice;
- the plots the notebooks and the wizard show.

FLUOVIEW reports a picture's pixel size and slice heights inside the picture
(not in its settings), so both are taken from the capture just made.

Adapted from the Leica driver's calibration_common, which these measurements
were first written for.

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

from .. import orientation as _orientation
from ...procedures import changes
from ...procedures import readings as readers
from ...procedures.acquire import PendingCapture
from ...procedures.save_data import ome_zarr
from ...procedures.save_data import save as saving
from ...procedures.save_data.naming import Naming, run_hash

# matplotlib is imported inside the plot helpers, so importing this file never needs a display.


# -- Constants ---------------------------------------------------------

STAGING_SCHEMA_VERSION: int = 1
#: The fewest slices a focus stack may have: the sharpest plane is found by fitting a curve through them.
MIN_FOCUS_STACK_SECTIONS: int = 5


# -- What a measurement keeps ------------------------------------------


@dataclass(frozen=True)
class SessionPaths:
    session_root: Path
    session_dir: Path
    configs_dir: Path
    reports_dir: Path
    data_dir: Path


@dataclass
class ImageGeometry:
    """The size of a picture in pixels, as (rows, columns), and the size of one pixel in micrometres."""

    image_size_px: tuple[int, int]
    format_px: tuple[int, int]
    pixel_size_um: float
    pixel_w_um: float
    pixel_h_um: float


def make_session_paths(
    session_id: str,
    sessions_root: str | Path,
    *,
    acquisition_name: str | None = None,
) -> SessionPaths:
    """The folders of one measurement, made on disk: ``<root>/<session>[/<acquisition>]/{data,reports}``."""
    # absolute(), not resolve(): a mapped drive letter stays as the operator spelled it.
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


# -- The picture's geometry -------------------------------------------


def read_capture_geometry(
    session: Any, image: np.ndarray | None = None
) -> ImageGeometry:
    """The pixel size and size of the capture just made, as FLUOVIEW wrote them into the picture.

    Pixels that are not square are refused: every measurement here turns a
    shift in pixels into micrometres with one number.
    """
    record = getattr(session, "last_capture_record", None) or {}
    pixel = record.get("pixel_size_um")
    size = record.get("image_size_px")
    if not pixel or not size:
        raise ValueError(
            "the picture did not say its pixel size; FLUOVIEW should write it into every picture"
        )
    pixel_w, pixel_h = float(pixel[0]), float(pixel[1])
    if not np.isclose(pixel_w, pixel_h, rtol=0, atol=1e-9):
        raise ValueError(
            f"pixels that are not square are not supported (x {pixel_w} um, y {pixel_h} um)"
        )
    format_px = (int(size[1]), int(size[0]))
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
    """Refuse a picture whose size or pixel size changed since the first one of the measurement."""
    if tuple(actual.image_size_px) != tuple(expected_size_px):
        raise ValueError(
            f"{context}: image size mismatch "
            f"({tuple(actual.image_size_px)} vs {tuple(expected_size_px)})"
        )
    if not np.isclose(actual.pixel_size_um, expected_pixel_size_um, rtol=0, atol=1e-9):
        raise ValueError(
            f"{context}: pixel size mismatch ({actual.pixel_size_um} vs {expected_pixel_size_um})"
        )


# -- Moves ---------------------------------------------------------------


def move_xy_and_verify(
    client: Any,
    x_um: float,
    y_um: float,
    *,
    settle_s: float = 0.5,
    tolerance_um: float = 0.5,
) -> None:
    """Move the stage, in FLUOVIEW's coordinates, wait *settle_s*, and refuse a read-back off by more than *tolerance_um*."""
    result = changes.move_xy(client, x_um, y_um, tolerance_um=tolerance_um)
    if not result or not result.get("success"):
        raise RuntimeError(f"move_xy failed: {(result or {}).get('message')}")
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


def move_focus_and_verify(
    client: Any, z_um: float, *, tolerance_um: float = 1.0
) -> None:
    """Move the focus motor to *z_um*, in FLUOVIEW's coordinates, and refuse a move that was not confirmed."""
    result = changes.move_z(client, z_um, tolerance_um=tolerance_um)
    if not result or not result.get("success"):
        raise RuntimeError(f"move_z failed: {(result or {}).get('message')}")
    if result.get("confirmed") is not True:
        raise RuntimeError(f"move_z was not confirmed: {result.get('message')}")


def read_focus_um(client: Any) -> float:
    """The focus motor, in micrometres, in FLUOVIEW's coordinates."""
    z = readers.get_z(client)
    if z is None:
        raise RuntimeError("could not read the focus position from FLUOVIEW")
    return float(z)


def read_active_objective(
    client: Any, known_names: dict[int, str] | None = None
) -> tuple[int, str]:
    """The objective in use, as (slot, name), changing nothing."""
    objective = readers.get_objective(client) or {}
    slot = objective.get("slot")
    if slot is None:
        raise RuntimeError("could not read the objective in use from FLUOVIEW")
    slot = int(slot)
    name = str(objective.get("name") or (known_names or {}).get(slot) or "").strip()
    if not name:
        raise RuntimeError(f"could not read the name of the objective in slot {slot}")
    return slot, name


def objectives_by_slot(client: Any) -> dict[int, str]:
    """The objectives on the nosepiece, by slot, with their names."""
    return {
        int(entry["slot"]): str(entry.get("name") or "").strip()
        or f"slot {entry['slot']}"
        for entry in readers.get_objectives(client) or []
    }


# -- Captures --------------------------------------------------------------


def stage_now(client: Any) -> dict:
    """Where the stage stands, in FLUOVIEW's own readings, in the shape a capture is placed with.

    The setup runs before there is an origin, so its captures are placed by
    the stage's own numbers: ``x``, ``y`` and ``z`` (the focus motor), with
    the motor and piezo readings beside them. Read just before a capture, so
    the save can say where it was taken.
    """
    xy = readers.get_xy(client) or {}
    if ("x_um" not in xy) or ("y_um" not in xy):
        raise RuntimeError(f"get_xy returned no readback: {xy}")
    z = read_focus_um(client)
    piezo = readers.read_now("z_piezo", client)
    return {
        "x": float(xy["x_um"]),
        "y": float(xy["y_um"]),
        "z": z,
        "z_motor_um": z,
        "z_piezo_um": float(piezo) if piezo is not None else 0.0,
        "objective": readers.get_objective(client),
    }


def _capture_for_calibration(session: Any, *, name: str, folder: str, orientation=None):
    """Run the imaging set up in FLUOVIEW and save it as every capture is saved; answer (record, saved).

    ``orientation`` None uses the microscope's measured orientation; pass
    ``Orientation()`` for the picture as the scanner delivered it.
    """
    if orientation is None:
        orientation = _orientation.rig_orientation()
    position_label = f"{len(session.exported_files):06d}"
    at = stage_now(session.client)
    acq = changes.take_picture(session.client)
    pending = PendingCapture(
        acq=acq,
        label=position_label,
        acquisition_type=folder,
        naming=Naming(stem=f"{folder}_{run_hash()}_{position_label}"),
        state={},
        at=at,
        output_root=Path(session.paths.data_dir) / name,
        session_hash6=run_hash(),
        asked={},
        settings={"trigger": "full"},
    )
    record, saved = saving.save_pending(
        session.client, pending, orientation=orientation, projection="none"
    )
    session.last_capture_record = record
    return record, saved


def _planes_of_first_channel(record: dict) -> list[dict]:
    planes = [
        p for p in record.get("planes") or [] if p.get("c") == 0 and p.get("t") == 0
    ]
    return sorted(planes, key=lambda p: p["z"])


def acquire_frame_to(session: Any, name: str, *, orientation=None) -> np.ndarray:
    """Capture one picture, save it in the measurement's ``data`` folder, and answer its pixels.

    A picture with several channels is measured on its first channel, and a
    single plane is expected: a measurement picture is one plane of one
    channel. The store's path is kept in ``session.exported_files[name]``.
    """
    record, saved = _capture_for_calibration(
        session, name=name, folder="frame", orientation=orientation
    )
    planes = _planes_of_first_channel(record)
    if len(planes) != 1:
        raise ValueError(
            f"{name} expected one plane; FLUOVIEW took {len(planes)}. Switch the z-stack off in FLUOVIEW."
        )
    store = Path(saved.store)
    img = np.asarray(ome_zarr.read_plane(store, (0, 0, 0)))
    session.raw_files[name] = str(store.relative_to(session.paths.session_dir)).replace(
        "\\", "/"
    )
    session.exported_files[name] = str(store)
    return img


def acquire_stack_to(session: Any, dirname: str, *, orientation=None) -> np.ndarray:
    """Run the z-stack set up in FLUOVIEW, save it, and answer its slices, lowest first, as (slices, rows, columns).

    The measurement never sets the stack up (its range, step and number of
    slices); it only runs what the operator set up in FLUOVIEW. The height of
    each slice is kept in ``session.last_stack_z_um``.
    """
    record, saved = _capture_for_calibration(
        session, name=dirname, folder="stack", orientation=orientation
    )
    planes = _planes_of_first_channel(record)
    if len(planes) < 2:
        raise ValueError(
            f"{dirname} expected a z-stack; FLUOVIEW took {len(planes)} plane. Switch the z-stack on in FLUOVIEW."
        )
    store = Path(saved.store)
    arr = np.asarray([ome_zarr.read_plane(store, (0, 0, p["z"])) for p in planes])
    session.last_stack_z_um = [float(p["z_um"]) for p in planes]
    rel = str(store.relative_to(session.paths.session_dir)).replace("\\", "/")
    for i in range(len(planes)):
        session.raw_files[f"{dirname}/z_{i:03d}"] = rel
    session.exported_files[dirname] = str(store)
    return arr


def read_stack_z_positions(
    session: Any, expected_slices: int, *, override: list[float] | None = None
) -> list[float]:
    """The focus height of each slice of the stack just taken, lowest first, in micrometres.

    The heights come from the picture itself (FLUOVIEW writes where the stack
    began and ended), never from the slice count alone. *override* replaces
    them, for a test or a check by hand.
    """
    positions = (
        override if override is not None else getattr(session, "last_stack_z_um", None)
    )
    if positions is None:
        raise RuntimeError("no z-stack has been taken in this measurement yet")
    if len(positions) != expected_slices:
        raise ValueError(
            f"the stack has {expected_slices} slices but {len(positions)} heights are known"
        )
    if len(positions) < MIN_FOCUS_STACK_SECTIONS:
        raise RuntimeError(
            f"a focus stack needs at least {MIN_FOCUS_STACK_SECTIONS} slices to find the sharpest one; "
            f"this one has {len(positions)}. Set more slices in FLUOVIEW."
        )
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
