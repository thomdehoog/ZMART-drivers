"""The focus map, as the LAS X Navigator's: focal points with their focus, and the focus of every field of a Start
interpolated from them as LAS X's help describes it (Creating a Focus Map, its calculation)."""

import pytest
from lasx_stand_in import PlainNavigator, attach
from limits_fixtures import hermetic_mock_machine_root

from zmart_drivers.leica.stellaris import navigator
from zmart_drivers.leica.stellaris.navigator.procedures import focus_map
from zmart_drivers.leica.stellaris.navigator.procedures.clear_cache import data_container

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
def scope(lasx, tmp_path, monkeypatch):
    # The project the Navigator's autofocus searches into, as a picture's.
    monkeypatch.setattr(data_container, "experiment_for_capture", lambda: 2518)
    hermetic_mock_machine_root()
    scope = navigator.connect(output_root=str(tmp_path / "out"))
    yield scope
    scope.close()


class TestFindingTheMap:
    """As the Focus Map's "Find all by Autofocus": LCS's Navigator autofocus at each focal point (simulator, 2026-11-03)."""

    def test_each_focal_point_is_searched_by_las_x_s_navigator_autofocus_and_its_focus_kept(self, scope, lasx):
        here = scope.position()
        x, y, z = (here[a]["position"] for a in ("x", "y", "z"))
        lasx.focus_points_find = [2.0, -1.0, 0.5]

        points = scope.find_focus_map([(x, y), (x + 500.0, y), (x, y + 500.0)])

        searches = [e for e in lasx.sent if "StartNavigatorAutoFocus" in e]
        assert len(searches) == 3 and all("<Position " in e for e in searches)
        assert [p[:2] for p in points] == [(x, y), (x + 500.0, y), (x, y + 500.0)]
        # Each search starts at the focus found at the point before.
        assert [p[2] - z for p in points] == [pytest.approx(2.0), pytest.approx(1.0), pytest.approx(1.5)]

    def test_manual_operation_is_given_back_and_the_stage_put_back(self, scope, lasx):
        here = scope.position()
        x, y, z = (here[a]["position"] for a in ("x", "y", "z"))

        scope.find_focus_map([(x, y), (x + 500.0, y)])

        assert '<Microscope Command="DisableManualOperation" Disable="1"/>' in lasx.sent
        assert lasx.sent.index('<Microscope Command="DisableManualOperation" Disable="0"/>') > max(
            i for i, e in enumerate(lasx.sent) if "StartNavigatorAutoFocus" in e
        )
        back = scope.position()
        assert (back["x"]["position"], back["y"]["position"], back["z"]["position"]) == (
            pytest.approx(x), pytest.approx(y), pytest.approx(z)
        )

    def test_a_focal_point_where_no_focus_was_found_refuses_the_map_and_puts_everything_back(self, scope, lasx):
        here = scope.position()
        x, y, z = (here[a]["position"] for a in ("x", "y", "z"))
        lasx.focus_points_find = [1.0, None]

        with pytest.raises(RuntimeError, match="found no focus"):
            scope.find_focus_map([(x, y), (x + 500.0, y)])

        assert lasx.sent[-1] != '<Microscope Command="DisableManualOperation" Disable="1"/>'
        assert '<Microscope Command="DisableManualOperation" Disable="0"/>' in lasx.sent
        back = scope.position()
        assert (back["x"]["position"], back["z"]["position"]) == (pytest.approx(x), pytest.approx(z))

    def test_fewer_than_two_places_are_refused_before_anything_moves(self, scope, lasx):
        here = scope.position()
        with pytest.raises(ValueError, match="two"):
            scope.find_focus_map([(here["x"]["position"], here["y"]["position"])])
        assert not any("StartNavigatorAutoFocus" in e for e in lasx.sent)


class TestShiftingTheMap:
    def test_the_map_moves_by_the_focus_here_less_the_map_s(self, scope):
        here = scope.position()
        x, y, z = (here[a]["position"] for a in ("x", "y", "z"))
        points = [(x - 100.0, y, z + 1.0), (x + 100.0, y, z + 3.0)]

        shifted = scope.shift_focus_map(points)

        # The map gives z + 2 here; the focus is at z, so every point moves by -2.
        assert [p[2] for p in shifted] == [pytest.approx(z - 1.0), pytest.approx(z + 1.0)]
        assert [p[:2] for p in shifted] == [p[:2] for p in points]


class TestSpreadingFocusPoints:
    def test_the_points_spread_over_the_fields_and_stay_among_them(self):
        fields = [(float(i) * 100, float(j) * 100) for i in range(9) for j in range(6)]

        places = focus_map.spread(fields, 5)

        assert len(places) == 5 and len(set(places)) == 5
        assert all(0 <= px <= 800 and 0 <= py <= 500 for px, py in places)
        # Spread: no two closer than a fifth of the area's width.
        assert min(abs(complex(*a) - complex(*b)) for i, a in enumerate(places) for b in places[i + 1:]) > 160

    def test_the_same_fields_give_the_same_points(self):
        fields = [(float(i) * 100, float(j) * 100) for i in range(9) for j in range(6)]
        assert focus_map.spread(fields, 4) == focus_map.spread(fields, 4)

    @pytest.mark.parametrize("number", [0, -1, 1.5, True])
    def test_a_number_that_is_not_one_or_more_is_refused(self, number):
        with pytest.raises(ValueError, match="number"):
            focus_map.spread([(0.0, 0.0), (1.0, 1.0)], number)

    def test_more_points_than_fields_is_refused(self):
        with pytest.raises(ValueError, match="fields"):
            focus_map.spread([(0.0, 0.0), (1.0, 1.0)], 3)
