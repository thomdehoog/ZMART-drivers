"""The running log: one file that records what the driver did, for finding out later what went wrong.

When something unexpected happens at the microscope, the most useful thing to
have is a plain record of what the driver sent, what came back, and what it
decided. This file starts that record. Every message the driver already
writes (each send, retry, fallback to another source and final answer) is
copied into one text file with a time on each line.

The file is kept small on purpose: when it reaches about five megabytes it is
renamed and a new one is started, and only the three newest old files are
kept, so the record can never fill the disk. This is separate from the log
that is saved beside the pictures of one acquisition, which says how those
pictures were made.

A problem with the log file itself (a full disk, a folder that cannot be
written) is noted once and never stops the experiment.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

FILE_NAME = "running_log.txt"
MAX_BYTES = 5_000_000
OLD_FILES_KEPT = 3
LINE_FORMAT = "%(asctime)s  %(levelname)-7s  %(name)s  %(message)s"

_log = logging.getLogger(__name__)


def start(folder, logger_name, *, level=logging.INFO) -> Path | None:
    """Begin copying the driver's messages into ``<folder>/running_log.txt``.

    *logger_name* is the driver's package name; every message from every part
    of the driver reaches it. Calling this again for the same file does
    nothing, so each connect can call it safely. Returns the path of the
    file, or None when it could not be opened.
    """
    path = Path(folder) / FILE_NAME
    package_logger = logging.getLogger(logger_name)
    for handler in package_logger.handlers:
        if getattr(handler, "baseFilename", None) == str(path.resolve()):
            return path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path, maxBytes=MAX_BYTES, backupCount=OLD_FILES_KEPT, encoding="utf-8"
        )
    except OSError as why:
        _log.warning("the running log could not be opened at %s: %s", path, why)
        return None
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(LINE_FORMAT))
    package_logger.addHandler(handler)
    if package_logger.level == logging.NOTSET or package_logger.level > level:
        package_logger.setLevel(level)
    return path
