"""ROI regions and bleach points put into LAS X's data container, with no window: the procedures against a stand-in
data container that keeps its sets in memory as LAS X's does."""

import xml.etree.ElementTree as ET

import pytest
from lasx_stand_in import PlainNavigator, attach
from limits_fixtures import install_permissive_limits

from zmart_drivers.leica.stellaris.navigator.primitives.direct import data_container as regions
from zmart_drivers.leica.stellaris.navigator.primitives.direct import client as hardware_server
from zmart_drivers.leica.stellaris.navigator.procedures import changes

ROI = ("Confocal", "ROIScan")


class DataContainer:
    """LAS X's data container as far as the driver uses it: sets of ROISingle children, read, added to and cleared."""

    def __init__(self):
        self.sets = {177: [], 178: []}
        self.requests = []

    def __call__(self, do, set_id, xml=None):
        self.requests.append((do, set_id))
        if set_id not in self.sets:
            return {"success": False, "message": f"no element {set_id}"}
        if do == "add":
            root = ET.fromstring(xml)
            self.sets[set_id] += [ET.tostring(child, encoding="unicode") for child in root.find("Element/Children")]
        elif do == "clear":
            self.sets[set_id] = []
        return {"success": True, "xml": regions.set_xml(self.sets[set_id])}


@pytest.fixture
def lasx(monkeypatch):
    return attach(monkeypatch, PlainNavigator())


@pytest.fixture
def client(lasx):
    lasx.live[(*ROI, "CurrentDCROISetID")] = 177
    lasx.live[(*ROI, "CurrentDCBleachPointROISetID")] = 178
    return install_permissive_limits(hardware_server.Client("test"), wide_stage=True)


@pytest.fixture
def container():
    return DataContainer()


class TestRegions:
    REGIONS = [{"shape": "rectangle", "centre_um": (-50.0, 30.0), "size_um": (120.0, 80.0)},
               {"shape": "polygon", "vertices_um": [(0.0, 0.0), (40.0, 10.0), (20.0, 50.0)]}]

    def test_regions_go_into_the_current_set_and_are_read_back(self, lasx, client, container):
        lasx.live[(*ROI, "EnableRoiScan")] = True

        result = changes.add_roi_regions(client, self.REGIONS, container=container)

        assert result["confirmed"] is True and [r["shape"] for r in result["regions"]] == ["rectangle", "polygon"]
        assert container.requests == [("read", 177), ("add", 177)]

    def test_more_regions_are_named_after_those_there(self, lasx, client, container):
        lasx.live[(*ROI, "EnableRoiScan")] = True
        changes.add_roi_regions(client, self.REGIONS, container=container)

        result = changes.add_roi_regions(client, [{"shape": "range", "from_um": -10.0, "to_um": 10.0}], container=container)

        assert [r["name"] for r in result["regions"]] == ["ROI 1", "ROI 2", "ROI 3"]

    def test_without_roi_scan_nothing_is_added(self, lasx, client, container):
        assert "set_roi_scan" in changes.add_roi_regions(client, self.REGIONS, container=container)["message"]
        assert container.requests == []

    @pytest.mark.parametrize("region", [{"shape": "rectangle", "centre_um": (600.0, 0.0), "size_um": (50.0, 50.0)},
                                        {"shape": "polygon", "vertices_um": [(0.0, 0.0), (10.0, 0.0), (0.0, -700.0)]}])
    def test_a_region_beyond_the_field_is_refused_and_nothing_is_added(self, lasx, client, container, region):
        lasx.live[(*ROI, "EnableRoiScan")] = True

        result = changes.add_roi_regions(client, [region], container=container)

        assert result["success"] is False and "field" in result["message"] and ("add", 177) not in container.requests

    def test_what_is_not_a_region_is_refused_and_nothing_is_added(self, lasx, client, container):
        lasx.live[(*ROI, "EnableRoiScan")] = True
        assert changes.add_roi_regions(client, [{"shape": "star"}], container=container)["success"] is False
        assert ("add", 177) not in container.requests

    def test_a_set_that_does_not_read_back_as_written_is_not_confirmed(self, lasx, client, container):
        lasx.live[(*ROI, "EnableRoiScan")] = True
        honest = container.__call__

        def drops_one(do, set_id, xml=None):
            answer = honest(do, set_id, xml)
            if do == "add":
                container.sets[set_id] = container.sets[set_id][:-1]
                answer["xml"] = regions.set_xml(container.sets[set_id])
            return answer

        result = changes.add_roi_regions(client, self.REGIONS, container=drops_one)

        assert result["confirmed"] is False and "1 of 2" in result["message"]

    def test_the_regions_are_cleared(self, lasx, client, container):
        lasx.live[(*ROI, "EnableRoiScan")] = True
        changes.add_roi_regions(client, self.REGIONS, container=container)

        result = changes.clear_roi_regions(client, container=container)

        assert result["confirmed"] is True and result["regions"] == [] and container.sets[177] == []

    def test_a_data_container_that_refuses_is_reported(self, lasx, client):
        lasx.live[(*ROI, "EnableRoiScan")] = True
        refusing = lambda do, set_id, xml=None: {"success": False, "message": "the wrapper is version 2.70.0.0"}  # noqa: E731

        result = changes.add_roi_regions(client, self.REGIONS, container=refusing)

        assert result["success"] is False and "2.70" in result["message"]


def test_the_sets_are_read_as_the_driver_gives_them(lasx, client, container):
    lasx.live[(*ROI, "EnableRoiScan")] = True
    changes.add_roi_regions(client, TestRegions.REGIONS, container=container)

    result = changes.read_roi_set(client, "regions", container=container)

    assert result["success"] is True and [r["shape"] for r in result["regions"]] == ["rectangle", "polygon"]
    assert changes.read_roi_set(client, "bleach_points", container=container)["points"] == []
    assert changes.read_roi_set(client, "stars", container=container)["success"] is False


class TestBleachPoints:
    def test_points_go_in_with_their_times_and_are_read_back(self, lasx, client, container):
        lasx.live[(*ROI, "EnableBleachScan")] = True

        result = changes.add_bleach_points(client, [{"at_um": (10.0, -20.0), "seconds": 3.0}, {"at_um": (0.0, 0.0), "seconds": 0.5}],
                                           container=container)

        assert result["confirmed"] is True and [p["seconds"] for p in result["points"]] == [3.0, 0.5]
        assert container.requests[-1] == ("add", 178)

    def test_without_the_bleach_mode_nothing_is_added(self, lasx, client, container):
        result = changes.add_bleach_points(client, [{"at_um": (0.0, 0.0), "seconds": 1.0}], container=container)

        assert "set_bleach_mode" in result["message"] and container.requests == []

    def test_a_point_beyond_the_field_is_refused(self, lasx, client, container):
        lasx.live[(*ROI, "EnableBleachScan")] = True
        assert changes.add_bleach_points(client, [{"at_um": (0.0, 900.0), "seconds": 1.0}], container=container)["success"] is False

    def test_the_points_are_cleared(self, lasx, client, container):
        lasx.live[(*ROI, "EnableBleachScan")] = True
        changes.add_bleach_points(client, [{"at_um": (0.0, 0.0), "seconds": 1.0}], container=container)

        assert changes.clear_bleach_points(client, container=container)["points"] == []
