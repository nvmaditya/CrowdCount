"""Drawing helpers for annotated frames. All functions draw in place on BGR images."""

from __future__ import annotations

import colorsys
from collections.abc import Iterable, Mapping, Sequence
from functools import lru_cache

import cv2
import numpy as np

from crowdcount.detector import Detection
from crowdcount.zones import RGB, Zone, ZoneStats

FONT = cv2.FONT_HERSHEY_SIMPLEX
BGR = tuple[int, int, int]


def to_bgr(rgb: RGB) -> BGR:
    return int(rgb[2]), int(rgb[1]), int(rgb[0])


@lru_cache(maxsize=256)
def track_color(track_id: int) -> BGR:
    """A stable, well-separated colour per track ID (golden-ratio hue stepping)."""
    hue = (track_id * 0.618033988749895) % 1.0
    r, g, b = colorsys.hsv_to_rgb(hue, 0.65, 1.0)
    return int(b * 255), int(g * 255), int(r * 255)


def _scale(frame: np.ndarray) -> float:
    """Text/line scale relative to a 720p frame, so overlays stay legible at any size."""
    return max(0.6, min(2.0, frame.shape[0] / 720))


def label(
    frame: np.ndarray,
    text: str,
    origin: tuple[int, int],
    bg: BGR,
    fg: BGR = (255, 255, 255),
    font_scale: float = 0.5,
) -> None:
    """Draw ``text`` on a filled pill whose bottom-left corner is ``origin``."""
    s = _scale(frame)
    fs, th = font_scale * s, max(1, round(s))
    (tw, tht), base = cv2.getTextSize(text, FONT, fs, th)
    pad = round(4 * s)
    x, y = origin
    x = max(0, min(x, frame.shape[1] - tw - 2 * pad))
    y = max(tht + 2 * pad, y)
    cv2.rectangle(frame, (x, y - tht - 2 * pad), (x + tw + 2 * pad, y + base), bg, -1)
    cv2.putText(frame, text, (x + pad, y - pad), FONT, fs, fg, th, cv2.LINE_AA)


def draw_zones(
    frame: np.ndarray, zones: Iterable[Zone], stats: Mapping[str, ZoneStats], alpha: float = 0.18
) -> None:
    """Translucent zone fills with an outline and a live count label."""
    zones = [z for z in zones if z.enabled]
    if not zones:
        return
    overlay = frame.copy()
    for zone in zones:
        cv2.fillPoly(overlay, [np.array(zone.points, np.int32)], to_bgr(zone.color))
    cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0, dst=frame)
    thickness = max(1, round(2 * _scale(frame)))
    for zone in zones:
        pts = np.array(zone.points, np.int32)
        color = to_bgr(zone.color)
        cv2.polylines(frame, [pts], True, color, thickness, cv2.LINE_AA)
        s = stats.get(zone.name, ZoneStats(0, 0))
        x, y, _, _ = zone.bounds
        label(
            frame,
            f"{zone.name}  {s.current} now / {s.total_visitors} total",
            (x + 4, y + 24),
            color,
            (15, 15, 15),
        )


def draw_detections(
    frame: np.ndarray,
    detections: Sequence[Detection],
    trails: Mapping[int, Sequence[tuple[int, int]]] | None = None,
) -> None:
    """Bounding boxes, track IDs with confidence, centre points and motion trails."""
    s = _scale(frame)
    thickness = max(1, round(2 * s))
    for det in detections:
        color = track_color(det.track_id)
        x1, y1, x2, y2 = det.bbox
        if trails and len(trails.get(det.track_id, ())) > 1:
            cv2.polylines(
                frame, [np.array(trails[det.track_id], np.int32)], False, color, thickness, cv2.LINE_AA
            )
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness, cv2.LINE_AA)
        cv2.circle(frame, det.center, max(2, round(3 * s)), color, -1, cv2.LINE_AA)
        label(frame, f"#{det.track_id} {det.confidence:.2f}", (x1, y1), color, (15, 15, 15), 0.45)


def draw_hud(frame: np.ndarray, lines: Sequence[str]) -> None:
    """Semi-transparent status panel in the top-left corner."""
    if not lines:
        return
    s = _scale(frame)
    fs, th = 0.55 * s, max(1, round(s))
    sizes = [cv2.getTextSize(t, FONT, fs, th)[0] for t in lines]
    line_h = max(h for _, h in sizes) + round(10 * s)
    pad = round(10 * s)
    w = max(w for w, _ in sizes) + 2 * pad
    h = line_h * len(lines) + pad
    x0, y0 = round(10 * s), round(10 * s)
    roi = frame[y0 : y0 + h, x0 : x0 + w]
    if roi.size:
        roi[:] = (roi * 0.35).astype(np.uint8)
    for i, text in enumerate(lines):
        cv2.putText(
            frame,
            text,
            (x0 + pad, y0 + line_h * (i + 1) - round(4 * s)),
            FONT,
            fs,
            (255, 255, 255),
            th,
            cv2.LINE_AA,
        )
