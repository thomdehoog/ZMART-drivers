"""The table of job settings: each setting is read back from where LAS X writes it.

For every setting, a readback holding the new value where that setting's
``read`` looks confirms the change, and one holding another value does not.
The zoom, the image format and the z-stack, whose readbacks are particular,
have their own cases. Offline: ``confirming.readback`` is replaced, so no
microscope is asked.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

import pytest

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.operations.changes import confirming
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.operations.changes import job_settings

# Per setting: the arguments besides its value, the value asked for, a value
# read back that matches it (within the setting's tolerance) and one that
# does not, and a readback holding a value where the setting reads it.

SAMPLES = {
    "zoom": dict(params={}, target=5.0, match=5.05, miss=5.5, ch=lambda a: {"zoom": {"current": a}}),
    "image_format": dict(params={}, target="512 x 512", match="512 x 512", miss="1024 x 1024", ch=lambda a: {"format": a}),
    "z_stack_size": dict(params={}, target=20.0, match=20.5, miss=30.0, ch=lambda a: {"stack": {"size": a, "stepSize": None}}),
    "scan_field_rotation": dict(
        params={},
        target=45.0,
        match=45.2,  # |0.2| < 0.5
        miss=46.0,  # |1.0| >= 0.5
        ch=lambda a: {"scanFieldRotation": {"value": a}},
    ),
    "pinhole_airy": dict(
        params={"setting_index": 0},
        target=1.0,
        match=1.02,  # |0.02| < 0.05
        miss=0.5,
        ch=lambda a: {"activeSettings": [{"pinholeAiry": {"value": a}}]},
    ),
    "detector_gain": dict(
        params={"setting_index": 0, "beam_route": "BR1"},
        target=750.0,
        match=750.5,  # |0.5| < 1.0
        miss=700.0,
        ch=lambda a: {
            "activeSettings": [
                {"activeDetectors": [{"_beamRoute": "BR1", "gain": {"value": a}}]}
            ]
        },
    ),
    "laser_intensity": dict(
        params={"setting_index": 0, "beam_route": "BR1", "line_index": 0},
        target=0.5,
        match=0.502,  # |0.002| < 0.005
        miss=0.6,
        ch=lambda a: {
            "activeSettings": [
                {
                    "activeLaserLines": [
                        {
                            "_beamRoute": "BR1",
                            "_lineIndex": 0,
                            "intensity": {"value": a},
                        }
                    ]
                }
            ]
        },
    ),
    "filter_wheel_spectrum": dict(
        params={"setting_index": 0, "beam_route": "BR1", "filter_wheel_type": "FW"},
        target=525,
        match=525.4,  # |0.4| < 1
        miss=560,
        ch=lambda a: {
            "activeSettings": [
                {
                    "filterWheels": [
                        {"_beamRoute": "BR1", "type": "FW", "spectrumPosition": a}
                    ]
                }
            ]
        },
    ),
    "scan_speed": dict(
        params={},
        target=600,
        match=600,
        miss=400,
        ch=lambda a: {"scanSpeed": {"value": a}},
    ),
    "scan_resonant": dict(
        params={},
        target=True,
        match=True,
        miss=False,
        ch=lambda a: {"scanSpeed": {"isResonant": a}},
    ),
    "scan_mode": dict(
        params={},
        target="xyz",
        match="xyz",
        miss="xy",
        ch=lambda a: {"scanMode": a},
    ),
    "sequential_mode": dict(
        params={},
        target="Line",
        match="Line",
        miss="Frame",
        ch=lambda a: {"sequentialMode": a},
    ),
    "z_stack_step_size": dict(
        params={},
        target=2.0,
        match=2.2,  # |0.2| < 0.5
        miss=3.0,
        ch=lambda a: {"stack": {"stepSize": a}},
    ),
    "frame_accumulation": dict(
        params={"setting_index": 0},
        target=4,
        match=4,
        miss=2,
        ch=lambda a: {"activeSettings": [{"frameAccumulation": a}]},
    ),
    "frame_average": dict(
        params={"setting_index": 0},
        target=2,
        match=2,
        miss=3,
        ch=lambda a: {"activeSettings": [{"frameAverage": a}]},
    ),
    "line_accumulation": dict(
        params={"setting_index": 0},
        target=3,
        match=3,
        miss=1,
        ch=lambda a: {"activeSettings": [{"lineAccumulation": a}]},
    ),
    "line_average": dict(
        params={"setting_index": 0},
        target=8,
        match=8,
        miss=4,
        ch=lambda a: {"activeSettings": [{"lineAverage": a}]},
    ),
    "laser_shutter": dict(
        params={"setting_index": 0, "beam_route": "BR1"},
        target=True,
        match=True,
        miss=False,
        ch=lambda a: {
            "activeSettings": [
                {"activeLaserLines": [{"_beamRoute": "BR1", "shutterOpen": a}]}
            ]
        },
    ),
    "filter_wheel_slot": dict(
        params={"setting_index": 0, "beam_route": "BR1", "filter_wheel_type": "FW"},
        target=3,
        match=3,
        miss=5,
        ch=lambda a: {
            "activeSettings": [
                {
                    "filterWheels": [
                        {"_beamRoute": "BR1", "type": "FW", "filterIndex": a}
                    ]
                }
            ]
        },
    ),
}


def answering(settings):
    """A readback that always answers ``settings``."""
    return lambda client, job_name, **window: settings


def arguments(name, sample, value):
    given = dict(sample["params"])
    if name == "image_format":
        given["format_str"] = value
    else:
        given[job_settings.SETTINGS[name].value] = value
    return given


def test_every_setting_has_a_sample():
    assert set(SAMPLES) | {"z_stack_definition"} == set(job_settings.SETTINGS)


def test_each_setting_names_the_argument_that_holds_its_value():
    for setting in job_settings.SETTINGS.values():
        assert setting.value in setting.takes or setting.normalize is not None, setting.name


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_a_readback_with_the_new_value_confirms(name, monkeypatch):
    sample = SAMPLES[name]
    monkeypatch.setattr(confirming, "readback", answering(sample["ch"](sample["match"])))

    result = job_settings.confirm(object(), name, "JOB", poll_window=1.0, **arguments(name, sample, sample["target"]))

    assert result == {"success": True, "logs": []}


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_a_readback_with_another_value_does_not_confirm(name, monkeypatch):
    sample = SAMPLES[name]
    monkeypatch.setattr(confirming, "readback", answering(sample["ch"](sample["miss"])))

    result = job_settings.confirm(object(), name, "JOB", poll_window=0.02, **arguments(name, sample, sample["target"]))

    assert result["success"] is False
    (entry,) = result["logs"]
    assert entry["level"] == "warning"
    assert "not seen" in entry["msg"]


def test_the_tolerances():
    tolerances = {name: setting.tolerance for name, setting in job_settings.SETTINGS.items() if setting.tolerance}
    assert tolerances == {
        "zoom": 0.1,
        "scan_field_rotation": 0.5,
        "z_stack_definition": 1.0,
        "z_stack_step_size": 0.5,
        "z_stack_size": 1.5,
        "pinhole_airy": 0.05,
        "detector_gain": 1.0,
        "laser_intensity": 0.005,
        "filter_wheel_spectrum": 1.0,
    }


def test_a_readback_without_the_value_yet_is_looked_at_again_until_the_window_ends(monkeypatch):
    monkeypatch.setattr(confirming, "readback", answering({"wrong": "shape"}))

    result = job_settings.confirm(object(), "scan_speed", "JOB", value=600, poll_window=0.02)

    assert result["success"] is False


@pytest.mark.parametrize(
    "begin, end, read_begin, read_end, step",
    [
        (0.0, 20.0, 0.0, 20.0, None),  # as asked
        (0.0, 20.0, 1.0, 19.0, 3.0),  # snapped to 6 steps of 3 around the centre 10
        (10.0, -10.0, 10.5, -10.5, 3.0),  # a descending stack keeps its direction
    ],
)
def test_a_z_stack_counts_as_asked_or_as_las_x_snaps_it_to_its_step(begin, end, read_begin, read_end, step, monkeypatch):
    stack = {"stack": {"begin": read_begin, "end": read_end, "stepSize": step}}
    monkeypatch.setattr(confirming, "readback", answering(stack))

    result = job_settings.confirm(object(), "z_stack_definition", "JOB", begin_um=begin, end_um=end, poll_window=0.5)

    assert result["success"] is True


def test_a_wrong_setting_or_argument_is_refused_before_anything_is_sent():
    with pytest.raises(ValueError, match="no setting"):
        job_settings.change(object(), "brightness", "JOB", value=1)
    with pytest.raises(ValueError, match="unknown"):
        job_settings.change(object(), "zoom", "JOB", value=2.0, colour="red")
    with pytest.raises(ValueError, match="missing"):
        job_settings.change(object(), "detector_gain", "JOB", value=700)
