"""Changes through a saved setting file, against the stand-in for LAS X's plain Navigator.

LAS X's window changes the detectors without a command LCS logs; its Setting
panel saves and loads a whole setting as a file with two commands it does log
(simulator, 2026-11-01). Values changed in Setting 1 of a saved file and
loaded again were applied: a detector on, its gain, its band, a laser line's
power. These tests check the driver changes only Setting 1, checks the file's
values against the limits before it loads it, reads the change back, and
reports anything else that changed.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

import re

import pytest
from lasx_stand_in import PlainNavigator, attach
from limits_fixtures import (
    DEFAULT_STAGE_UM,
    install_permissive_limits,
    merged_limits_payload,
)

from zmart_drivers.leica.stellaris.navigator.dispatcher import limits as gate
from zmart_drivers.leica.stellaris.navigator.driver_setup.limits import limit_rules
from zmart_drivers.leica.stellaris.navigator.primitives.direct import (
    client as hardware_server,
)
from zmart_drivers.leica.stellaris.navigator.primitives.direct import (
    reads as direct_reader,
)
from zmart_drivers.leica.stellaris.navigator.procedures import setting_files


@pytest.fixture
def lasx(monkeypatch):
    return attach(monkeypatch, PlainNavigator())


@pytest.fixture
def client(lasx):
    return install_permissive_limits(hardware_server.Client("test"), wide_stage=True)


def detectors():
    return {d["name"]: d for d in direct_reader.get_detectors()}


def test_a_detector_is_switched_on_through_setting_1_and_read_back(lasx, client, tmp_path):
    result = setting_files.set_detector(client, tmp_path, "Trans PMT", on=True)

    assert result["confirmed"] is True
    assert "Trans PMT" in detectors()
    assert lasx.commands() == ["ATL_Block_Save_To_XML", "ATL_Block_Load_From_XML"]


def test_only_setting_1_is_changed_in_the_file_loaded(lasx, client, tmp_path):
    setting_files.set_detector(client, tmp_path, "HyD S 3", gain=50.0)

    loaded = (tmp_path / lasx.loaded[-1].split("\\")[-1].split("/")[-1]).read_text(encoding="utf-8")
    best_focus = loaded[loaded.index("Best Focus Setting"):]
    hyd = re.search(r'<Detector [^>]*Name="HyD S 3"[^>]*>', best_focus)[0]
    assert ' Gain="2.5"' in hyd
    assert detectors()["HyD S 3"]["gain"]["value"] == pytest.approx(50.0)


def test_a_detector_s_band_is_changed_and_read_back(lasx, client, tmp_path):
    result = setting_files.set_detector(client, tmp_path, "HyD S 3", band=(550.0, 650.0))

    assert result["confirmed"] is True
    assert (detectors()["HyD S 3"]["detectionBegin"], detectors()["HyD S 3"]["detectionEnd"]) == (550.0, 650.0)


def test_a_detector_the_setting_does_not_have_is_refused_before_anything_is_loaded(lasx, client, tmp_path):
    result = setting_files.set_detector(client, tmp_path, "HyD 9", on=True)

    assert result["success"] is False and "HyD 9" in result["message"]
    assert "ATL_Block_Load_From_XML" not in lasx.commands()


def test_several_sequential_settings_are_refused(lasx, client, tmp_path):
    lasx.sequential = 2

    result = setting_files.set_detector(client, tmp_path, "Trans PMT", on=True)

    assert result["success"] is False and "sequential" in result["message"]
    assert "ATL_Block_Load_From_XML" not in lasx.commands()


def test_a_file_with_a_laser_above_the_limit_is_refused_before_it_is_loaded(lasx, tmp_path):
    limited = hardware_server.Client("test")
    payload = merged_limits_payload(dict(DEFAULT_STAGE_UM))
    gate.install(limited, gate.GateState(
        limits=limit_rules.LeicaLimits(payload, source="test", path=None, is_fallback=False), stage_cfg=None, error=None))
    try:
        lasx.nodes.light_sources[0]["AOTF"]["AotfLine"][0]["Intensity"] = 50.0
        operators = tmp_path / "operators.seq"
        lasx.take_ATL_Block_Save_To_XML(type("E", (), {"get": lambda self, k: str(operators)})())
        lasx.nodes.light_sources[0]["AOTF"]["AotfLine"][0]["Intensity"] = 2.0

        result = setting_files.load(limited, operators)
    finally:
        gate.uninstall(limited)

    assert result["success"] is False and "set_laser_power" in result["message"]
    assert "ATL_Block_Load_From_XML" not in lasx.commands()


def test_an_operator_s_setting_is_loaded_and_what_changed_is_reported(lasx, client, tmp_path):
    operators = tmp_path / "operators.seq"
    lasx.nodes.detection["Spectrophotometer"][1]["DetectionUnitList"]["DetectorHWInfo"][0]["IsActive"] = True
    lasx.take_ATL_Block_Save_To_XML(type("E", (), {"get": lambda self, k: str(operators)})())
    lasx.nodes.detection["Spectrophotometer"][1]["DetectionUnitList"]["DetectorHWInfo"][0]["IsActive"] = False

    result = setting_files.load(client, operators)

    assert result["success"] is True
    assert "Trans PMT" in detectors()
    assert any("Trans PMT" in change for change in result["changed"])



def setting_1(tmp_path, lasx):
    """Setting 1 as LAS X would save it now, read the way the driver reads it back."""
    return setting_files.read_back(lasx_saved(tmp_path, lasx))


def lasx_saved(tmp_path, lasx):
    path = tmp_path / "now.seq"
    lasx.take_ATL_Block_Save_To_XML(type("E", (), {"get": lambda self, k: str(path)})())
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("mode", ["Fast Mode", "Counting", "Analog", "Reflection"])
def test_each_operating_mode_is_written_as_las_x_writes_it_and_read_back(lasx, client, tmp_path, mode):
    result = setting_files.set_detector(client, tmp_path, "HyD S 3", operating_mode=mode)

    assert result["confirmed"] is True
    assert setting_1(tmp_path, lasx)["detectors"]["HyD S 3"]["operating_mode"] == mode


@pytest.mark.parametrize("mode", ["Intensity", "TauGating", "TauScan", "TauSeparation", "TauInteraction"])
def test_each_tau_mode_is_written_as_las_x_writes_it_and_read_back(lasx, client, tmp_path, mode):
    assert setting_files.set_detector(client, tmp_path, "HyD S 3", tau_mode=mode)["confirmed"] is True
    assert setting_1(tmp_path, lasx)["detectors"]["HyD S 3"]["tau_mode"] == mode


def test_tau_contrast_and_the_dye_are_set_and_read_back(lasx, client, tmp_path):
    result = setting_files.set_detector(client, tmp_path, "HyD S 3", tau_contrast=True, dye="Leica/ALEXA 488")

    assert result["confirmed"] is True
    detector = setting_1(tmp_path, lasx)["detectors"]["HyD S 3"]
    assert (detector["tau_contrast"], detector["dye"]) == (True, "Leica/ALEXA 488")


def test_an_operating_mode_las_x_does_not_have_is_refused(lasx, client, tmp_path):
    result = setting_files.set_detector(client, tmp_path, "HyD S 3", operating_mode="Turbo")

    assert result["success"] is False and "Turbo" in result["message"]
    assert "ATL_Block_Load_From_XML" not in lasx.commands()


def test_the_excitation_line_is_chosen_or_left_to_las_x_and_read_back(lasx, client, tmp_path):
    lasx.nodes.detection["Spectrophotometer"][0]["DetectionUnitList"]["DetectorHWInfo"][0]["IsActive"] = False

    assert setting_files.set_excitation_line(client, tmp_path, 405.0)["confirmed"] is True
    assert setting_1(tmp_path, lasx)["excitation_line"] == 405.0
    assert setting_files.set_excitation_line(client, tmp_path, "auto")["confirmed"] is True
    assert setting_1(tmp_path, lasx)["excitation_line"] == "auto"


def test_the_excitation_line_is_refused_with_several_detectors_on(lasx, client, tmp_path):
    result = setting_files.set_excitation_line(client, tmp_path, 405.0)

    assert result["success"] is False and "detector" in result["message"]


def test_the_dye_spectra_are_shown_or_hidden_and_read_back(lasx, client, tmp_path):
    result = setting_files.show_spectra(client, tmp_path, excitation=True, emission=False)

    assert result["confirmed"] is True
    assert setting_1(tmp_path, lasx)["show"] == {"excitation": True, "emission": False}


def test_the_setting_is_read_back_also_into_a_folder_that_does_not_exist_yet(lasx, client, tmp_path):
    found = setting_files.saved_now(client, tmp_path / "new" / "folder")

    assert "HyD S 3" in found["detectors"]



def test_setting_1_is_found_whatever_it_is_called():
    text = (
        '<LDM_Block_Sequential><LDM_Block_Sequential_Master><ATLConfocalSettingDefinition UserSettingName="S2"/>'
        '</LDM_Block_Sequential_Master><LDM_Block_Sequential_List><ATLConfocalSettingDefinition UserSettingName="ZMART test">'
        '<Detector Name="HyD S 3"/></ATLConfocalSettingDefinition><Autofocus_Block/></LDM_Block_Sequential_List></LDM_Block_Sequential>'
    )
    start, end = setting_files.setting_1(text)
    assert text[start:end].startswith('<ATLConfocalSettingDefinition UserSettingName="ZMART test">')
