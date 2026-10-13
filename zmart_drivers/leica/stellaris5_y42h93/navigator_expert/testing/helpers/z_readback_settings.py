"""Job settings and saved-experiment data shaped as LAS X gives them, for the Z readback tests."""


def raw_settings(*, galvo_um=0.0, wide_um=4999.5, stack_on=None):
    """Raw job settings as ``get_job_settings`` returns them (LAS X nests each
    drive's value as ``{"position": ...}``)."""
    settings = {
        "zoom": {"current": 1.0},
        "scanSpeed": {"value": 400, "isResonant": False},
        "activeSettings": [],
        "scanMode": "xyz",
        "zPosition": {"z-galvo": {"position": galvo_um}, "z-wide": {"position": wide_um}},
    }
    if stack_on is not None:
        settings["stack"] = {
            "begin": -10.0,
            "end": 10.0,
            "stepSize": 1.0,
            "size": 20.0,
            "sections": 21,
            "mode": stack_on,
        }
    return settings


def saved(job_name, drive, z_um):
    """Parsed-.lrp shaped data with one job's Master ZPosition (metres)."""
    return {
        "sequence_name": "collecting pattern",
        "sequence_elements": [],
        "jobs": {
            job_name: {"Master": {"attrs": {"ZPosition": repr(z_um * 1e-6), "ZUseModeName": drive}}}
        },
    }
