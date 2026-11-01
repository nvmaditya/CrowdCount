import numpy as np
import pytest

from crowdcount.state import DEFAULT_GLOBAL_THRESHOLD, AlertRule, SharedState, _iso
from crowdcount.zones import ZoneStats


class FakeClock:
    def __init__(self, start: float = 1_700_000_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def state(clock):
    s = SharedState(clock=clock, alert_clear_seconds=0)
    s.begin_run(width=640, height=480, source="test.mp4")
    return s


def zones(**counts):
    return {name: ZoneStats(current=c, total_visitors=c) for name, c in counts.items()}


class TestHistory:
    def test_one_bucket_per_second_keeps_the_peak(self, state, clock):
        for total in (3, 7, 5):  # three frames within the same second
            state.publish(total=total, zones=zones(A=total))
            clock.advance(0.3)
        history = state.history()
        assert len(history) == 1
        assert history[0]["total_count"] == 7
        assert history[0]["zone_counts"] == {"A": 7}

    def test_new_second_starts_a_new_bucket(self, state, clock):
        state.publish(total=1, zones={})
        clock.advance(1.0)
        state.publish(total=2, zones={})
        assert [b["total_count"] for b in state.history()] == [1, 2]

    def test_history_size_bounds_buckets(self, clock):
        s = SharedState(history_size=3, clock=clock)
        for i in range(5):
            s.publish(total=i, zones={})
            clock.advance(1.0)
        assert [b["total_count"] for b in s.history()] == [2, 3, 4]

    def test_limit_returns_most_recent(self, state, clock):
        for i in range(5):
            state.publish(total=i, zones={})
            clock.advance(1.0)
        assert [b["total_count"] for b in state.history(limit=2)] == [3, 4]

    def test_history_is_a_copy(self, state):
        state.publish(total=1, zones=zones(A=1))
        state.history()[0]["zone_counts"]["A"] = 99
        assert state.history()[0]["zone_counts"]["A"] == 1


class TestAlerts:
    def test_global_alert_fires_once_per_breach(self, state):
        fired = []
        state.add_alert_listener(fired.append)
        state.set_global_threshold(2)

        state.publish(total=3, zones={})
        state.publish(total=4, zones={})  # still breached: no new alert
        assert [a.key for a in fired] == ["global"]

        state.publish(total=1, zones={})  # clears
        state.publish(total=5, zones={})  # breaches again
        assert [a.key for a in fired] == ["global", "global"]

    def test_count_at_the_threshold_is_near_not_over(self, state):
        state.set_global_threshold(3)
        assert [a.level for a in state.publish(total=3, zones={})] == ["near"]
        assert state.alerts()["global"]["exceeded"] is False

    def test_zone_alerts(self, state):
        state.set_zone_threshold("Gate", 1)
        new = state.publish(total=2, zones=zones(Gate=2, Hall=2))
        assert [(a.key, a.kind, a.current, a.threshold) for a in new] == [("Gate", "zone", 2, 1)]
        assert state.alerts()["Gate"]["zone"] == "Gate"

    def test_removing_zone_threshold(self, state):
        state.set_zone_threshold("Gate", 1)
        state.set_zone_threshold("Gate", None)
        assert state.publish(total=0, zones=zones(Gate=5)) == []

    def test_reading_alerts_has_no_side_effects(self, state):
        fired = []
        state.add_alert_listener(fired.append)
        state.set_global_threshold(1)
        state.publish(total=2, zones={})
        for _ in range(3):
            state.alerts()
        assert len(fired) == 1

    def test_failing_listener_does_not_break_publish(self, state):
        def boom(alert):
            raise RuntimeError("disk full")

        seen = []
        state.add_alert_listener(boom)
        state.add_alert_listener(seen.append)
        state.set_global_threshold(1)
        state.publish(total=2, zones={})
        assert len(seen) == 1

    @pytest.mark.parametrize("bad", [0, -1])
    def test_invalid_thresholds(self, state, bad):
        with pytest.raises(ValueError):
            state.set_global_threshold(bad)
        with pytest.raises(ValueError):
            state.set_zone_threshold("A", bad)


class TestAlertRules:
    @pytest.mark.parametrize(
        ("limit", "pct", "warn_at"),
        [(4, 80, 4), (10, 80, 8), (3, 80, 3), (6, 80, 5), (1, 80, 1), (5, 100, 5), (100, 1, 1)],
    )
    def test_warn_at_rounds_up_within_the_limit(self, limit, pct, warn_at):
        assert AlertRule(limit, warn_percent=pct).warn_at == warn_at

    @pytest.mark.parametrize(
        "kwargs", [{"limit": 0}, {"limit": 5, "warn_percent": 0}, {"limit": 5, "warn_percent": 101}]
    )
    def test_invalid_rules(self, kwargs):
        with pytest.raises(ValueError):
            AlertRule(**kwargs)

    def test_near_fires_before_over_and_de_escalation_is_quiet(self, state):
        state.set_global_rule(AlertRule(10))

        def levels(new):
            return [a.level for a in new]

        assert state.publish(total=7, zones={}) == []
        assert levels(state.publish(total=8, zones={})) == ["near"]
        assert state.alerts()["global"]["exceeded"] is False
        assert levels(state.publish(total=11, zones={})) == ["over"]
        assert state.publish(total=9, zones={}) == []  # over -> near fires nothing
        assert state.alerts()["global"]["level"] == "near"
        assert state.publish(total=3, zones={}) == []
        assert state.alerts() == {}
        assert levels(state.publish(total=8, zones={})) == ["near"]

    def test_since_marks_when_the_current_level_started(self, state, clock):
        state.set_global_rule(AlertRule(10))
        start = clock.now
        state.publish(total=8, zones={})
        clock.advance(5)
        state.publish(total=9, zones={})
        assert state.alerts()["global"]["since"] == _iso(start)
        clock.advance(5)
        state.publish(total=11, zones={})
        assert state.alerts()["global"]["since"] == _iso(start + 10)

    def test_disabled_rule_clears_without_firing(self, state):
        state.set_global_rule(AlertRule(10))
        state.publish(total=12, zones={})
        state.set_global_rule(AlertRule(10, enabled=False))
        assert state.publish(total=20, zones={}) == []
        assert state.alerts() == {}

    def test_changing_the_limit_mid_alert_does_not_refire(self, state):
        state.set_global_rule(AlertRule(10))
        state.publish(total=12, zones={})
        state.set_global_threshold(11)
        assert state.publish(total=12, zones={}) == []
        assert state.alerts()["global"]["threshold"] == 11
        state.set_global_threshold(20)
        assert state.publish(total=12, zones={}) == []
        assert state.alerts() == {}

    def test_acknowledged_until_it_fires_again(self, state):
        fired = []
        state.add_alert_listener(fired.append)
        state.set_global_rule(AlertRule(2))
        state.publish(total=3, zones={})
        alert_id = state.alerts()["global"]["id"]
        assert fired[0].id == alert_id
        assert state.acknowledge_alert(alert_id) is True
        assert state.alerts()["global"]["acknowledged"] is True
        assert state.acknowledge_alert("nope") is False
        state.publish(total=0, zones={})
        state.publish(total=3, zones={})
        assert state.alerts()["global"]["acknowledged"] is False
        assert state.alerts()["global"]["id"] != alert_id

    def test_setting_a_threshold_keeps_rule_options(self, state):
        state.set_zone_rule("A", AlertRule(5, warn_percent=50, banner=False))
        state.set_zone_threshold("A", 7)
        assert state.rules()["zones"]["A"] == {
            "limit": 7,
            "warn_percent": 50,
            "warn_at": 4,
            "enabled": True,
            "banner": False,
        }

    def test_rename_zone_rule(self, state):
        state.set_zone_rule("A", AlertRule(5, banner=False))
        state.rename_zone_rule("A", "B")
        assert list(state.rules()["zones"]) == ["B"]
        assert state.rules()["zones"]["B"]["banner"] is False
        state.rename_zone_rule("B", None)
        assert state.rules()["zones"] == {}

    def test_summary_includes_rules(self, state):
        assert state.summary()["rules"]["global"]["limit"] == DEFAULT_GLOBAL_THRESHOLD


class TestFlapProtection:
    @pytest.fixture
    def slow_state(self, clock):
        s = SharedState(clock=clock, alert_clear_seconds=5)
        s.begin_run(width=640, height=480, source="test.mp4")
        s.set_global_rule(AlertRule(10))
        return s

    def test_flapping_counts_fire_once(self, slow_state, clock):
        fired = []
        slow_state.add_alert_listener(fired.append)
        for i in range(30):
            slow_state.publish(total=8 if i % 2 == 0 else 7, zones={})
            clock.advance(0.1)
        assert [a.level for a in fired] == ["near"]

    def test_alerts_drop_only_after_the_count_stays_low(self, slow_state, clock):
        slow_state.publish(total=12, zones={})
        clock.advance(1)
        slow_state.publish(total=9, zones={})
        assert slow_state.alerts()["global"]["level"] == "over"  # not low for 5 s yet
        clock.advance(2)
        slow_state.publish(total=12, zones={})  # back over: the pending drop is cancelled
        clock.advance(1)
        slow_state.publish(total=3, zones={})
        clock.advance(4)
        slow_state.publish(total=3, zones={})
        assert slow_state.alerts()["global"]["level"] == "over"  # only 4 s low
        clock.advance(1)
        slow_state.publish(total=3, zones={})
        assert slow_state.alerts() == {}

    def test_disabling_a_rule_clears_at_once(self, slow_state):
        slow_state.publish(total=12, zones={})
        slow_state.set_global_rule(AlertRule(10, enabled=False))
        slow_state.publish(total=12, zones={})
        assert slow_state.alerts() == {}

    def test_raising_the_limit_clears_at_once(self, slow_state):
        slow_state.set_global_rule(AlertRule(2))
        slow_state.publish(total=6, zones={})
        assert slow_state.alerts()["global"]["level"] == "over"
        slow_state.set_global_threshold(50)
        slow_state.publish(total=6, zones={})
        assert slow_state.alerts() == {}

    def test_raising_the_limit_drops_a_level_at_once(self, slow_state):
        fired = []
        slow_state.add_alert_listener(fired.append)
        slow_state.publish(total=12, zones={})
        slow_state.set_global_threshold(14)  # warn_at 12: 12 is near
        slow_state.publish(total=12, zones={})
        assert slow_state.alerts()["global"]["level"] == "near"
        assert slow_state.alerts()["global"]["threshold"] == 14
        assert [a.level for a in fired] == ["over"]  # dropping a level records nothing


class TestSnapshot:
    def test_summary_reports_counts_peak_and_status(self, state, clock):
        state.publish(total=4, zones=zones(A=1))
        clock.advance(2)
        state.publish(total=2, zones=zones(A=0))
        summary = state.summary()
        assert summary["total_count"] == 2
        assert summary["peak_count"] == 4
        assert summary["zones"]["A"] == {"current": 0, "total_visitors": 0, "threshold": None}
        assert summary["status"]["state"] == "running"
        assert summary["status"]["frame_width"] == 640
        assert summary["detection_running"] is True

    def test_begin_run_clears_counts_but_keeps_thresholds(self, state):
        state.set_global_threshold(9)
        state.set_zone_threshold("A", 2)
        state.publish(total=3, zones=zones(A=3))
        state.begin_run(width=100, height=100)
        assert state.total_count == 0
        assert state.history() == []
        assert state.get_global_threshold() == 9
        assert state.get_zone_thresholds() == {"A": 2}

    def test_reset_marks_detector_offline(self, state):
        state.reset()
        assert state.status().state == "offline"
        assert state.detection_running is False


class TestImages:
    def test_no_images_before_first_frame(self, state):
        assert state.frame_jpeg() is None
        assert state.snapshot_jpeg() is None
        assert state.heatmap_jpeg() is None

    def test_frame_jpeg_is_cached_per_frame(self, state):
        frame = np.zeros((48, 64, 3), np.uint8)
        state.set_frames(frame, frame)
        first = state.frame_jpeg()
        assert first is not None and first[1][:2] == b"\xff\xd8"
        assert state.frame_jpeg() is first
        state.set_frames(frame.copy(), frame)
        assert state.frame_jpeg()[0] == first[0] + 1

    def test_heatmap_renders_after_detections(self, state):
        state.set_frames(np.zeros((480, 640, 3), np.uint8), np.zeros((480, 640, 3), np.uint8))
        state.publish(total=1, zones={}, points=[(320, 240)])
        jpeg = state.heatmap_jpeg()
        assert jpeg is not None and jpeg[:2] == b"\xff\xd8"

    def test_heatmap_reset(self, state):
        state.publish(total=1, zones={}, points=[(10, 10)])
        state.reset_heatmap()
        assert state._heat is not None and float(state._heat.max()) == 0.0


def test_heatmap_accumulates_rather_than_marking_coverage(state):
    # Regression: v1 used cv2.circle, which overwrote pixels with 1.0, so the "heatmap" was a
    # binary coverage mask. Repeat visits must make a spot hotter.
    state.publish(total=1, zones={}, points=[(100, 100)])
    once = float(state._heat.max())
    for _ in range(9):
        state.publish(total=1, zones={}, points=[(100, 100)])
    assert float(state._heat.max()) == pytest.approx(10 * once)


def test_heatmap_points_near_the_edge(state):
    state.publish(total=2, zones={}, points=[(0, 0), (639, 479)])
    assert float(state._heat.max()) > 0
