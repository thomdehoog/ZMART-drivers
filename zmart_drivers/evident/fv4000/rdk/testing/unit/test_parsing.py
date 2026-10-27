"""FLUOVIEW's units and result codes, read the way the driver reads them."""

import pytest

from zmart_drivers.evident.fv4000.rdk.primitives.rdk import parsing, writes


def test_micrometres_become_whole_nanometres_and_back():
    assert parsing.um_to_nm(1.2345) == 1234  # rounded to the nearest nanometre
    assert parsing.um_to_nm(-0.0005) == 0 or parsing.um_to_nm(-0.0005) == -0
    assert parsing.nm_to_um(2_500_000) == 2500.0


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "50", True, None])
def test_a_value_that_is_not_a_finite_number_is_refused_before_it_reaches_the_stage(
    bad,
):
    with pytest.raises(ValueError):
        parsing.um_to_nm(bad)


@pytest.mark.parametrize(
    ("code", "kind", "again"),
    [
        ("UNAVAILABLE", parsing.Kind.TEMPORARY, True),
        ("ERROR", parsing.Kind.PERMANENT, False),
        ("INVALID_PARAMETERS", parsing.Kind.BAD_REQUEST, False),
        ("FILE_NOT_FOUND", parsing.Kind.BAD_REQUEST, False),
        ("OUT_OF_MEMORY", parsing.Kind.PERMANENT, False),
        ("SOMETHING_NEW", parsing.Kind.PERMANENT, False),
        (None, parsing.Kind.PERMANENT, False),
    ],
)
def test_every_result_code_of_the_manual_has_a_kind_and_only_unavailable_is_sent_again(
    code, kind, again
):
    assert parsing.classify_result(code) is kind
    assert parsing.sends_again(parsing.classify_result(code)) is again


def test_require_ok_hands_the_reply_back_or_names_the_call_and_the_code():
    assert parsing.require_ok("X.y", {"result": "OK", "a": 1}) == {
        "result": "OK",
        "a": 1,
    }
    with pytest.raises(parsing.RdkError) as caught:
        parsing.require_ok("Parameter.getParameter(XY)", {"result": "UNAVAILABLE"})
    assert (
        caught.value.code == "UNAVAILABLE"
        and caught.value.kind is parsing.Kind.TEMPORARY
    )
    assert "Parameter.getParameter(XY)" in str(caught.value)


def test_binary_data_is_read_whether_the_library_wrapped_it_or_not():
    import xmlrpc.client

    assert parsing.as_bytes(b"ab") == b"ab"
    assert parsing.as_bytes(xmlrpc.client.Binary(b"cd")) == b"cd"
    with pytest.raises(TypeError):
        parsing.as_bytes(3)


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(1.0, (1, 0)), (0.5, (5, 1)), (2.25, (225, 2)), (0.001, (1, 3)), (30, (30, 0))],
)
def test_a_time_lapse_interval_becomes_a_whole_number_and_a_power_of_ten(
    seconds, expected
):
    interval = writes._interval(seconds)
    assert (interval["interval"], interval["unitOfInterval"]) == expected
    assert interval["interval"] * 10.0 ** -interval["unitOfInterval"] == pytest.approx(
        seconds
    )
