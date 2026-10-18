"""The kinds of problem, and the one rule that matters most: only a temporary problem is sent again.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

import pytest

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.error_classification import Kind, classify_error, sends_again


@pytest.mark.parametrize(
    ("message", "kind"),
    [
        ("The scanner is being scanned", Kind.TEMPORARY),
        ("request timed out", Kind.TEMPORARY),
        ("value is invalid for this detector", Kind.BAD_REQUEST),
        ("position out of range", Kind.BAD_REQUEST),
        ("command not implemented on this hardware", Kind.PERMANENT),
        ("something nobody has seen before", Kind.PERMANENT),
        ("", Kind.PERMANENT),
    ],
)
def test_leica_messages_are_sorted_into_the_shared_kinds(message, kind):
    assert classify_error(message) is kind


def test_a_message_that_is_both_invalid_and_busy_is_not_sent_again():
    """The permanent patterns win, so a refused request is never repeated."""
    assert classify_error("block is being scanned and the value is invalid") is (
        Kind.BAD_REQUEST
    )


def test_only_a_temporary_problem_is_sent_again():
    again = [kind for kind in Kind if sends_again(kind)]
    assert again == [Kind.TEMPORARY]
