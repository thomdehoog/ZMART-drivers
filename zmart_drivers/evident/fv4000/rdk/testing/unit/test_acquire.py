"""Taking a picture: sent once, watched until the scan ran, and never sent again."""

import pytest

from zmart_drivers.evident.fv4000.rdk.procedures import changes


def _starts(mock):
    return [c for c in mock.calls if c[0] == "Protocol.startProtocol"]


def test_a_capture_is_seen_scanning_and_then_idle(governed_client, mock):
    mock.scan_s = 0.3
    result = changes.acquire(governed_client, poll_interval=0.02)
    assert result["success"] is True and result["confirmed"] is True
    assert result["target"] == "D:/Images/image_0001.oir" and result["planned_s"] == 0
    assert (
        result["progress"]["state"] == "IDLING"
        and result["progress"]["series"]["completeZ"] == 1
    )
    assert len(_starts(mock)) == 1


def test_a_scan_too_quick_to_be_seen_is_confirmed_by_the_file_it_wrote(
    governed_client, mock
):
    mock.scan_s = 0.0
    result = changes.acquire(governed_client, poll_interval=0.02)
    assert result["success"] is True and result["confirmed"] is True
    assert any("already opens" in entry["msg"] for entry in result["logs"])


def test_a_capture_is_never_sent_again_however_busy_fluoview_is(governed_client, mock):
    mock.busy_answers = 5
    result = changes.acquire(governed_client, poll_interval=0.02)
    assert result["success"] is False
    assert len(_starts(mock)) == 1, "a second send would be a second picture"


def test_a_scan_that_never_starts_fails_instead_of_pretending(governed_client, mock):
    mock.silent_start = True
    result = changes.acquire(governed_client, poll_interval=0.02, start_timeout=0.2)
    assert result["success"] is False and "never started" in result["message"]
    with pytest.raises(RuntimeError, match="never started"):
        changes.take_picture(governed_client, poll_interval=0.02, start_timeout=0.2)


def test_take_picture_answers_when_and_what_was_captured(governed_client, mock):
    mock.scan_s = 0.1
    acq = changes.take_picture(governed_client, poll_interval=0.02)
    assert acq.target.endswith(".oir") and acq.finished_at >= acq.started_at
    assert acq.command_result["confirmed"] is True and acq.progress["state"] == "IDLING"


def test_a_capture_waits_for_a_running_scan_to_end_instead_of_sending(
    governed_client, mock
):
    mock.scan_s = 0.2
    mock.call("Protocol.startProtocol", {"executionType": "EXECUTION_TYPE_MANUAL_MAIN"})
    result = changes.acquire(governed_client, poll_interval=0.02)
    assert result["success"] is True
    assert len(_starts(mock)) == 2 and result["target"] == "D:/Images/image_0002.oir"


def test_stop_ends_a_running_scan_and_sees_fluoview_idle(governed_client, mock):
    mock.scan_s = 10
    mock.call("Protocol.startProtocol", {"executionType": "EXECUTION_TYPE_MANUAL_MAIN"})
    result = changes.stop(governed_client)
    assert (
        result["success"] is True
        and result["confirmed"] is True
        and mock.state == "IDLING"
    )
