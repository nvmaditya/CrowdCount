"""Thread-safe live state shared by the detection pipeline and the web server.

The pipeline *publishes* one snapshot per processed frame; the server only *reads*.
Everything here is in-process memory: run the detector and the server in the same
process (``crowdcount run``) for the dashboard to see live data.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from typing import Any

import cv2
import numpy as np

from crowdcount.zones import ZoneStats

logger = logging.getLogger(__name__)

DEFAULT_GLOBAL_THRESHOLD = 50


def _iso(ts: float) -> str:
    """Local, timezone-aware ISO-8601 timestamp (parses unambiguously in browsers)."""
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


@dataclass(frozen=True)
class Alert:
    """An active threshold breach. ``key`` is ``"global"`` or the zone name."""

    key: str
    kind: str  # "global" | "zone"
    threshold: int
    current: int
    zone: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.kind,
            "zone": self.zone,
            "threshold": self.threshold,
            "current": self.current,
            "exceeded": True,
        }


AlertListener = Callable[[Alert], None]


@dataclass(frozen=True)
class DetectorStatus:
    """What the dashboard shows about the detector process."""

    state: str = "offline"  # offline | starting | running | stopped | error
    source: str | None = None
    model: str | None = None
    tracker: str | None = None
    device: str | None = None
    fps: float = 0.0
    frame_width: int = 0
    frame_height: int = 0
    started_at: str | None = None
    message: str | None = None


@dataclass
class _Snapshot:
    total: int = 0
    zones: dict[str, ZoneStats] = field(default_factory=dict)
    points: list[tuple[int, int]] = field(default_factory=list)
    last_update: float | None = None
    peak_total: int = 0
    peak_at: float | None = None


class SharedState:
    """Counts, history, heatmap, alert thresholds and latest frames.

    Args:
        history_size: number of one-second history buckets to keep (default: one hour).
        heatmap_max_side: the heatmap accumulator is downscaled so its longer side is at
            most this many pixels, bounding memory and render cost for large frames.
        clock: time source, injectable for tests.
    """

    def __init__(
        self,
        *,
        history_size: int = 3600,
        heatmap_max_side: int = 640,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._lock = threading.RLock()
        self._clock = clock
        self._history_size = history_size
        self._heatmap_max_side = heatmap_max_side
        self._listeners: list[AlertListener] = []
        self._global_threshold = DEFAULT_GLOBAL_THRESHOLD
        self._zone_thresholds: dict[str, int] = {}
        self._reset_run_data()
        self._status = DetectorStatus()

    # ------------------------------------------------------------------ lifecycle

    def _reset_run_data(self) -> None:
        self._snap = _Snapshot()
        self._history: deque[dict[str, Any]] = deque(maxlen=self._history_size)
        self._bucket_second: int | None = None
        self._active_alerts: dict[str, Alert] = {}
        self._frame_size: tuple[int, int] = (0, 0)  # (width, height)
        self._heat: np.ndarray | None = None
        self._heat_scale = 1.0
        self._heat_since: float | None = None
        self._annotated: np.ndarray | None = None
        self._raw: np.ndarray | None = None
        self._frame_seq = 0
        self._jpeg_cache: tuple[int, bytes] | None = None

    def reset(self) -> None:
        """Clear counts, history, heatmap, frames and status. Thresholds are kept."""
        with self._lock:
            self._reset_run_data()
            self._status = DetectorStatus()

    def begin_run(self, *, width: int, height: int, **status: Any) -> None:
        """Start a fresh detection run on a ``width`` x ``height`` source."""
        with self._lock:
            self._reset_run_data()
            self._frame_size = (width, height)
            self._status = DetectorStatus(
                state="running",
                frame_width=width,
                frame_height=height,
                started_at=_iso(self._clock()),
                **status,
            )

    def set_status(self, **fields: Any) -> None:
        """Update detector status fields (``state``, ``fps``, ``message``...)."""
        with self._lock:
            self._status = replace(self._status, **fields)

    # ------------------------------------------------------------------ publishing

    def publish(
        self,
        *,
        total: int,
        zones: Mapping[str, ZoneStats],
        points: Sequence[tuple[int, int]] = (),
        timestamp: float | None = None,
    ) -> list[Alert]:
        """Record one processed frame. Returns alerts that became active on this frame.

        Registered alert listeners are called for each newly active alert, outside
        the lock; a failing listener is logged and never interrupts detection.
        """
        now = self._clock() if timestamp is None else timestamp
        with self._lock:
            snap = self._snap
            snap.total = int(total)
            snap.zones = dict(zones)
            snap.points = [(int(x), int(y)) for x, y in points]
            snap.last_update = now
            if snap.total > snap.peak_total or snap.peak_at is None:
                snap.peak_total, snap.peak_at = snap.total, now
            self._accumulate_heat(snap.points, now)
            self._record_history(now)
            new_alerts = self._evaluate_alerts()
            listeners = list(self._listeners)

        for alert in new_alerts:
            for listener in listeners:
                try:
                    listener(alert)
                except Exception:
                    logger.exception("Alert listener %r failed", listener)
        return new_alerts

    def _record_history(self, now: float) -> None:
        """Keep one bucket per wall-clock second holding the peak counts in that second."""
        second = int(now)
        zone_counts = {name: s.current for name, s in self._snap.zones.items()}
        if self._bucket_second == second and self._history:
            bucket = self._history[-1]
            bucket["total_count"] = max(bucket["total_count"], self._snap.total)
            for name, count in zone_counts.items():
                bucket["zone_counts"][name] = max(bucket["zone_counts"].get(name, 0), count)
        else:
            self._bucket_second = second
            self._history.append(
                {"timestamp": _iso(second), "total_count": self._snap.total, "zone_counts": zone_counts}
            )

    def _evaluate_alerts(self) -> list[Alert]:
        active: dict[str, Alert] = {}
        if self._snap.total > self._global_threshold:
            active["global"] = Alert("global", "global", self._global_threshold, self._snap.total)
        for name, stats in self._snap.zones.items():
            threshold = self._zone_thresholds.get(name)
            if threshold and stats.current > threshold:
                active[name] = Alert(name, "zone", threshold, stats.current, zone=name)
        new = [a for key, a in active.items() if key not in self._active_alerts]
        self._active_alerts = active
        return new

    def _accumulate_heat(self, points: Sequence[tuple[int, int]], now: float) -> None:
        width, height = self._frame_size
        if not points or width <= 0 or height <= 0:
            return
        if self._heat is None:
            scale = min(1.0, self._heatmap_max_side / max(width, height))
            self._heat_scale = scale
            self._heat = np.zeros((max(1, round(height * scale)), max(1, round(width * scale))), np.float32)
            self._heat_since = now
        radius = max(3, round(0.035 * max(self._heat.shape)))
        for x, y in points:
            cx, cy = round(x * self._heat_scale), round(y * self._heat_scale)
            cv2.circle(self._heat, (cx, cy), radius, 1.0, -1)

    def set_frames(self, annotated: np.ndarray, raw: np.ndarray | None = None) -> None:
        """Store the latest annotated frame (for the live stream) and raw frame (for snapshots)."""
        with self._lock:
            self._annotated = annotated
            self._raw = raw
            self._frame_seq += 1

    # ------------------------------------------------------------------ thresholds & listeners

    def add_alert_listener(self, listener: AlertListener) -> None:
        with self._lock:
            self._listeners.append(listener)

    def remove_alert_listener(self, listener: AlertListener) -> None:
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def set_global_threshold(self, threshold: int) -> None:
        if threshold < 1:
            raise ValueError("threshold must be >= 1")
        with self._lock:
            self._global_threshold = int(threshold)

    def get_global_threshold(self) -> int:
        with self._lock:
            return self._global_threshold

    def set_zone_threshold(self, zone: str, threshold: int | None) -> None:
        """Set a zone threshold; ``None`` removes it."""
        with self._lock:
            if threshold is None:
                self._zone_thresholds.pop(zone, None)
            elif threshold < 1:
                raise ValueError("threshold must be >= 1")
            else:
                self._zone_thresholds[zone] = int(threshold)

    def get_zone_thresholds(self) -> dict[str, int]:
        with self._lock:
            return dict(self._zone_thresholds)

    # ------------------------------------------------------------------ reading

    @property
    def total_count(self) -> int:
        with self._lock:
            return self._snap.total

    @property
    def last_update(self) -> str | None:
        with self._lock:
            ts = self._snap.last_update
        return _iso(ts) if ts is not None else None

    @property
    def detection_running(self) -> bool:
        with self._lock:
            return self._status.state == "running"

    def status(self) -> DetectorStatus:
        with self._lock:
            return self._status

    def zone_stats(self) -> dict[str, dict[str, Any]]:
        """Per-zone ``current``, ``total_visitors`` and ``threshold`` (``None`` if unset)."""
        with self._lock:
            return {
                name: {
                    "current": s.current,
                    "total_visitors": s.total_visitors,
                    "threshold": self._zone_thresholds.get(name),
                }
                for name, s in self._snap.zones.items()
            }

    def coordinates(self) -> list[tuple[int, int]]:
        with self._lock:
            return list(self._snap.points)

    def history(self, limit: int = 300) -> list[dict[str, Any]]:
        """The most recent ``limit`` one-second buckets, oldest first."""
        with self._lock:
            items = list(self._history)[-limit:] if limit > 0 else []
            return [{**b, "zone_counts": dict(b["zone_counts"])} for b in items]

    def alerts(self) -> dict[str, dict[str, Any]]:
        """Currently active alerts keyed by ``"global"`` or zone name. Read-only."""
        with self._lock:
            return {key: a.to_dict() for key, a in self._active_alerts.items()}

    def summary(self) -> dict[str, Any]:
        """Everything the dashboard needs in one payload."""
        with self._lock:
            snap = self._snap
            return {
                "total_count": snap.total,
                "peak_count": snap.peak_total,
                "peak_at": _iso(snap.peak_at) if snap.peak_at is not None else None,
                "zones": self.zone_stats(),
                "alerts": self.alerts(),
                "global_threshold": self._global_threshold,
                "zone_thresholds": dict(self._zone_thresholds),
                "last_update": _iso(snap.last_update) if snap.last_update is not None else None,
                "detection_running": self._status.state == "running",
                "status": asdict(self._status),
                "heatmap_since": _iso(self._heat_since) if self._heat_since is not None else None,
            }

    # ------------------------------------------------------------------ images

    def frame_jpeg(self, quality: int = 80) -> tuple[int, bytes] | None:
        """Latest annotated frame as ``(sequence_number, jpeg_bytes)``, or ``None``."""
        with self._lock:
            frame, seq, cached = self._annotated, self._frame_seq, self._jpeg_cache
        if frame is None:
            return None
        if cached is not None and cached[0] == seq:
            return cached
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok:
            return None
        result = (seq, buf.tobytes())
        with self._lock:
            if self._frame_seq == seq:
                self._jpeg_cache = result
        return result

    def snapshot_jpeg(self, quality: int = 90) -> bytes | None:
        """Latest raw (un-annotated) frame as JPEG, e.g. as a backdrop for drawing zones."""
        with self._lock:
            frame = self._raw
        if frame is None:
            return None
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return buf.tobytes() if ok else None

    def heatmap_jpeg(self, quality: int = 85) -> bytes | None:
        """Cumulative occupancy heatmap blended over the latest raw frame, or ``None``."""
        with self._lock:
            if self._heat is None:
                return None
            heat = self._heat.copy()
            raw = self._raw
        h, w = heat.shape
        peak = float(heat.max())
        norm = heat / peak if peak > 0 else heat
        k = max(3, (round(0.08 * max(h, w)) // 2) * 2 + 1)
        norm = cv2.GaussianBlur(norm, (k, k), 0)
        if norm.max() > 0:
            norm = norm / norm.max()
        colored = cv2.applyColorMap((norm * 255).astype(np.uint8), cv2.COLORMAP_TURBO)
        if raw is not None:
            base = cv2.resize(raw, (w, h), interpolation=cv2.INTER_AREA)
            base = (base * 0.55).astype(np.uint8)
        else:
            base = np.full((h, w, 3), 18, np.uint8)
        alpha = np.clip(norm * 0.85, 0.0, 0.85)[..., None]
        out = (base * (1 - alpha) + colored * alpha).astype(np.uint8)
        ok, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return buf.tobytes() if ok else None

    def reset_heatmap(self) -> None:
        with self._lock:
            if self._heat is not None:
                self._heat.fill(0)
                self._heat_since = self._clock()


#: Process-wide default instance used by the CLI.
shared_state = SharedState()
