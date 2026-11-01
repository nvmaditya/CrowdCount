import json

import pytest

from crowdcount.zones import (
    Zone,
    ZoneConfigError,
    ZoneTracker,
    load_zones,
    parse_zones,
    point_in_polygon,
    polygon_area,
    save_zones,
    zones_outside_frame,
)

SQUARE = [(0, 0), (10, 0), (10, 10), (0, 10)]
# A "U" shape: the notch between x=4..6 above y=4 is outside the polygon.
CONCAVE = [(0, 0), (10, 0), (10, 10), (6, 10), (6, 4), (4, 4), (4, 10), (0, 10)]


class TestPointInPolygon:
    @pytest.mark.parametrize("point", [(5, 5), (0.5, 9.5), (9.99, 0.01)])
    def test_inside(self, point):
        assert point_in_polygon(point, SQUARE)

    @pytest.mark.parametrize("point", [(-1, 5), (11, 5), (5, -0.1), (5, 10.1), (100, 100)])
    def test_outside(self, point):
        assert not point_in_polygon(point, SQUARE)

    @pytest.mark.parametrize("point", [(0, 5), (10, 5), (5, 0), (5, 10)])
    def test_edges_are_inside(self, point):
        assert point_in_polygon(point, SQUARE)

    @pytest.mark.parametrize("point", SQUARE)
    def test_vertices_are_inside(self, point):
        assert point_in_polygon(point, SQUARE)

    def test_concave_notch_is_outside(self):
        assert not point_in_polygon((5, 7), CONCAVE)

    @pytest.mark.parametrize("point", [(2, 7), (8, 7), (5, 2)])
    def test_concave_arms_are_inside(self, point):
        assert point_in_polygon(point, CONCAVE)

    def test_ray_through_vertex_does_not_double_count(self):
        # Horizontal ray from (-5, 5) passes exactly through the diamond's left vertex.
        diamond = [(0, 5), (5, 0), (10, 5), (5, 10)]
        assert point_in_polygon((5, 5), diamond)
        assert not point_in_polygon((-5, 5), diamond)

    def test_degenerate_polygon(self):
        assert not point_in_polygon((0, 0), [(0, 0), (1, 1)])

    def test_matches_opencv(self):
        cv2 = pytest.importorskip("cv2")
        np = pytest.importorskip("numpy")
        contour = np.array(CONCAVE, dtype=np.int32)
        for x in range(-2, 13):
            for y in range(-2, 13):
                expected = cv2.pointPolygonTest(contour, (float(x), float(y)), False) >= 0
                assert point_in_polygon((x, y), CONCAVE) == expected, (x, y)


def test_polygon_area():
    assert polygon_area(SQUARE) == 100
    assert polygon_area(list(reversed(SQUARE))) == 100


class TestZoneValidation:
    def test_minimal_zone_gets_defaults(self):
        zone = Zone.from_dict({"name": " Lobby ", "points": [[0, 0], [5, 0], [5, 5]]})
        assert zone.name == "Lobby"
        assert zone.enabled is True
        assert len(zone.color) == 3

    @pytest.mark.parametrize(
        ("payload", "message"),
        [
            ({"points": [[0, 0], [1, 0], [1, 1]]}, "name"),
            ({"name": "", "points": [[0, 0], [1, 0], [1, 1]]}, "name"),
            ({"name": "A", "points": [[0, 0], [1, 0]]}, "at least 3"),
            ({"name": "A", "points": [[0, 0], [1, 0], "x"]}, "invalid point"),
            ({"name": "A", "points": [[0, 0], [1, 0], [1, True]]}, "invalid point"),
            ({"name": "A", "points": [[0, 0], [1, 0], [1, 1]], "color": [300, 0, 0]}, "color"),
            ({"name": "A", "points": [[0, 0], [1, 0], [1, 1]], "enabled": "yes"}, "enabled"),
        ],
    )
    def test_rejects_bad_zone(self, payload, message):
        with pytest.raises(ZoneConfigError, match=message):
            Zone.from_dict(payload)

    def test_duplicate_names_rejected(self):
        zone = {"name": "A", "points": [[0, 0], [1, 0], [1, 1]]}
        with pytest.raises(ZoneConfigError, match="duplicate"):
            parse_zones({"zones": [zone, zone]})

    def test_bare_list_accepted(self):
        zones = parse_zones([{"name": "A", "points": [[0, 0], [1, 0], [1, 1]]}])
        assert [z.name for z in zones] == ["A"]


class TestZoneFiles:
    def test_round_trip(self, tmp_path):
        path = tmp_path / "zones.json"
        zones = [Zone("A", [(0, 0), (4, 0), (4, 4)], (1, 2, 3), enabled=False)]
        save_zones(path, zones)
        assert load_zones(path) == zones
        assert json.loads(path.read_text())["zones"][0]["color"] == [1, 2, 3]

    def test_missing_file_means_no_zones(self, tmp_path):
        assert load_zones(tmp_path / "nope.json") == []

    def test_invalid_json_reports_location(self, tmp_path):
        path = tmp_path / "zones.json"
        path.write_text('{"zones": [\n  {"name": "A",,}\n]}')
        with pytest.raises(ZoneConfigError, match="line 2"):
            load_zones(path)

    def test_save_leaves_no_temp_files(self, tmp_path):
        save_zones(tmp_path / "zones.json", [])
        assert [p.name for p in tmp_path.iterdir()] == ["zones.json"]


def test_zones_outside_frame():
    inside = Zone("in", [(0, 0), (99, 0), (99, 49)])
    outside = Zone("out", [(0, 0), (150, 0), (150, 40)])
    assert zones_outside_frame([inside, outside], width=100, height=50) == ["out"]


class TestZoneTracker:
    def make(self):
        left = Zone("left", [(0, 0), (10, 0), (10, 10), (0, 10)])
        right = Zone("right", [(5, 0), (20, 0), (20, 10), (5, 10)])  # overlaps left at x 5..10
        off = Zone("off", [(0, 0), (20, 0), (20, 10)], enabled=False)
        return ZoneTracker([left, right, off])

    def test_empty_zones_are_reported_with_zero(self):
        tracker = self.make()
        tracker.update([])
        stats = tracker.stats()
        assert list(stats) == ["left", "right"]
        assert all(s.current == 0 and s.total_visitors == 0 for s in stats.values())

    def test_disabled_zones_are_ignored(self):
        tracker = self.make()
        membership = tracker.update([(1, (15, 5))])
        assert membership == {1: ["right"]}
        assert "off" not in tracker.stats()

    def test_overlapping_zones_count_person_in_both(self):
        tracker = self.make()
        membership = tracker.update([(7, (7, 5))])
        assert membership[7] == ["left", "right"]
        assert tracker.occupancy == {"left": 1, "right": 1}

    def test_occupancy_resets_every_frame(self):
        tracker = self.make()
        tracker.update([(1, (2, 2)), (2, (3, 3))])
        assert tracker.occupancy["left"] == 2
        tracker.update([(1, (15, 5))])
        assert tracker.occupancy == {"left": 0, "right": 1}

    def test_same_track_id_is_one_visitor(self):
        tracker = self.make()
        for _ in range(5):
            tracker.update([(1, (2, 2))])
        tracker.update([])
        tracker.update([(1, (2, 2))])  # leaves and comes back with the same ID
        assert tracker.stats()["left"].total_visitors == 1

    def test_new_track_id_is_a_new_visitor(self):
        tracker = self.make()
        tracker.update([(1, (2, 2))])
        tracker.update([(2, (2, 2))])  # e.g. the tracker lost and re-acquired the person
        assert tracker.stats()["left"].total_visitors == 2

    def test_set_zones_keeps_history_for_surviving_zones(self):
        tracker = self.make()
        tracker.update([(1, (2, 2)), (2, (15, 5))])
        tracker.set_zones([Zone("left", [(0, 0), (10, 0), (10, 10)])])
        stats = tracker.stats()
        assert list(stats) == ["left"]
        assert stats["left"].total_visitors == 1

    def test_reset_visitors(self):
        tracker = self.make()
        tracker.update([(1, (2, 2))])
        tracker.reset_visitors()
        assert tracker.stats()["left"].total_visitors == 0
        assert tracker.stats()["left"].current == 1
