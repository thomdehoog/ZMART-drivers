"""The script for the first visit to a real microscope runs, here against the pretend one.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "hardware" / "check_hardware.py"


def test_every_check_answers_on_the_pretend_microscope(
    stand_in, key_file, tmp_path, monkeypatch
):
    spec = importlib.util.spec_from_file_location("check_hardware", SCRIPT)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    monkeypatch.setattr(script, "REPORT", tmp_path / "hardware.json")
    code = script.main(
        [
            "--port",
            str(stand_in.port),
            "--move",
            "--objective",
            "2",
            "--snap",
            str(tmp_path / "pictures"),
        ]
    )
    answers = json.loads((tmp_path / "hardware.json").read_text())
    assert code == 0, [a for a in answers if a["outcome"] == "failed"]
    assert {"11.1 xy", "11.3", "11.4", "11.7", "11.9 turn"} <= {
        a["check"] for a in answers
    }
    assert stand_in.scope.position()[:2] == (1000.0, 2000.0)
