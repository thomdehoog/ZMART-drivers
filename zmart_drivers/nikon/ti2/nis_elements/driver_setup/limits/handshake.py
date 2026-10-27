"""The connect-time limits handshake: find the saved limits, check them against NIS-Elements, hand them to the gate.

The gate in ``dispatcher/limits.py`` only keeps the limits of each connection
and refuses what they do not allow. This file does the part that needs this
microscope's saved settings and the instrument: it finds the operator's
``limits.json``, checks it, cuts its ranges to what NIS-Elements allows, and
installs the result for the connection. If the file is missing or does not
check out, it installs the limits that ship with the driver instead and says
so loudly, so a session is never left without limits. Only when even those
cannot be used does the gate refuse every change.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
from typing import Any

from ...dispatcher import limits as _gate
from ...primitives.dll.profile import limits_setup_hint
from .. import store as _machine
from . import config as _config
from . import limit_rules as _rules

log = logging.getLogger(__name__)


def _install(
    client: Any, limits_file: Any, nis_um: dict, *, source: str, is_fallback: bool
) -> _gate.GateState:
    """Read one limits file, compile it against NIS's limits, and install it for *client*."""
    stage_cfg = _config.load(limits_path=limits_file)
    limits, notes = _rules.build_limits(
        stage_cfg, nis_um, source=source, path=limits_file, is_fallback=is_fallback
    )
    for note in notes:
        log.warning("limits: %s", note)
    state = _gate.GateState(
        limits=limits,
        stage_cfg=_gate.freeze(
            {"stage_um": limits.describe()["envelope_um"], "nis_um": dict(nis_um)}
        ),
        error=None,
    )
    _gate.install(client, state)
    return state


def _install_defaults(
    client: Any, machine: Any, nis_um: dict, reason: str
) -> _gate.GateState:
    """Govern the session with the driver's bundled defaults, loudly."""
    state = _install(
        client,
        machine.bundled_default_path("limits"),
        nis_um,
        source="defaults",
        is_fallback=True,
    )
    log.warning(
        "limits fallback: %s. The session runs within the driver's bundled DEFAULT limits, "
        "cut to what NIS-Elements allows, not this microscope's own file. Publish this "
        "microscope's limits with %s.",
        reason,
        limits_setup_hint,
    )
    return state


def connect_handshake(
    client: Any, nis_um: dict, *, machine: Any = None, load: bool = True
) -> _gate.GateStatus:
    """Resolve, check and install the limits for *client*; never raises.

    *nis_um* is what NIS-Elements allows, read from the instrument just
    before (per axis ``[min, max]`` in micrometres); every envelope is cut
    to it. With ``load`` False the operator's file is skipped on purpose and
    the defaults govern. Answers the gate's status, for the connection
    report.
    """
    machine = machine if machine is not None else _machine.MACHINE
    if not load:
        return _gate.status(
            _install_defaults(
                client,
                machine,
                nis_um,
                "limits loading was skipped for this connection",
            )
        )
    path = machine.latest_path("limits")
    if path is None:
        return _gate.status(
            _install_defaults(
                client,
                machine,
                nis_um,
                "no limits have been published for this microscope yet",
            )
        )
    try:
        state = _install(client, path, nis_um, source="machine", is_fallback=False)
    except Exception as why:  # noqa: BLE001 -- a bad file falls back to the defaults, never crashes connect
        try:
            return _gate.status(
                _install_defaults(
                    client,
                    machine,
                    nis_um,
                    f"the limits file {path} did not check out: {why}",
                )
            )
        except Exception as defaults_why:  # noqa: BLE001 -- even the defaults are unusable: refuse everything
            error = (
                f"the limits file did not check out ({why}) and the bundled defaults are unusable "
                f"({defaults_why}); every change is refused until the limits can be read (see {limits_setup_hint})"
            )
            log.error("%s", error)
            state = _gate.GateState(limits=None, stage_cfg=None, error=error)
            _gate.install(client, state)
            return _gate.status(state)
    log.info("limits handshake ok: %s governs this session", state.limits.describe())
    return _gate.status(state)
