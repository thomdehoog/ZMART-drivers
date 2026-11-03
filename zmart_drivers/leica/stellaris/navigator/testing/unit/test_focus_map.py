"""The focus map, as the LAS X Navigator's: focal points with their focus, and the focus of every field of a Start
interpolated from them as LAS X's help describes it (Creating a Focus Map, its calculation)."""

import pytest
from lasx_stand_in import PlainNavigator, attach
from limits_fixtures import hermetic_mock_machine_root

from zmart_drivers.leica.stellaris import navigator
from zmart_drivers.leica.stellaris.navigator.procedures import focus_map

SQUARE = [(0.0, 0.0, 10.0), (100.0, 0.0, 20.0), (100.0, 100.0, 40.0), (0.0, 100.0, 30.0)]


class TestTheMesh:
    def test_four_points_make_two_triangles(self):
        assert len(focus_map.triangles(SQUARE)) == 2

    def test_the_triangles_hold_no_other_point_in_their_circle(self):
        points = [(0, 0, 0), (100, 0, 0), (50, 10, 0), (50, 90, 0), (0, 100, 0), (100, 100, 0), (50, 50, 0)]
        for a, b, c in focus_map.triangles(points):
            for i, p in enumerate(points):
                if i not in (a, b, c):
                    assert not focus_map.in_circle(points[a], points[b], points[c], p)

    def test_points_in_a_line_make_no_triangle(self):
        assert focus_map.triangles([(0, 0, 1), (50, 0, 2), (100, 0, 3)]) == []


class TestTheFocusOfAField:
    def test_a_focal_point_keeps_its_own_focus(self):
        for x, y, z in SQUARE:
            assert focus_map.focus_at(SQUARE, x, y) == pytest.approx(z)

    def test_inside_a_triangle_the_focus_is_linear_between_its_corners(self):
        # On the plane through (0,0,10), (100,0,20), (100,100,40) at (75, 25): 10 + 0.1*75 + 0.2*25.
        assert focus_map.focus_at(SQUARE[:3], 75.0, 25.0) == pytest.approx(10 + 0.1 * 75 + 0.2 * 25)

    def test_outside_beside_an_edge_the_focus_is_that_of_the_edge_s_foot_point(self):
        # Below the edge (0,0)-(100,0): the foot point is (40, 0), z 10 + 0.1*40.
        assert focus_map.focus_at(SQUARE, 40.0, -500.0) == pytest.approx(14.0)

    def test_outside_beyond_every_edge_the_nearest_point_s_focus_is_taken(self):
        assert focus_map.focus_at(SQUARE, 300.0, 300.0) == pytest.approx(40.0)
        assert focus_map.focus_at(SQUARE, -50.0, -80.0) == pytest.approx(10.0)

    def test_two_points_are_enough_and_nothing_is_extrapolated(self):
        two = [(0.0, 0.0, 10.0), (100.0, 0.0, 30.0)]
        assert focus_map.focus_at(two, 50.0, 70.0) == pytest.approx(20.0)
        assert focus_map.focus_at(two, 500.0, 0.0) == pytest.approx(30.0)

    @pytest.mark.parametrize("points", [[], [(0.0, 0.0, 1.0)], [(0.0, 0.0, 1.0), (0.0, 0.0, 2.0)]])
    def test_fewer_than_two_places_are_refused(self, points):
        with pytest.raises(ValueError, match="two"):
            focus_map.focus_at(points, 0.0, 0.0)

    def test_a_point_without_its_focus_is_refused(self):
        with pytest.raises(ValueError, match="focal point"):
            focus_map.focus_at([(0.0, 0.0), (1.0, 1.0)], 0.0, 0.0)


@pytest.fixture
def lasx(monkeypatch):
    return attach(monkeypatch, PlainNavigator(z_wide_um=100.0))


@pytest.fixture
def scope(lasx, tmp_path):
    hermetic_mock_machine_root()
    scope = navigator.connect(output_root=str(tmp_path / "out"))
    yield scope
    scope.close()


class TestFindingTheMapAndUsingIt:
    def test_every_focal_point_is_visited_its_focus_found_and_the_stage_put_back(self, scope, lasx, monkeypatch):
        here = scope.position()
        x, y, z = (here[a]["position"] for a in ("x", "y", "z"))
        found = []

        def autofocus(project=None):
            moved = scope.position()
            found.append((round(moved["x"]["position"]), round(moved["y"]["position"])))
            return {"found": True}

        monkeypatch.setattr(scope, "autofocus", autofocus)

        points = scope.find_focus_map([(x, y), (x + 500.0, y), (x, y + 500.0)])

        assert found == [(round(x), round(y)), (round(x + 500), round(y)), (round(x), round(y + 500))]
        assert [p[:2] for p in points] == [(pytest.approx(x), pytest.approx(y)), (pytest.approx(x + 500), pytest.approx(y)),
                                           (pytest.approx(x), pytest.approx(y + 500))]
        assert all(p[2] == pytest.approx(z) for p in points)
        back = scope.position()
        assert (back["x"]["position"], back["y"]["position"]) == (pytest.approx(x), pytest.approx(y))

    def test_a_focal_point_where_the_autofocus_found_nothing_is_refused(self, scope, monkeypatch):
        here = scope.position()
        x, y = here["x"]["position"], here["y"]["position"]
        monkeypatch.setattr(scope, "autofocus", lambda project=None: {"found": False})

        with pytest.raises(RuntimeError, match="found no focus"):
            scope.find_focus_map([(x, y), (x + 500.0, y)])
