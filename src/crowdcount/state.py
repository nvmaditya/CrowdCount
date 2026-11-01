"""Thread-safe live state shared by the detection pipeline and the web server.

The pipeline *publishes* one snapshot per processed frame; the server only *reads*.
Everything here is in-process memory: run the detector and the server in the same
process (``crowdcount run``) for the dashboard to see live data.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
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
DEFAULT_WARN_PERCENT = 80
#: An alert rises at once but drops a level (or clears) only after the lower
#: condition has held this long, so counts jittering around a limit do not flap.
ALERT_CLEAR_SECONDS = 5.0

_LEVEL_RANK: dict[str | None, int] = {None: 0, "near": 1, "over": 2}


def _iso(ts: float) -> str:
    """Local, timezone-aware ISO-8601 timestamp (parses unambiguously in browsers)."""
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


@dataclass(frozen=True)
class AlertRule:
    """When to warn about a scope: ``limit`` is the cap, ``warn_percent`` the early-warning band."""

    limit: int
    warn_percent: int = DEFAULT_WARN_PERCENT
    enabled: bool = True
    banner: bool = True

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise ValueError("limit must be >= 1")
        if not 1 <= self.warn_percent <= 100:
            raise ValueError("warn_percent must be between 1 and 100")

    @property
    def warn_at(self) -> int:
        """Smallest count that counts as near the limit (integer ceil, within ``[1, limit]``)."""
        return min(self.limit, max(1, -(-self.limit * self.warn_percent // 100)))

    def level(self, count: int) -> str | None:
        """``"over"`` above the limit, ``"near"`` from ``warn_at`` up, else ``None`` (also when disabled)."""
        if not self.enabled:
            return None
        if count > self.limit:
            return "over"
        if count >= self.warn_at:
            return "near"
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "limit": self.limit,
            "warn_percent": self.warn_percent,
            "warn_at": self.warn_at,
            "enabled": self.enabled,
            "banner": self.banner,
        }


@dataclass(frozen=True)
class Alert:
    """An active alert. ``key`` is ``"global"`` or the zone name."""

    key: str
    kind: str  # "global" | "zone"
    threshold: int
    current: int
    zone: str | None = None
    level: str = "over"  # "over" | "near"
    since: float = 0.0  # epoch seconds when the current level started
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    acknowledged: bool = False
    banner: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.kind,
            "zone": self.zone,
            "level": self.level,
            "threshold": self.threshold,
            "current": self.current,
            "exceeded": self.level == "over",
            "since": _iso(self.since),
            "acknowledged": self.acknowledged,
            "banner": self.banner,
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
        alert_clear_seconds: how long a lower alert level must hold before the alert
            drops a level or clears (rising is always immediate).
    """

    def __init__(
        self,
        *,
        history_size: int = 3600,
        heatmap_max_side: int = 640,
        clock: Callable[[], float] = time.time,
        alert_clear_seconds: float = ALERT_CLEAR_SECONDS,
    ) -> None:
        self._lock = threading.RLock()
        self._clock = clock
        self._history_size = history_size
        self._heatmap_max_side = heatmap_max_side
        self._listeners: list[AlertListener] = []
        self._alert_clear_seconds = alert_clear_seconds
        self._global_rule = AlertRule(DEFAULT_GLOBAL_THRESHOLD)
        self._zone_rules: dict[str, AlertRule] = {}
        self._reset_run_data()
        self._status = DetectorStatus()

    # ------------------------------------------------------------------ lifecycle

    def _reset_run_data(self) -> None:
        self._snap = _Snapshot()
        self._history: deque[dict[str, Any]] = deque(maxlen=self._history_size)
        self._bucket_second: int | None = None
        self._active_alerts: dict[str, Alert] = {}
        self._pending_drop: dict[str, float] = {}  # alert key -> when its lower level began
        self._frame_size: tuple[int, int] = (0, 0)  # (width, height)
        self._heat: np.ndarray | None = None
        self._heat_scale = 1.0
        self._heat_since: float | None = None
        self._stamp_cache: np.ndarray | None = None
        self._annotated: np.ndarray | None = None
        self._raw: np.ndarray | None = None
        self._frame_seq = 0
        self._jpeg_cache: tuple[int, bytes] | None = None

    def reset(self) -> None:
        """Clear counts, history, heatmap, frames and status. Alert rules are kept."""
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
            new_alerts = self._evaluate_alerts(now)
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

    def _evaluate_alerts(self, now: float) -> list[Alert]:
        """Update the active alerts for the latest snapshot; returns those newly raised.

        Rising (nothing -> near, near -> over) is immediate and gets a new id. Dropping a
        level or clearing waits until the lower level has held for ``alert_clear_seconds``;
        an over -> near drop fires nothing and keeps the id. Admin edits act at once:
        disabled or removed rules clear, and a changed limit re-levels the alert.
        """
        snap = self._snap
        scopes = [("global", "global", None, snap.total, self._global_rule)] + [
            (name, "zone", name, stats.current, self._zone_rules[name])
            for name, stats in snap.zones.items()
            if name in self._zone_rules
        ]
        scopes = [s for s in scopes if s[4].enabled]
        active: dict[str, Alert] = {}
        new: list[Alert] = []
        pending: dict[str, float] = {}
        for key, kind, zone, count, rule in scopes:
            level = rule.level(count)
            prev = self._active_alerts.get(key)
            # Hold a drop only while the limit is unchanged: a new limit is an admin edit, not jitter.
            if (
                prev is not None
                and _LEVEL_RANK[level] < _LEVEL_RANK[prev.level]
                and prev.threshold == rule.limit
            ):
                started = self._pending_drop.get(key, now)
                if now - started < self._alert_clear_seconds:
                    pending[key] = started
                    active[key] = replace(prev, current=count, threshold=rule.limit, banner=rule.banner)
                    continue
            if level is None:
                continue
            if prev is None or _LEVEL_RANK[level] > _LEVEL_RANK[prev.level]:
                alert = Alert(key, kind, rule.limit, count, zone, level=level, since=now, banner=rule.banner)
                new.append(alert)
            elif level != prev.level:
                alert = replace(
                    prev, level=level, since=now, current=count, threshold=rule.limit, banner=rule.banner
                )
            else:
                alert = replace(prev, current=count, threshold=rule.limit, banner=rule.banner)
            active[key] = alert
        self._active_alerts = active
        self._pending_drop = pending
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
        stamp = self._stamp()
        r = stamp.shape[0] // 2
        h, w = self._heat.shape
        for x, y in points:
            cx, cy = round(x * self._heat_scale), round(y * self._heat_scale)
            # Add (not overwrite) a Gaussian blob, clipped to the frame.
            x0, x1, y0, y1 = max(cx - r, 0), min(cx + r + 1, w), max(cy - r, 0), min(cy + r + 1, h)
            if x0 < x1 and y0 < y1:
                self._heat[y0:y1, x0:x1] += stamp[y0 - cy + r : y1 - cy + r, x0 - cx + r : x1 - cx + r]

    def _stamp(self) -> np.ndarray:
        """Gaussian blob added per detection; radius ~3.5% of the heatmap's longer side."""
        assert self._heat is not None
        radius = max(3, round(0.035 * max(self._heat.shape)))
        if self._stamp_cache is None or self._stamp_cache.shape[0] != 2 * radius + 1:
            k = cv2.getGaussianKernel(2 * radius + 1, radius / 2)
            self._stamp_cache = (k @ k.T / (k.max() ** 2)).astype(np.float32)
        return self._stamp_cache

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

    def set_global_rule(self, rule: AlertRule) -> None:
        with self._lock:
            self._global_rule = rule

    def set_zone_rule(self, zone: str, rule: AlertRule | None) -> None:
        """Set a zone's rule; ``None`` removes it."""
        with self._lock:
            if rule is None:
                self._zone_rules.pop(zone, None)
            else:
                self._zone_rules[zone] = rule

    def rename_zone_rule(self, old: str, new: str | None) -> None:
        """Move a zone's rule to ``new`` (``None`` drops it). No-op if ``old`` has no rule."""
        with self._lock:
            rule = self._zone_rules.pop(old, None)
            if rule is not None and new is not None:
                self._zone_rules[new] = rule

    def rules(self) -> dict[str, Any]:
        """The global rule and every zone rule, as plain dicts."""
        with self._lock:
            return {
                "global": self._global_rule.to_dict(),
                "zones": {name: rule.to_dict() for name, rule in self._zone_rules.items()},
            }

    def acknowledge_alert(self, alert_id: str) -> bool:
        """Mark an active alert acknowledged until it clears. ``False`` if no active alert has that id."""
        with self._lock:
            for key, alert in self._active_alerts.items():
                if alert.id == alert_id:
                    self._active_alerts[key] = replace(alert, acknowledged=True)
                    return True
        return False

    def set_global_threshold(self, threshold: int) -> None:
        """Set the global limit, keeping the rule's other options."""
        with self._lock:
            self._global_rule = replace(self._global_rule, limit=int(threshold))

    def get_global_threshold(self) -> int:
        with self._lock:
            return self._global_rule.limit

    def set_zone_threshold(self, zone: str, threshold: int | None) -> None:
        """Set a zone limit, keeping its other options (defaults for a new rule); ``None`` removes it."""
        with self._lock:
            if threshold is None:
                self._zone_rules.pop(zone, None)
            else:
                rule = self._zone_rules.get(zone)
                self._zone_rules[zone] = (
                    replace(rule, limit=int(threshold)) if rule else AlertRule(int(threshold))
                )

    def get_zone_thresholds(self) -> dict[str, int]:
        with self._lock:
            return {name: rule.limit for name, rule in self._zone_rules.items()}

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
                    "threshold": self._zone_rules[name].limit if name in self._zone_rules else None,
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
        """Currently active alerts (both levels) keyed by ``"global"`` or zone name. Read-only."""
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
                "rules": self.rules(),
                "global_threshold": self._global_rule.limit,
                "zone_thresholds": self.get_zone_thresholds(),
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
        # Log scale: busy spots stay distinguishable instead of everything saturating.
        norm = np.log1p(heat) / np.log1p(peak) if peak > 0 else heat
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
