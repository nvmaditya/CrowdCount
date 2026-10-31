import csv
import json
import os
import time

import pytest

from crowdcount.detector import Detection
from crowdcount.pipeline import Pipeline, PipelineOptions, _ZoneFileWatcher
from crowdcount.state import SharedState
from crowdcount.video import VideoSource, VideoSourceError, describe_source, is_stream
from crowdcount.zones import Zone, save_zones


class FakeTracker:
    """Reports one person following the white square in the synthetic clip."""

    def __init__(self):
        self.calls = 0

    def track(self, frame):
        self.calls += 1
        cols = frame[60].max(axis=1).nonzero()[0]
        if not len(cols):
            return []
        x1, x2 = int(cols.min()), int(cols.max())
        return [Detection(track_id=1, bbox=(x1, 40, x2, 80), confidence=0.9)]


@pytest.fixture
def zones_file(tmp_path):
    path = tmp_path / "zones.json"
    save_zones(
        path,
        [
            Zone("left", [(0, 0), (80, 0), (80, 119), (0, 119)]),
            Zone("right", [(80, 0), (159, 0), (159, 119), (80, 119)]),
        ],
    )
    return path


def run(video, zones_file, state=None, **opts):
    state = state or SharedState()
    tracker = FakeTracker()
    options = PipelineOptions(realtime=False, **opts)
    summary = Pipeline(
        tracker, VideoSource(str(video)), zones_file, state, options, info={"model": "fake"}
    ).run()
    return summary, state, tracker


def test_processes_every_frame_and_publishes(synthetic_video, zones_file):
    summary, state, tracker = run(synthetic_video, zones_file)
    assert summary.frames_read == 20
    assert summary.frames_processed == 20
    assert summary.stopped_by == "eof"
    assert tracker.calls == 20
    zones = state.zone_stats()
    # The square starts in "left" and ends in "right": one unique track visited both.
    assert zones["left"]["total_visitors"] == 1
    assert zones["right"]["total_visitors"] == 1
    assert zones["right"]["current"] == 1 and zones["left"]["current"] == 0
    assert state.status().state == "stopped"
    assert state.status().model == "fake"
    assert state.frame_jpeg() is not None


def test_stride_and_max_frames(synthetic_video, zones_file):
    summary, _, tracker = run(synthetic_video, zones_file, stride=3, max_frames=4)
    assert tracker.calls == 4
    assert summary.stopped_by == "max_frames"
    assert summary.frames_read == 10  # frames 1, 4, 7, 10 were processed


def test_loop_keeps_reading_past_the_end(synthetic_video, zones_file):
    state = SharedState()
    pipeline = Pipeline(
        FakeTracker(),
        VideoSource(str(synthetic_video), loop=True),
        zones_file,
        state,
        PipelineOptions(realtime=False, max_frames=45),
    )
    summary = pipeline.run()
    assert summary.frames_processed == 45


def test_metrics_and_recording(synthetic_video, zones_file, tmp_path):
    out = tmp_path / "metrics"
    video_out = tmp_path / "annotated.mp4"
    run(synthetic_video, zones_file, metrics_out=out, output=video_out, max_frames=5)
    assert video_out.stat().st_size > 0
    with (out / "frame_timing.csv").open() as fh:
        assert len(list(csv.DictReader(fh))) == 5
    with (out / "zone_timeseries.csv").open() as fh:
        assert csv.DictReader(fh).fieldnames == ["timestamp", "frame_index", "total_count", "left", "right"]
    summary = json.loads((out / "run_summary.json").read_text())
    assert summary["frames_processed"] == 5
    assert summary["zone_unique_tracks"] == {"left": 1, "right": 0}


def test_tracker_errors_mark_status(synthetic_video, zones_file):
    class Broken:
        def track(self, frame):
            raise RuntimeError("GPU fell over")

    state = SharedState()
    with pytest.raises(RuntimeError):
        Pipeline(
            Broken(), VideoSource(str(synthetic_video)), zones_file, state, PipelineOptions(realtime=False)
        ).run()
    assert state.status().state == "error"
    assert "GPU fell over" in state.status().message


class TestZoneFileWatcher:
    def test_reloads_on_change_and_ignores_bad_edits(self, zones_file):
        watcher = _ZoneFileWatcher(zones_file, interval=0.001)
        time.sleep(0.002)
        assert watcher.poll() is None  # unchanged

        save_zones(zones_file, [Zone("new", [(0, 0), (5, 0), (5, 5)])])
        os.utime(zones_file, (time.time() + 5, time.time() + 5))
        time.sleep(0.002)
        assert [z.name for z in watcher.poll()] == ["new"]

        zones_file.write_text("{ not json")
        os.utime(zones_file, (time.time() + 10, time.time() + 10))
        time.sleep(0.002)
        assert watcher.poll() is None  # broken edit is ignored, last good zones stay

    def test_disabled_when_interval_is_zero(self, zones_file):
        assert _ZoneFileWatcher(zones_file, interval=0).poll() is None


class TestVideoSource:
    def test_missing_file(self, tmp_path):
        with pytest.raises(VideoSourceError, match="not found"):
            VideoSource(str(tmp_path / "missing.mp4")).open()

    def test_undecodable_file(self, tmp_path):
        bogus = tmp_path / "notavideo.mp4"
        bogus.write_bytes(b"hello")
        with pytest.raises(VideoSourceError):
            VideoSource(str(bogus)).open()

    def test_reports_size(self, synthetic_video):
        with VideoSource(str(synthetic_video)) as src:
            assert (src.width, src.height) == (160, 120)
            assert src.fps == pytest.approx(10)

    def test_source_helpers(self):
        assert is_stream("rtsp://cam/stream") and is_stream("https://cam/video.mjpg")
        assert not is_stream("clip.mp4")
        assert describe_source("rtsp://user:secret@10.0.0.5:554/live") == "rtsp://10.0.0.5/live"
        assert describe_source("/videos/lobby.mp4") == "lobby.mp4"

    @pytest.mark.parametrize("source", ["", "   "])
    def test_empty_source(self, source):
        with pytest.raises(VideoSourceError, match="no video source"):
            VideoSource(source)

    def test_only_regular_files_are_opened(self, tmp_path):
        # Device nodes and directories are not video files (OpenCV would open /dev/video* as a device).
        for source in ("0", str(tmp_path)):
            with pytest.raises(VideoSourceError, match="video file not found"):
                VideoSource(source).open()
