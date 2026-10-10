"""Composed stage routines.

Routines here move the stage by composing the checked command primitives
in ``commands.py`` -- every leg passes through ``move_xy``'s limit
checks, so nothing in this module can escape the configured envelope.

Backlash discipline: when the stage reverses direction, leadscrew slack
introduces a position offset that depends on which side the nut last
came from. By always finishing every move with the same +X +Y leg, the
slack-state is pinned and the offset becomes invisible -- the stage then
enters every acquisition in the same mechanical state and positions are
repeatable regardless of where it came from.

When the takeup runs is the caller's decision: the acquisition routine
in the ZMART adapter and the calibration notebook order it at the moment
they need a pinned stage. The driver never fires it on its own.

The constants below are physics of this stage, not configuration
(decision §2b): backlash lives in neither ``limits.json`` nor
``calibration.json``. Callers that want non-default takeup pass the
params explicitly.
"""

import logging
import time

from ..actions import set as _commands
from ..dispatcher import checks as _checks
from ..dispatcher import read as _readers

log = logging.getLogger(__name__)

# The ZMB STELLARIS 5 stage has 3-5 um of leadscrew backlash; 50 um of
# overshoot is 10x margin. 100 ms between moves keeps consecutive
# commands distinct, so controllers that blend back-to-back moves treat
# each one as its own motion.
BACKLASH_OVERSHOOT_UM = 50.0
BACKLASH_SETTLE_MS = 100
BACKLASH_DEFAULT_ROUNDS = 3


def _leg(client, result, x_um, y_um, what, tolerance_um=20.0):
    """Judge one move leg: refused or failed raises; unconfirmed gets one more look.

    A leg that LAS X refused, or that failed outright, raises, because
    nothing has happened or something has gone wrong. A leg that was sent
    and accepted but whose readback never matched in time is not a failure
    yet: the stage is read once more, and when it stands at the target the
    leg counts as confirmed late. When it reads somewhere else, the leg is
    contradicted, and that raises. When it cannot be read at all, the leg
    stays unconfirmed, a warning says so, and the recipe carries on; the
    caller records it, so the workflow is told rather than stopped.

    Returns ``True`` when the leg is confirmed, ``False`` when it is not.
    """
    if not result or not result.get("success"):
        raise RuntimeError(f"{what} failed: {result}")
    if result.get("confirmed"):
        return True
    pos = _readers.get_xy(client)
    if pos is not None:
        dx, dy = abs(float(pos["x_um"]) - x_um), abs(float(pos["y_um"]) - y_um)
        if dx <= tolerance_um and dy <= tolerance_um:
            log.info("%s confirmed on a later readback", what)
            return True
        raise RuntimeError(
            f"{what} contradicted by the readback: the stage reads "
            f"({pos['x_um']:.2f}, {pos['y_um']:.2f}), not ({x_um:.2f}, {y_um:.2f})"
        )
    log.warning("%s was not confirmed and the stage could not be read; carrying on", what)
    return False


def arrive_xy(client, x_um, y_um):
    """Arrive at ``(x_um, y_um)`` with the backlash taken up.

    Approach through an overshoot waypoint in -X -Y, settle, then make
    the final +X +Y leg. Near the envelope's lower edge the waypoint is
    clamped inside the envelope, so a legal target close to the boundary
    stays reachable -- the takeup is merely shortened there.

    Both legs go through the checked ``move_xy`` door. A refused or failed
    leg raises. A leg that could not be confirmed is given one more look
    (see :func:`_leg`); if the stage still cannot be read, this returns
    ``{"success": True, "confirmed": False}`` and the caller records it.
    """
    # Refuse an illegal destination before any leg fires. Without this,
    # the clamp below could turn an out-of-envelope target into a real
    # move to the envelope's corner — motion caused by a target that
    # should have been refused outright.
    _checks.check_xy(x_um, y_um)
    waypoint_x = x_um - BACKLASH_OVERSHOOT_UM
    waypoint_y = y_um - BACKLASH_OVERSHOOT_UM
    env = _checks.get_stage_limits()
    if env["x_min"] is not None:
        clamped_x = max(waypoint_x, env["x_min"])
        clamped_y = max(waypoint_y, env["y_min"])
        if (clamped_x, clamped_y) != (waypoint_x, waypoint_y):
            log.warning(
                "backlash overshoot clamped to the stage envelope near its "
                "edge; takeup is shortened for this move"
            )
        waypoint_x, waypoint_y = clamped_x, clamped_y
    r = _commands.move_xy(client, waypoint_x, waypoint_y, unit="um")
    confirmed = _leg(
        client,
        r,
        waypoint_x,
        waypoint_y,
        f"backlash overshoot to ({waypoint_x:.2f}, {waypoint_y:.2f})",
    )
    time.sleep(BACKLASH_SETTLE_MS / 1000.0)
    r = _commands.move_xy(client, x_um, y_um, unit="um")
    confirmed &= _leg(
        client, r, x_um, y_um, f"backlash final approach to ({x_um:.2f}, {y_um:.2f})"
    )
    return {"success": True, "confirmed": confirmed}


def correct_backlash(
    client,
    *,
    at=None,
    overshoot_um=BACKLASH_OVERSHOOT_UM,
    settle_ms=BACKLASH_SETTLE_MS,
    tolerance_um=20.0,
    passes=BACKLASH_DEFAULT_ROUNDS,
):
    """Pin the stage to the +X +Y slack-state with no net displacement.

    Repeats ``passes`` times: drive to ``(x - overshoot, y - overshoot)``,
    pause ``settle_ms``, then drive back to ``(x, y)``. Every return leg
    approaches from -X -Y, so each pass engages both leadscrews against
    the same flank; repeating the back-and-forth settles mechanical slack
    that a single pass can leave partially taken up.

    Parameters
    ----------
    client
        LAS X API client.
    at
        ``(x_um, y_um)`` when the caller already knows where the stage is
        -- for example right after a confirmed move. Skips the position
        read. Omit it and the current position is read from the API.
    overshoot_um
        Distance to retreat in -X -Y. Must exceed the stage's backlash;
        the default is this stage's measured physics (see above).
    settle_ms
        Pause between each overshoot and return, so controllers that
        blend consecutive moves treat the next command as distinct.
    tolerance_um
        Pass-through to ``move_xy``. Loose by default; the takeup
        does not need precision.
    passes
        How many back-and-forth passes to run. Three is the historical
        default; whether one pass suffices after a fresh arrival has not
        been measured on the rig -- keep three until bench data says
        otherwise.

    A refused or failed leg raises and fires no further moves. A leg
    that could not be confirmed is given one more look (see
    :func:`_leg`): confirmed late, contradicted (raises), or still
    unconfirmed, in which case the takeup carries on and returns
    ``confirmed: False`` so the caller can record it.

    Returns ``{"success": True, "confirmed": bool, "passes": int}``.
    """
    if passes != int(passes) or int(passes) < 1:
        raise ValueError(
            f"backlash takeup needs a whole number of passes (at least one), got {passes}"
        )
    if at is None:
        # This read parameterizes the corrective moves below, so bypass the
        # passive reader profile and use the authoritative API path.
        pos = _readers.get_xy(client)
        if pos is None:
            raise RuntimeError("backlash takeup: could not read XY")
        x, y = float(pos["x_um"]), float(pos["y_um"])
    else:
        x, y = float(at[0]), float(at[1])
    log.debug(
        "backlash takeup at (%.2f, %.2f) um, overshoot %.1f um, %d passes",
        x,
        y,
        overshoot_um,
        passes,
    )
    confirmed = True
    for index in range(int(passes)):
        if index > 0:
            # Same reason as the mid-pass pause below: without a gap the
            # previous return and this overshoot could blend into one
            # motion, and the extra passes would settle nothing.
            time.sleep(settle_ms / 1000.0)
        # success=True alone means "command accepted" (profiles set
        # success_on_unconfirmed=True). Each leg gets the readback's word
        # through _leg: a leg the stage is seen to have made counts, a leg
        # the stage is seen NOT to have made raises.
        r = _commands.move_xy(
            client, x - overshoot_um, y - overshoot_um, unit="um", tolerance=tolerance_um
        )
        confirmed &= _leg(
            client, r, x - overshoot_um, y - overshoot_um, "backlash overshoot move", tolerance_um
        )
        time.sleep(settle_ms / 1000.0)
        r = _commands.move_xy(client, x, y, unit="um", tolerance=tolerance_um)
        confirmed &= _leg(client, r, x, y, "backlash return move", tolerance_um)
    return {"success": True, "confirmed": confirmed, "passes": int(passes)}
