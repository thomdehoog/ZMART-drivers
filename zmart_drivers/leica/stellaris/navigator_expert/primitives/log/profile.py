"""What the LAS X log answers for each reading, and how old its answer may be.

The log writes a value only when it changes, so each answer carries the age of
its log line (``AGE_KEYS`` says which line), and an answer older than its
reading's ``MAX_AGE_S`` does not count: each kind of state goes stale at its own
pace. The readings look these tables up each time they read, so a test or a
validator can change an age in place.

The full job list has no log answer: the log reports only the active job (the
``selected_job`` reading), and a list put together from it leaves out every job
not dumped again this session (confirmed on the bench, 2026-07-06).

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from . import reads as log_reader

#: The log's answer to each reading it can answer.
READS = {
    "scan_status": lambda snapshot, *, max_age_s: log_reader.get_scan_status(
        snapshot, max_age_s=max_age_s
    ),
    "job_settings": lambda snapshot, *, max_age_s, job_name: log_reader.get_job_settings(
        job_name, snapshot, max_age_s=max_age_s
    ),
    "hardware_info": lambda snapshot, *, max_age_s: log_reader.get_hardware_info(
        snapshot, max_age_s=max_age_s
    ),
    "xy": lambda snapshot, *, max_age_s: log_reader.get_xy(
        snapshot, max_age_s=max_age_s
    ),
    "selected_job": lambda snapshot, *, max_age_s: log_reader.get_selected_job(
        snapshot, max_age_s=max_age_s
    ),
}

#: Which age of ``log_reader.age_for_snapshot`` belongs to each reading's answer.
AGE_KEYS = {
    "scan_status": "scan_status",
    "hardware_info": "hardware_info",
    "xy": "xy",
    "selected_job": "selected_job",
}

#: How old a log answer may be, in seconds, and still count.
MAX_AGE_S = {
    "xy": 1.0,
    "job_settings": 2.0,
    "selected_job": 2.0,
    "hardware_info": 2.0,
    "scan_status": 0.5,
}
