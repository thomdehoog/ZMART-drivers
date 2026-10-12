"""
Unit tests: after loading an experiment, the CAM API and LAS X must agree on the job ids.
================================================================================
On the simulator (2026-10-13) an ``apply_lrp_change`` load gave the jobs new
ids in LAS X (HiRes 145 -> 152) while the CAM API kept the old ones; every
CAM job read then failed and every save timed out, and only a LAS X restart
helped. ``apply_lrp_change`` now compares the CAM API's job list with the
job sequence in LAS X's state tree after the load, and stops with the reason
when they never agree. Without the hardware server it cannot compare, and
goes on as before.
"""

import pytest

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.scanfields import (
    transaction,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_interface import (
    state_tree,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_interface.hardware_server import (
    HardwareServerUnavailable,
)

LAS_X = {"HiRes": (152, 3), "Overview": (154, 2), "AF Job": (156, 1)}


def sequence(ids):
    return state_tree.JobSequence(
        block_id=151,
        selected_element=3,
        jobs={
            name: state_tree.Job(block, element)
            for name, (block, element) in ids.items()
        },
    )


@pytest.fixture
def run(monkeypatch, tmp_path):
    """apply_lrp_change with LAS X stood in; returns (call, record of what happened)."""
    record = {"saves": 0, "loads": 0}
    templates = tmp_path
    (templates / "T.lrp").write_text("<lrp/>", encoding="utf-8")

    def save(client, name, folder, *, timeout=30, confirm_path=None, **kwargs):
        record["saves"] += 1
        return {"ok": True}

    def load(client, name):
        record["loads"] += 1
        return {"ok": True}

    monkeypatch.setattr(
        transaction, "find_scanning_templates_dir", lambda: str(templates)
    )
    monkeypatch.setattr(transaction, "save_experiment", save)
    monkeypatch.setattr(transaction, "load_experiment", load)
    monkeypatch.setattr(
        transaction, "get_selected_job", lambda client: {"Name": "HiRes"}
    )
    monkeypatch.setattr(transaction, "reorder_jobs", lambda path, job: None)

    def call(cam_ids, las_x):
        monkeypatch.setattr(
            transaction,
            "get_jobs",
            lambda client, **kwargs: [{"Name": n, "ID": i} for n, i in cam_ids.items()],
        )
        monkeypatch.setattr(transaction, "job_sequence", las_x)
        return transaction.apply_lrp_change(object(), "T.xml", lambda path: 1)

    return call, record


def test_agreeing_ids_go_on_to_the_confirming_save(run):
    call, record = run
    result = call(
        {"HiRes": 152, "Overview": 154, "AF Job": 156}, lambda: sequence(LAS_X)
    )
    assert result["success"] is True
    assert (record["loads"], record["saves"]) == (1, 2)


def test_stale_cam_ids_stop_before_any_further_save(run, caplog):
    call, record = run
    result = call(
        {"HiRes": 145, "Overview": 147, "AF Job": 149}, lambda: sequence(LAS_X)
    )
    assert result is None
    assert (record["loads"], record["saves"]) == (1, 1)
    assert (
        "145" in caplog.text and "152" in caplog.text and "restart LAS X" in caplog.text
    )


def test_ids_that_agree_a_moment_later_are_waited_for(run, monkeypatch):
    # *run* stands LAS X in; this test sets the job lists itself.
    answers = iter([{"HiRes": 145}, {"HiRes": 152, "Overview": 154, "AF Job": 156}])
    monkeypatch.setattr(
        transaction,
        "get_jobs",
        lambda client, **kwargs: [
            {"Name": n, "ID": i} for n, i in next(answers).items()
        ],
    )
    monkeypatch.setattr(transaction, "job_sequence", lambda: sequence(LAS_X))
    result = transaction.apply_lrp_change(object(), "T.xml", lambda path: 1)
    assert result["success"] is True


def test_without_the_hardware_server_it_goes_on_as_before(run):
    call, record = run

    def unreachable():
        raise HardwareServerUnavailable("no hardware server")

    result = call({"HiRes": 145}, unreachable)
    assert result["success"] is True
    assert (record["loads"], record["saves"]) == (1, 2)
