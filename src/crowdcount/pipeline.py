"""The detection loop: read frames, detect and track people, count zones, publish."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from crowdcount.annotate import draw_detections, draw_hud, draw_zones
from crowdcount.detector import Detection
from crowdcount.metrics import MetricsLogger
from crowdcount.state import SharedState
from crowdcount.video import VideoSource
from crowdcount.zones import Zone, ZoneConfigError, ZoneTracker, load_zones, zones_outside_frame

logger = logging.getLogger(__name__)

WINDOW_TITLE = "CrowdCount"


class Tracker(Protocol):
    """Anything that turns a frame into tracked detections (see ``PersonDetector``)."""

    def track(self, frame: np.ndarray) -> list[Detection]: ...


@dataclass
class PipelineOptions:
    """How the loop runs. See ``crowdcount run --help`` for user-facing descriptions."""

    stride: int = 1
    realtime: bool = True
    display: bool = False
    output: Path | None = None
    max_frames: int | None = None
    metrics_out: Path | None = None
    zones_reload_interval: float = 1.0
    trail_length: int = 30


@dataclass
class RunSummary:
    frames_read: int = 0
    frames_processed: int = 0
    elapsed_s: float = 0.0
    mean_fps: float = 0.0
    stopped_by: str = "eof"  # eof | max_frames | user | error
    zone_totals: dict[str, int] = field(default_factory=dict)


class _ZoneFileWatcher:
    """Reload zones when the file changes, keeping the last good set on errors."""

    def __init__(self, path: Path, interval: float) -> None:
        self.path = path
        self.interval = interval
        self._mtime = self._stat()
        self._next_check = time.monotonic() + interval

    def _stat(self) -> float | None:
        try:
            return self.path.stat().st_mtime
        except FileNotFoundError:
            return None

    def poll(self) -> list[Zone] | None:
        """Return new zones if the file changed since the last successful poll."""
        if self.interval <= 0 or time.monotonic() < self._next_check:
            return None
        self._next_check = time.monotonic() + self.interval
        mtime = self._stat()
        if mtime == self._mtime:
            return None
        self._mtime = mtime
        try:
            zones = load_zones(self.path)
        except ZoneConfigError as exc:
            logger.warning("Ignoring zone file change: %s", exc)
            return None
        logger.info("Reloaded %d zone(s) from %s", len(zones), self.path)
        return zones


class Pipeline:
    """Run detection on a video source and publish results to ``state``.

    Args:
        tracker: the detector (``PersonDetector`` in production, a fake in tests).
        source: an opened or unopened :class:`VideoSource`.
        zones_file: zone definitions; watched for changes while running.
        state: where live results are published for the dashboard.
        options: loop behaviour.
        info: extra status fields shown on the dashboard (model, tracker, device).
    """

    def __init__(
        self,
        tracker: Tracker,
        source: VideoSource,
        zones_file: Path,
        state: SharedState,
        options: PipelineOptions | None = None,
        info: dict[str, str] | None = None,
    ) -> None:
        self.tracker = tracker
        self.source = source
        self.zones_file = Path(zones_file)
        self.state = state
        self.options = options or PipelineOptions()
        self.info = info or {}
        self.zone_tracker = ZoneTracker(load_zones(self.zones_file))
        if not self.zone_tracker.zones:
            logger.info(
                "No zones in %s yet: counting the whole frame. Draw zones on the Zones page "
                "or with `crowdcount zones edit`; they apply without a restart.",
                self.zones_file,
            )
        self._stop = threading.Event()
        self._paused = False
        self._trails: dict[int, deque[tuple[int, int]]] = {}
        self._last_seen: dict[int, int] = {}

    def stop(self) -> None:
        """Ask the loop to finish after the current frame (thread-safe)."""
        self._stop.set()

    # ------------------------------------------------------------------ main loop

    def run(self) -> RunSummary:
        opts = self.options
        if not self.source.opened:
            self.source.open()
        src = self.source
        self._warn_zones_outside(self.zone_tracker.zones, src.width, src.height)
        self.state.begin_run(width=src.width, height=src.height, source=src.label, **self.info)
        watcher = _ZoneFileWatcher(self.zones_file, opts.zones_reload_interval)
        writer = self._open_writer(src) if opts.output else None
        metrics = (
            MetricsLogger(opts.metrics_out, [z.name for z in self.zone_tracker.active_zones])
            if opts.metrics_out
            else None
        )
        if opts.display:
            cv2.namedWindow(WINDOW_TITLE, cv2.WINDOW_NORMAL)
            cv2.resizeWindow(WINDOW_TITLE, src.width, src.height)

        summary = RunSummary()
        stride = opts.stride
        frame_interval = stride / src.fps
        fps = 0.0
        recent: deque[float] = deque(maxlen=20)  # completion times of recent frames
        frame_times: list[float] = []
        started = time.perf_counter()
        error: BaseException | None = None
        try:
            while not self._stop.is_set():
                if self._paused:
                    key = cv2.waitKey(50) & 0xFF
                    stride = self._handle_key(key, stride)
                    continue
                frame = src.read()
                if frame is None:
                    summary.stopped_by = "eof"
                    break
                summary.frames_read += 1
                if (summary.frames_read - 1) % stride:
                    continue

                if (zones := watcher.poll()) is not None:
                    self.zone_tracker.set_zones(zones)
                    self._warn_zones_outside(zones, src.width, src.height)

                t0 = time.perf_counter()
                detections = self.tracker.track(frame)
                t_infer = time.perf_counter()
                self.zone_tracker.update((d.track_id, d.center) for d in detections)
                stats = self.zone_tracker.stats()
                self._update_trails(detections, summary.frames_processed)

                annotated = frame.copy()
                draw_zones(annotated, self.zone_tracker.zones, stats)
                draw_detections(annotated, detections, self._trails)
                draw_hud(annotated, [f"People: {len(detections)}", f"{fps:4.1f} fps"])
                self.state.publish(total=len(detections), zones=stats, points=[d.center for d in detections])
                self.state.set_frames(annotated, frame)
                t_done = time.perf_counter()

                summary.frames_processed += 1
                if summary.frames_processed > 1:  # the first frame includes model warm-up
                    frame_times.append(t_done - t0)
                    recent.append(t_done)
                    if len(recent) > 1:  # throughput over the last ~20 frames
                        fps = (len(recent) - 1) / max(recent[-1] - recent[0], 1e-6)
                        self.state.set_status(fps=round(fps, 1))

                if metrics is not None:
                    self._log_metrics(metrics, summary, src, stride, detections, t0, t_infer, t_done)
                if writer is not None:
                    writer.write(annotated)
                if opts.display:
                    cv2.imshow(WINDOW_TITLE, annotated)
                    stride = self._handle_key(cv2.waitKey(1) & 0xFF, stride)
                    frame_interval = stride / src.fps
                if opts.max_frames is not None and summary.frames_processed >= opts.max_frames:
                    summary.stopped_by = "max_frames"
                    break
                if opts.realtime and not src.live:
                    remaining = frame_interval - (time.perf_counter() - t0)
                    if remaining > 0:
                        time.sleep(remaining)
            else:
                summary.stopped_by = "user"
        except KeyboardInterrupt:  # Ctrl+C or SIGTERM: clean up below, then let the CLI choose the exit code
            summary.stopped_by = "user"
            raise
        except BaseException as exc:
            summary.stopped_by = "error"
            error = exc
            self.state.set_status(state="error", message=str(exc))
            raise
        finally:
            src.release()
            if writer is not None:
                writer.release()
            if opts.display:
                cv2.destroyAllWindows()
                cv2.waitKey(1)
            if error is None:
                self.state.set_status(state="stopped")
            summary.elapsed_s = time.perf_counter() - started
            summary.mean_fps = len(frame_times) / sum(frame_times) if frame_times else 0.0
            summary.zone_totals = {n: s.total_visitors for n, s in self.zone_tracker.stats().items()}
            if metrics is not None:
                self._finish_metrics(metrics, summary)
            if error is None:
                logger.info(
                    "Detection stopped (%s): %d frames processed, %.1f fps mean processing rate",
                    summary.stopped_by,
                    summary.frames_processed,
                    summary.mean_fps,
                )
        return summary

    # ------------------------------------------------------------------ helpers

    def _update_trails(self, detections: list[Detection], frame_index: int) -> None:
        for det in detections:
            trail = self._trails.setdefault(det.track_id, deque(maxlen=self.options.trail_length))
            trail.append(det.center)
            self._last_seen[det.track_id] = frame_index
        stale = [
            tid for tid, seen in self._last_seen.items() if frame_index - seen > 2 * self.options.trail_length
        ]
        for tid in stale:
            self._trails.pop(tid, None)
            self._last_seen.pop(tid, None)

    def _handle_key(self, key: int, stride: int) -> int:
        """Keyboard controls for the preview window. Returns the (possibly new) stride."""
        if key in (ord("q"), 27):  # q or Esc
            self.stop()
        elif key in (ord("p"), ord(" ")):
            self._paused = not self._paused
            logger.info("Paused" if self._paused else "Resumed")
        elif key in (ord("+"), ord("=")):
            stride = min(stride + 1, 10)
            logger.info("Processing every %d frame(s)", stride)
        elif key in (ord("-"), ord("_")):
            stride = max(stride - 1, 1)
            logger.info("Processing every %d frame(s)", stride)
        elif key == ord("z"):
            for name, s in self.zone_tracker.stats().items():
                logger.info("Zone %-16s now=%-3d total=%d", name, s.current, s.total_visitors)
        elif key == ord("r"):
            self.state.reset_heatmap()
            logger.info("Heatmap reset")
        return stride

    def _open_writer(self, src: VideoSource) -> cv2.VideoWriter:
        assert self.options.output is not None
        path = Path(self.options.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        fps = max(1.0, src.fps / self.options.stride)
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (src.width, src.height))
        if not writer.isOpened():
            raise OSError(f"could not open {path} for writing")
        logger.info("Recording annotated video to %s", path)
        return writer

    @staticmethod
    def _warn_zones_outside(zones: list[Zone], width: int, height: int) -> None:
        outside = zones_outside_frame([z for z in zones if z.enabled], width, height)
        if outside:
            logger.warning(
                "Zone(s) %s extend beyond the %dx%d frame; were they drawn for a different camera?",
                ", ".join(outside),
                width,
                height,
            )

    def _log_metrics(
        self,
        metrics: MetricsLogger,
        summary: RunSummary,
        src: VideoSource,
        stride: int,
        detections: list[Detection],
        t0: float,
        t_infer: float,
        t_done: float,
    ) -> None:
        ts = datetime.now().astimezone().isoformat(timespec="milliseconds")
        idx = summary.frames_processed
        frame_ms = (t_done - t0) * 1000
        metrics.log_frame_timing(
            timestamp=ts,
            frame_index=idx,
            frames_read=summary.frames_read,
            source_fps=round(src.fps),
            frame_width=src.width,
            frame_height=src.height,
            speed_frame_skip=stride,
            people_count=len(detections),
            infer_ms=(t_infer - t0) * 1000,
            post_ms=(t_done - t_infer) * 1000,
            frame_ms=frame_ms,
            fps_inst=1000 / frame_ms if frame_ms > 0 else 0.0,
        )
        metrics.log_zone_counts(
            timestamp=ts,
            frame_index=idx,
            total_count=len(detections),
            zone_counts=self.zone_tracker.occupancy,
        )
        for det in detections:
            zones = self.zone_tracker.zones_at(det.center)
            metrics.log_detection(
                timestamp=ts,
                frame_index=idx,
                track_id=det.track_id,
                confidence=det.confidence,
                zones=zones,
                bbox=list(det.bbox),
                center=det.center,
            )
            metrics.note_track_seen(
                timestamp=ts,
                frame_index=idx,
                track_id=det.track_id,
                confidence=det.confidence,
                trail_len=len(self._trails.get(det.track_id, ())),
            )

    def _finish_metrics(self, metrics: MetricsLogger, summary: RunSummary) -> None:
        try:
            metrics.write_track_summaries()
            history = self.state.history(3600)
            metrics.write_history_dump({"history": history, "count": len(history)})
            metrics.write_run_summary(
                {
                    **self.info,
                    "source": self.source.label,
                    "frame_width": self.source.width,
                    "frame_height": self.source.height,
                    "frames_read": summary.frames_read,
                    "frames_processed": summary.frames_processed,
                    "mean_processing_fps": round(summary.mean_fps, 2),
                    "stopped_by": summary.stopped_by,
                    "zone_unique_tracks": summary.zone_totals,
                }
            )
        except OSError:
            logger.exception("Failed to write metrics to %s", self.options.metrics_out)
        finally:
            metrics.close()
