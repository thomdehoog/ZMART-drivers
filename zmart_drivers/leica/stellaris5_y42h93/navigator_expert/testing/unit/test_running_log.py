"""The running log copies the driver's messages into one file and never stops the experiment.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

import logging

import pytest

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.for_zmart_controller import (
    running_log,
)

NAME = "running_log_test_package"


@pytest.fixture(autouse=True)
def _clean_logger():
    yield
    logger = logging.getLogger(NAME)
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)
    logger.setLevel(logging.NOTSET)


def test_messages_from_the_driver_reach_the_file(tmp_path):
    path = running_log.start(tmp_path / "logs", NAME)
    logging.getLogger(f"{NAME}.dispatcher.change").info("sent: Zoom -> 2.0")
    for handler in logging.getLogger(NAME).handlers:
        handler.flush()

    assert path == tmp_path / "logs" / running_log.FILE_NAME
    text = path.read_text(encoding="utf-8")
    assert "sent: Zoom -> 2.0" in text
    assert "INFO" in text


def test_starting_twice_keeps_one_copy_of_each_line(tmp_path):
    running_log.start(tmp_path, NAME)
    running_log.start(tmp_path, NAME)
    logging.getLogger(NAME).info("once")
    for handler in logging.getLogger(NAME).handlers:
        handler.flush()

    text = (tmp_path / running_log.FILE_NAME).read_text(encoding="utf-8")
    assert text.count("once") == 1


def test_a_folder_that_cannot_be_written_does_not_raise(tmp_path):
    blocker = tmp_path / "not_a_folder"
    blocker.write_text("a file is in the way")

    assert running_log.start(blocker / "logs", NAME) is None
