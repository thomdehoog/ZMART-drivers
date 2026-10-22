"""Which of LAS X's sources each reading asks, in which order, and how old an answer may be.

The readers look ``STATE_READERS`` up each time they read, so a test or a
validator can hand in other settings by replacing it.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class StateReaderProfile:
    """Profile-controlled backend selection for passive state reads.

    These modes govern routed state reads, including command confirmation.
    Freshness limits reject old log state; transition-sensitive confirmations
    additionally reject observations from before the command. Reads that
    produce persisted or foundational correctness artifacts still use this
    policy. Callers never pin a backend; source availability belongs to the
    capability table and source preference belongs here.

    Nothing here says how long to wait or how often to look: that is the one
    rule in ``connection/waiting.py``. A mode says which sources take part; a
    ``*_log_max_age_s`` says how old a log answer may be and still count,
    because each kind of state goes stale at its own pace.

    Every reading asks the direct reader first (``direct``: LAS X's
    hardware server's state tree; Thom's decision, 2026-10-13). When it
    fails, for whatever reason (the hardware server not reachable, another
    LAS X version laying the tree out differently, an answer that does not
    count), the hybrid reader (CAM API and log) answers; the reading names
    its source. Measured on the simulator, 2026-10-13: XY read directly in
    1.1 ms against the CAM API's 20 ms (worst 57 ms), and a new stage
    position seen 3.3-4.2 ms after a move against 22-58 ms.
    """

    # --- Which source answers when the first one cannot ---------------------
    # Each reading asks the source named by its ``*_mode`` first. When that
    # source cannot answer, one of these families takes its place, and the
    # reading names the source it came from. Each is "api", "log" or "hybrid"
    # (the API and the log asked together).
    #
    # An ordinary reading falls back to the CAM API alone: the direct reader
    # first, then the API (Thom, 2026-10-21). "hybrid" would let the log answer
    # too, which can still show the state from before.
    reading_fallback: str = "api"
    # A reading a command waits on (is the scanner idle, did the stage arrive)
    # falls back to the API alone. The log writes a value only when it changes,
    # so it can still show the state from before the command.
    waiting_fallback: str = "api"
    # Confirming a job change: when the direct reader cannot answer, this
    # takes its place. Hybrid races the API against the log (see
    # ``selected_job_confirm_source`` below).
    selected_job_confirm_fallback: str = "hybrid"

    xy_mode: str = "direct"
    xy_log_max_age_s: float = 1.0

    job_settings_mode: str = "direct"
    job_settings_log_max_age_s: float = 2.0

    # The job list comes from the direct reader, with the API as its fallback:
    # the log stream only reports the ACTIVE job, so its list is incomplete (a
    # job not re-dumped this session is absent). Confirmed on the bench
    # (2026-07-06). selected_job (which job is active) is what the log can
    # see, so its fallback is hybrid.
    jobs_mode: str = "direct"

    selected_job_mode: str = "direct"
    selected_job_log_max_age_s: float = 2.0
    # Selected-job confirmation source: "direct" | "api" | "log" | "hybrid".
    # direct: the direct reader alone confirms (LAS X's live selection); only
    # when it cannot answer does hybrid take its place.
    # hybrid races the api leg (transition-admissible: a stale API readback
    # cannot witness a transition to a target it already read pre-command)
    # against the log leg (post-command CurrentBlock event). The race runs for
    # one window (``waiting.WINDOW_S``), and the dispatcher runs four of them
    # with the command fired again between: the one 4x3 rule.
    # The api confirm is measured-wrong on the real scope (stale 15 s+, wrong
    # job) and log-only is insufficient on the simulator; hybrid fits both
    # without environment detection, so it is the fallback behind the direct
    # reader, which is the default.
    selected_job_confirm_source: str = "direct"
    selected_job_log_prime_cluster: bool = False
    selected_job_log_cluster_max_age_s: float | None = None

    # Where a z drive is: "direct", the hardware server; any other mode, the job
    # settings, and for the drive carrying the job's z-stack, whose settings
    # value is stale, the experiment LAS X saves (``derived.z_um_from_settings``).
    z_mode: str = "direct"

    hardware_info_mode: str = "direct"
    hardware_info_log_max_age_s: float = 2.0

    scan_status_mode: str = "direct"
    scan_status_log_max_age_s: float = 0.5

    def __post_init__(self):
        for name in (
            "reading_fallback",
            "waiting_fallback",
            "selected_job_confirm_fallback",
        ):
            if getattr(self, name) not in ("api", "log", "hybrid"):
                raise ValueError(
                    f"{name} must be 'api', 'log' or 'hybrid', not {getattr(self, name)!r}"
                )


STATE_READERS = StateReaderProfile()
