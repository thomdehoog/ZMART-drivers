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


def test_a_detector_is_switched_on_with_one_command_and_read_back(lasx, client, tmp_path):
    result = setting_files.set_detector(client, tmp_path, "Trans PMT", on=True)

    assert result["confirmed"] is True
    assert "Trans PMT" in detectors()
    assert lasx.commands() == ["SetDetectorActive"]


def test_only_setting_1_is_changed_in_the_file_loaded(lasx, client, tmp_path):
    setting_files.set_detector(client, tmp_path, "HyD S 3", dye="Leica/ALEXA 555")

    loaded = (tmp_path / lasx.loaded[-1].split("\\")[-1].split("/")[-1]).read_text(encoding="utf-8")
    best_focus = loaded[loaded.index("Best Focus Setting"):]
    hyd = re.search(r'<Detector [^>]*Name="HyD S 3"[^>]*>', best_focus)[0]
    assert 'DyeName="Leica/ALEXA 555"' not in hyd
    assert detectors()["HyD S 3"]["dye"] == "Leica/ALEXA 555"


def test_a_detector_s_band_is_changed_and_read_back(lasx, client, tmp_path):
    result = setting_files.set_detector(client, tmp_path, "HyD S 3", band=(550.0, 650.0))

    assert result["confirmed"] is True
    assert (detectors()["HyD S 3"]["detectionBegin"], detectors()["HyD S 3"]["detectionEnd"]) == (550.0, 650.0)


def test_a_detector_the_setting_does_not_have_is_refused_before_anything_is_loaded(lasx, client, tmp_path):
    result = setting_files.set_detector(client, tmp_path, "HyD 9", on=True)

    assert result["success"] is False and "HyD 9" in result["message"]
    assert "ATL_Block_Load_From_XML" not in lasx.commands()


def test_with_several_settings_a_dye_is_refused_but_on_off_still_works(lasx, client, tmp_path):
    lasx.sequential = 2

    result = setting_files.set_detector(client, tmp_path, "Trans PMT", dye="Leica/ALEXA 488")

    assert result["success"] is False and "sequential" in result["message"]
    assert "ATL_Block_Load_From_XML" not in lasx.commands()
    assert setting_files.set_detector(client, tmp_path, "Trans PMT", on=True)["confirmed"] is True


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
    assert direct_reader.get_detector_modes()["HyD S 3"]["operating"] == {"Fast Mode": 4, "Counting": 1, "Analog": 10, "Reflection": 11}[mode]


def test_a_tau_mode_las_x_greys_out_is_refused_and_nothing_is_sent(lasx, client, tmp_path):
    units = [u for spectro in lasx.nodes.detection["Spectrophotometer"] for u in spectro["DetectionUnitList"]["DetectorHWInfo"]]
    next(u for u in units if tuple(u["BeamRoute"]) == (40, 3))["TauDisabled"] = (0,)  # HyD S 3: None greyed out
    sent = len(lasx.sent)

    result = setting_files.set_detector(client, tmp_path, "HyD S 3", tau_mode="None")

    assert result["success"] is False and "greys" in result["message"]
    assert len(lasx.sent) == sent


class TestTauInteraction:
    def test_the_donor_lifetime_is_set_with_the_window_s_command_and_read_back(self, lasx, client, tmp_path):
        from zmart_drivers.leica.stellaris.navigator.procedures import changes

        setting_files.set_detector(client, tmp_path, "HyD S 3", tau_mode="TauInteraction")
        assert direct_reader.get_detector_modes()["HyD S 3"]["donor_lifetime_ns"] == pytest.approx(2.4)

        assert changes.set_tau_donor_lifetime(client, "HyD S 3", 3.1)["confirmed"] is True
        assert direct_reader.get_detector_modes()["HyD S 3"]["donor_lifetime_ns"] == pytest.approx(3.1)

    @pytest.mark.parametrize("ns", [0.05, 12.0, "3"])
    def test_a_lifetime_off_the_window_s_scale_is_refused(self, lasx, client, tmp_path, ns):
        from zmart_drivers.leica.stellaris.navigator.procedures import changes

        setting_files.set_detector(client, tmp_path, "HyD S 3", tau_mode="TauInteraction")
        assert changes.set_tau_donor_lifetime(client, "HyD S 3", ns)["success"] is False

    def test_outside_tau_interaction_it_is_refused(self, lasx, client):
        from zmart_drivers.leica.stellaris.navigator.procedures import changes

        assert changes.set_tau_donor_lifetime(client, "HyD S 3", 3.0)["success"] is False


class TestTauSeparationChannels:
    @pytest.fixture
    def separating(self, lasx, client, tmp_path):
        setting_files.set_detector(client, tmp_path, "HyD S 3", tau_mode="TauSeparation")
        return client

    def test_a_channel_is_added_and_removed_as_the_window_s_plus_and_minus_do(self, lasx, separating):
        from zmart_drivers.leica.stellaris.navigator.procedures import changes

        assert changes.add_tau_channel(separating, "HyD S 3")["confirmed"] is True
        assert len(direct_reader.get_detector_modes()["HyD S 3"]["tau_colours"]) == 3
        assert changes.remove_tau_channel(separating, "HyD S 3", 3)["confirmed"] is True
        assert direct_reader.get_detector_modes()["HyD S 3"]["tau_colours"] == ["Cyan", "Magenta"]

    def test_two_channels_always_stay_and_at_most_seven(self, lasx, separating):
        from zmart_drivers.leica.stellaris.navigator.procedures import changes

        assert changes.remove_tau_channel(separating, "HyD S 3", 2)["success"] is False
        for _ in range(5):
            changes.add_tau_channel(separating, "HyD S 3")
        assert changes.add_tau_channel(separating, "HyD S 3")["success"] is False

    def test_the_colour_s_lock_is_sent_as_the_window_s_lock(self, lasx, separating):
        from zmart_drivers.leica.stellaris.navigator.procedures import changes

        changes.set_tau_channel_colour(separating, "HyD S 3", 1, "Cyan", locked=False)

        assert 'IsUserDefined="0"' in lasx.sent[-1]


class TestTauScan:
    @pytest.fixture
    def scanning(self, lasx, client, tmp_path):
        setting_files.set_detector(client, tmp_path, "HyD S 3", tau_mode="TauScan")
        return client

    def test_the_images_and_range_are_read(self, lasx, scanning):
        assert direct_reader.get_detector_modes()["HyD S 3"]["tau_scan"] == {"images": 3, "range_ns": pytest.approx((0.1, 10.0)), "most_images": 7}

    def test_they_are_set_with_the_window_s_commands(self, lasx, scanning):
        from zmart_drivers.leica.stellaris.navigator.procedures import changes

        result = changes.set_tau_scan(scanning, "HyD S 3", images=5, range_ns=(0.5, 8.0))

        assert result["confirmed"] is True
        assert direct_reader.get_detector_modes()["HyD S 3"]["tau_scan"]["images"] == 5
        assert [x.split('"')[1] for x in lasx.sent[-2:]] == ["SetDetectorTauScanCount", "SetDetectorTauScanRange"]

    @pytest.mark.parametrize("keywords", [{"images": 8}, {"images": 0}, {"range_ns": (0.01, 5.0)}, {"range_ns": (1.0, 20.0)},
                                          {"range_ns": (5.0, 2.0)}, {}])
    def test_what_the_window_does_not_offer_is_refused(self, lasx, scanning, keywords):
        from zmart_drivers.leica.stellaris.navigator.procedures import changes

        sent = len(lasx.sent)
        assert changes.set_tau_scan(scanning, "HyD S 3", **keywords)["success"] is False
        assert len(lasx.sent) == sent

    def test_outside_tau_scan_it_is_refused(self, lasx, client):
        from zmart_drivers.leica.stellaris.navigator.procedures import changes

        assert changes.set_tau_scan(client, "HyD S 3", images=4)["success"] is False


class TestTauGating:
    @pytest.fixture
    def gating(self, lasx, client, tmp_path):
        setting_files.set_detector(client, tmp_path, "HyD S 3", tau_mode="TauGating")
        return client

    def test_the_channels_and_gates_are_read(self, lasx, gating):
        assert direct_reader.get_detector_modes()["HyD S 3"]["gates"] == [{"on": True, "gates": [pytest.approx((0.3, 8.0))]}]

    def test_a_gate_is_moved_as_the_window_s_handles_do(self, lasx, gating):
        from zmart_drivers.leica.stellaris.navigator.procedures import changes

        result = changes.set_tau_gate(gating, "HyD S 3", 1, 1, 0.5, 6.0)

        assert result["confirmed"] is True
        assert lasx.sent[-1].startswith('<Scanner Command="SetDigitalGateConfig" ChannelIndex="0" SetMode="0" SingleSettingIndex="0" '
                                        'GateIndex1="0" GateBegin1="5e-10" GateEnd1="6e-09">')

    def test_gates_and_channels_are_added_and_removed(self, lasx, gating):
        from zmart_drivers.leica.stellaris.navigator.procedures import changes

        assert changes.add_tau_gate(gating, "HyD S 3", 1, 9.0, 11.0)["confirmed"] is True
        assert changes.add_tau_gate_channel(gating, "HyD S 3", 1.0, 3.0)["confirmed"] is True
        gates = direct_reader.get_detector_modes()["HyD S 3"]["gates"]
        assert [len(channel["gates"]) for channel in gates] == [2, 1]
        assert changes.remove_tau_gate_channel(gating, "HyD S 3", 2)["confirmed"] is True
        assert changes.remove_tau_gate(gating, "HyD S 3", 1, 2)["confirmed"] is True
        assert [len(channel["gates"]) for channel in direct_reader.get_detector_modes()["HyD S 3"]["gates"]] == [1]

    @pytest.mark.parametrize(("begin", "end"), [(-2.0, 3.0), (1.0, 12.0), (5.0, 4.0)])
    def test_a_gate_off_the_window_s_scale_is_refused(self, lasx, gating, begin, end):
        from zmart_drivers.leica.stellaris.navigator.procedures import changes

        sent = len(lasx.sent)
        assert changes.set_tau_gate(gating, "HyD S 3", 1, 1, begin, end)["success"] is False
        assert len(lasx.sent) == sent

    def test_gates_outside_tau_gating_or_not_there_are_refused(self, lasx, client, tmp_path):
        from zmart_drivers.leica.stellaris.navigator.procedures import changes

        assert changes.set_tau_gate(client, "HyD S 3", 1, 1, 0.5, 6.0)["success"] is False
        setting_files.set_detector(client, tmp_path, "HyD S 3", tau_mode="TauGating")
        assert changes.set_tau_gate(client, "HyD S 3", 1, 2, 0.5, 6.0)["success"] is False
        assert changes.remove_tau_gate_channel(client, "HyD S 3", 1)["success"] is False


def test_a_tau_separation_channel_s_colour_is_set_as_the_window_sets_it(lasx, client, tmp_path):
    from zmart_drivers.leica.stellaris.navigator.procedures import changes

    setting_files.set_detector(client, tmp_path, "HyD S 3", tau_mode="TauSeparation")
    assert direct_reader.get_detector_modes()["HyD S 3"]["tau_colours"] == ["Cyan", "Magenta"]

    result = changes.set_tau_channel_colour(client, "HyD S 3", 1, "Red")

    assert result["confirmed"] is True
    assert direct_reader.get_detector_modes()["HyD S 3"]["tau_colours"] == ["Red", "Magenta"]
    assert lasx.sent[-1].startswith('<Scanner Command="SetDetectorTauSeparationLut" TauChannelIndex="0" SetMode="2" SingleSettingID="3" IsUserDefined="1" LutName="Red">')


def test_a_tau_separation_channel_s_lifetime_is_set_as_the_window_s_slider_sets_it(lasx, client, tmp_path):
    from zmart_drivers.leica.stellaris.navigator.procedures import changes

    setting_files.set_detector(client, tmp_path, "HyD S 3", tau_mode="TauSeparation")
    assert direct_reader.get_detector_modes()["HyD S 3"]["tau_lifetimes_ns"] == pytest.approx([1.0, 3.0])

    result = changes.set_tau_channel_lifetime(client, "HyD S 3", 2, 4.5)

    assert result["confirmed"] is True
    assert direct_reader.get_detector_modes()["HyD S 3"]["tau_lifetimes_ns"] == pytest.approx([1.0, 4.5])
    assert lasx.sent[-1].startswith('<Scanner Command="SetDetectorTauSeparationConfig" TauChannelIndex="1" SeparationLine1="4.5e-09">')


def test_find_reports_that_las_x_found_no_lifetimes(lasx, client, tmp_path, monkeypatch):
    from zmart_drivers.leica.stellaris.navigator.procedures import changes

    log = tmp_path / "LifeTimeProcessTransfer.log"
    monkeypatch.setattr(changes, "LIFETIME_LOG", log)
    lasx.lifetime_log = (log, 0, None)
    setting_files.set_detector(client, tmp_path, "HyD S 3", tau_mode="TauSeparation")

    result = changes.find_tau_lifetimes(client, "HyD S 3")

    assert result["success"] is True and result["found"] is False
    assert result["lifetimes_ns"] == pytest.approx([1.0, 3.0])
    assert lasx.commands()[-1] == "FindTauSeparationLifeTimes"


def test_find_reports_the_lifetimes_las_x_found(lasx, client, tmp_path, monkeypatch):
    from zmart_drivers.leica.stellaris.navigator.procedures import changes

    log = tmp_path / "LifeTimeProcessTransfer.log"
    monkeypatch.setattr(changes, "LIFETIME_LOG", log)
    lasx.lifetime_log = (log, 1, (0.8e-09, 4.2e-09))
    setting_files.set_detector(client, tmp_path, "HyD S 3", tau_mode="TauSeparation")

    result = changes.find_tau_lifetimes(client, "HyD S 3")

    assert result["found"] is True
    assert result["lifetimes_ns"] == pytest.approx([0.8, 4.2])


def test_find_outside_tau_separation_is_refused(lasx, client):
    from zmart_drivers.leica.stellaris.navigator.procedures import changes

    assert changes.find_tau_lifetimes(client, "HyD S 3")["success"] is False


@pytest.mark.parametrize("ns", [0.01, 20.0, "3"])
def test_a_tau_lifetime_off_the_window_s_scale_is_refused(lasx, client, tmp_path, ns):
    from zmart_drivers.leica.stellaris.navigator.procedures import changes

    setting_files.set_detector(client, tmp_path, "HyD S 3", tau_mode="TauSeparation")
    sent = len(lasx.sent)

    assert changes.set_tau_channel_lifetime(client, "HyD S 3", 1, ns)["success"] is False
    assert len(lasx.sent) == sent


def test_a_tau_channel_colour_outside_tau_separation_or_channel_1_and_2_is_refused(lasx, client, tmp_path):
    from zmart_drivers.leica.stellaris.navigator.procedures import changes

    assert changes.set_tau_channel_colour(client, "HyD S 3", 1, "Red")["success"] is False
    setting_files.set_detector(client, tmp_path, "HyD S 3", tau_mode="TauSeparation")
    assert changes.set_tau_channel_colour(client, "HyD S 3", 3, "Red")["success"] is False
    assert changes.set_tau_channel_colour(client, "HyD S 3", 2, "")["success"] is False
    assert changes.set_tau_channel_colour(client, "HyD 9", 1, "Red")["success"] is False


@pytest.mark.parametrize("mode", ["None", "Intensity", "TauGating", "TauScan", "TauSeparation", "TauInteraction"])
def test_each_tau_mode_is_written_as_las_x_writes_it_and_read_back(lasx, client, tmp_path, mode):
    assert setting_files.set_detector(client, tmp_path, "HyD S 3", tau_mode=mode)["confirmed"] is True
    assert direct_reader.get_detector_modes()["HyD S 3"]["tau"] == {"None": 0, "Intensity": 2, "TauGating": 3, "TauScan": 5, "TauSeparation": 6, "TauInteraction": 10}[mode]


def test_the_dye_is_set_through_the_setting_and_read_back(lasx, client, tmp_path):
    result = setting_files.set_detector(client, tmp_path, "HyD S 3", dye="Leica/ALEXA 488")

    assert result["confirmed"] is True
    assert setting_1(tmp_path, lasx)["detectors"]["HyD S 3"]["dye"] == "Leica/ALEXA 488"


def test_tau_contrast_is_written_as_the_detector_panel_s_switch_and_read_back(lasx, client, tmp_path):
    loaded = len(lasx.loaded)

    result = setting_files.set_detector(client, tmp_path, "HyD S 3", tau_contrast=True)

    assert result["confirmed"] is True and direct_reader.get_detector_modes()["HyD S 3"]["tau_contrast"] is True
    assert len(lasx.loaded) == loaded  # no setting file: it works in any setting
    assert setting_files.set_detector(client, tmp_path, "HyD S 3", tau_contrast=False)["confirmed"] is True


def test_tau_contrast_where_the_detector_does_not_allow_it_is_refused(lasx, client, tmp_path):
    units = [u for spectro in lasx.nodes.detection["Spectrophotometer"] for u in spectro["DetectionUnitList"]["DetectorHWInfo"]]
    next(u for u in units if tuple(u["BeamRoute"]) == (40, 3))["TauContrastAllowed"] = False

    assert setting_files.set_detector(client, tmp_path, "HyD S 3", tau_contrast=True)["success"] is False


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



def test_on_gain_and_band_go_with_las_x_s_commands_not_a_setting_file(lasx, client, tmp_path):
    result = setting_files.set_detector(client, tmp_path, "HyD S 3", gain=100, band=(520, 676))

    assert result["confirmed"] is True
    assert lasx.commands() == ["SetDetectorGain", "SetSpectrophotometerMultiBandRange"]
    shown = detectors()["HyD S 3"]
    assert shown["gain"]["value"] == 100 and (shown["detectionBegin"], shown["detectionEnd"]) == (520, 676)


def test_a_detector_is_switched_off_and_on_with_las_x_s_command(lasx, client, tmp_path):
    assert setting_files.set_detector(client, tmp_path, "HyD 2", on=False)["confirmed"] is True
    assert "HyD 2" not in detectors()
    assert setting_files.set_detector(client, tmp_path, "HyD 2", on=True)["confirmed"] is True
    assert "HyD 2" in detectors()
    assert lasx.commands() == ["SetDetectorActive", "SetDetectorActive"]


def test_a_gain_outside_the_detector_s_own_range_is_refused(lasx, client, tmp_path):
    result = setting_files.set_detector(client, tmp_path, "HyD S 3", gain=900)

    assert result["success"] is False and "2.5 to 500" in result["message"]
    assert lasx.commands() == []


def test_the_tau_mode_is_its_place_in_the_detector_s_list(lasx, client, tmp_path):
    result = setting_files.set_detector(client, tmp_path, "HyD S 3", tau_mode="TauScan")

    assert result["confirmed"] is True
    assert 'GatingModeIndex="3"' in lasx.sent[-1]



def test_the_operating_mode_is_written_as_the_window_does_and_read_back(lasx, client, tmp_path):
    result = setting_files.set_detector(client, tmp_path, "HyD S 3", operating_mode="Analog", gain=100)

    assert result["confirmed"] is True
    assert client.reply["element"].startswith('<Scanner Command="SetDetectorGain"')  # the mode first, the gain after it
    assert direct_reader.get_detector_modes()["HyD S 3"]["operating"] == 10
    assert "ATL_Block_Load_From_XML" not in lasx.commands()


def test_an_operating_mode_the_detector_does_not_offer_is_refused(lasx, client, tmp_path):
    lasx.nodes.operating_offered = (4, 1)

    result = setting_files.set_detector(client, tmp_path, "HyD S 3", operating_mode="Analog")

    assert result["success"] is False and "Analog" in result["message"]
