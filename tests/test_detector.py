"""Detector helpers that don't need model weights."""

import pytest

from crowdcount.detector import (
    Detection,
    DetectionFilter,
    ModelLoadError,
    resolve_model_path,
    resolve_tracker,
)


def test_detection_geometry():
    det = Detection(track_id=3, bbox=(10, 20, 30, 60), confidence=0.8)
    assert det.center == (20, 40)
    assert det.area == 800


class TestDetectionFilter:
    f = DetectionFilter(confidence=0.5, min_box_area=100, min_aspect_ratio=0.2, max_aspect_ratio=2.0)

    def test_accepts_a_person_shaped_box(self):
        assert self.f.accepts((0, 0, 20, 50), 0.9)

    def test_rejects_low_confidence(self):
        assert not self.f.accepts((0, 0, 20, 50), 0.49)

    def test_rejects_small_boxes(self):
        assert not self.f.accepts((0, 0, 5, 10), 0.9)

    @pytest.mark.parametrize("bbox", [(0, 0, 100, 20), (0, 0, 4, 100)])
    def test_rejects_implausible_shapes(self, bbox):
        assert not self.f.accepts(bbox, 0.9)

    def test_rejects_degenerate_boxes(self):
        assert not self.f.accepts((10, 10, 10, 50), 0.9)


class TestResolveTracker:
    @pytest.mark.parametrize(
        ("name", "expected"), [("bytetrack", "bytetrack.yaml"), ("BoTSORT", "botsort.yaml")]
    )
    def test_short_names(self, name, expected):
        assert resolve_tracker(name) == expected

    def test_custom_yaml(self, tmp_path):
        cfg = tmp_path / "mytracker.yaml"
        cfg.write_text("tracker_type: bytetrack\n")
        assert resolve_tracker(str(cfg)) == str(cfg)

    def test_missing_yaml(self, tmp_path):
        with pytest.raises(ValueError, match="not found"):
            resolve_tracker(str(tmp_path / "nope.yaml"))

    def test_unknown(self):
        with pytest.raises(ValueError, match="unknown tracker"):
            resolve_tracker("sort")


class TestResolveModelPath:
    def test_stock_names_go_to_the_cache(self, tmp_path):
        assert resolve_model_path("yolov8n.pt", tmp_path) == str(tmp_path / "yolov8n.pt")

    def test_existing_file_is_used_as_is(self, tmp_path):
        weights = tmp_path / "custom.pt"
        weights.write_bytes(b"x")
        assert resolve_model_path(str(weights), tmp_path / "cache") == str(weights)

    def test_missing_custom_file(self, tmp_path):
        with pytest.raises(ModelLoadError, match="not found"):
            resolve_model_path(str(tmp_path / "custom.pt"), tmp_path)
