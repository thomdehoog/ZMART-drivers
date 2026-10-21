"""Where LAS X writes its logs, and how old a log answer may be.

The log reader reads ``LOG_READER`` each time it reads, so a test or a
validator can point it at other files by replacing it.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class LogReaderProfile:
    """Low-level LAS X log-reader paths and freshness defaults."""

    lcs_log_path: str = r"C:\ProgramData\Leica Microsystems\LAS X\lcsCommand.log"
    msgbox_log_path: str = r"C:\ProgramData\Leica Microsystems\LAS X\MatrixScreener.log"
    current_window_s: float = 180.0
    max_age_s: float | None = None


LOG_READER = LogReaderProfile()
