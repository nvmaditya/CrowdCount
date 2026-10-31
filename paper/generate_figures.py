"""Generate reproducible paper figures/data from the repo.

This script intentionally avoids hard dependencies on plotting libraries.
If matplotlib is available, it will also emit PNG charts.

Outputs (by default):
- paper/data/zone_areas.csv
- paper/figures/zones_overlay.png
- paper/figures/system_architecture.svg
- paper/figures/frame_processing_flowchart.svg
- paper/data/activity_logs_by_action.csv
- paper/data/activity_logs_timeseries_hour.csv
- paper/data/alerts_history.csv

Run:
  python paper/generate_figures.py
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np

try:
    from diagrams_svg import (  # type: ignore
        write_frame_processing_flowchart_svg,
        write_system_architecture_svg,
    )
except Exception:
    write_frame_processing_flowchart_svg = None  # type: ignore
    write_system_architecture_svg = None  # type: ignore


REPO_ROOT = Path(__file__).resolve().parent.parent


def _read_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _try_parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None

    # Common formats in this repo:
    # - "2026-01-01 18:47:25.404464"
    # - ISO8601 with 'T'
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _polygon_area_px2(points: Sequence[Sequence[float]]) -> float:
    """Shoelace formula. Returns area in pixel^2."""
    if len(points) < 3:
        return 0.0
    pts = np.asarray(points, dtype=np.float64)
    x = pts[:, 0]
    y = pts[:, 1]
    return float(0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def _polygon_centroid(points: Sequence[Sequence[float]]) -> tuple[int, int]:
    pts = np.asarray(points, dtype=np.float64)
    if pts.size == 0:
        return (0, 0)
    centroid = pts.mean(axis=0)
    return (round(float(centroid[0])), round(float(centroid[1])))


@dataclass(frozen=True)
class Zone:
    name: str
    points: list[list[int]]
    color_bgr: tuple[int, int, int]
    enabled: bool = True


def _load_zones(zones_path: Path) -> list[Zone]:
    data = _read_json(zones_path)
    zones_raw = data.get("zones", []) if isinstance(data, dict) else []

    zones: list[Zone] = []
    for z in zones_raw:
        if not isinstance(z, dict):
            continue
        name = str(z.get("name", "Unnamed"))
        points = z.get("points")
        if not isinstance(points, list):
            continue
        pts: list[list[int]] = []
        for p in points:
            if (
                isinstance(p, (list, tuple))
                and len(p) == 2
                and isinstance(p[0], (int, float))
                and isinstance(p[1], (int, float))
            ):
                pts.append([int(p[0]), int(p[1])])

        color = z.get("color", [0, 255, 0])
        if (
            isinstance(color, (list, tuple))
            and len(color) == 3
            and all(isinstance(c, (int, float)) for c in color)
        ):
            # zones.json colours are RGB (CrowdCount 2.x); OpenCV draws BGR.
            color_bgr = (int(color[2]), int(color[1]), int(color[0]))
        else:
            color_bgr = (0, 255, 0)

        enabled = bool(z.get("enabled", True))
        zones.append(Zone(name=name, points=pts, color_bgr=color_bgr, enabled=enabled))

    return zones


def write_zone_areas(zones: Sequence[Zone], out_csv: Path) -> None:
    _ensure_dir(out_csv.parent)

    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["zone_name", "enabled", "points_count", "area_px2"],
        )
        writer.writeheader()
        for z in zones:
            writer.writerow(
                {
                    "zone_name": z.name,
                    "enabled": z.enabled,
                    "points_count": len(z.points),
                    "area_px2": f"{_polygon_area_px2(z.points):.2f}",
                }
            )


def render_zone_overlay(
    zones: Sequence[Zone],
    video_path: Path,
    out_png: Path,
    alpha: float = 0.30,
) -> None:
    _ensure_dir(out_png.parent)

    frame: np.ndarray | None = None
    if video_path.is_file():
        cap = cv2.VideoCapture(str(video_path))
        try:
            ok, first = cap.read()
            if ok and first is not None:
                frame = first
        finally:
            cap.release()

    if frame is None:
        max_x = 0
        max_y = 0
        for z in zones:
            for x, y in z.points:
                max_x = max(max_x, x)
                max_y = max(max_y, y)
        width = max(1280, max_x + 50)
        height = max(720, max_y + 50)
        frame = np.zeros((height, width, 3), dtype=np.uint8)

    overlay = frame.copy()

    for z in zones:
        if not z.enabled or len(z.points) < 3:
            continue

        pts = np.asarray(z.points, dtype=np.int32)
        cv2.fillPoly(overlay, [pts], z.color_bgr)
        cv2.polylines(frame, [pts], True, z.color_bgr, 2)

        cx, cy = _polygon_centroid(z.points)
        cv2.putText(
            frame,
            z.name,
            (cx, max(20, cy)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            z.color_bgr,
            2,
        )

    cv2.addWeighted(overlay, float(alpha), frame, float(1.0 - alpha), 0, frame)
    cv2.imwrite(str(out_png), frame)


def _maybe_import_matplotlib():
    try:
        import matplotlib.pyplot as plt  # type: ignore

        return plt
    except Exception:
        return None


def write_activity_log_exports(
    activity_logs_path: Path,
    out_by_action_csv: Path,
    out_timeseries_hour_csv: Path,
    out_fig_dir: Path,
) -> None:
    _ensure_dir(out_by_action_csv.parent)
    _ensure_dir(out_fig_dir)

    if not activity_logs_path.exists():
        return

    data = _read_json(activity_logs_path)
    logs = data.get("logs", []) if isinstance(data, dict) else []

    counts = Counter()
    buckets: dict[tuple[datetime, str, str], int] = defaultdict(int)

    for item in logs:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category", "UNKNOWN"))
        action = str(item.get("action", "UNKNOWN"))

        counts[(category, action)] += 1

        ts = _try_parse_datetime(item.get("timestamp"))
        if ts is None:
            continue
        hour_bucket = ts.replace(minute=0, second=0, microsecond=0)
        buckets[(hour_bucket, category, action)] += 1

    with open(out_by_action_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["category", "action", "count"])
        writer.writeheader()
        for (category, action), count in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
            writer.writerow({"category": category, "action": action, "count": count})

    with open(out_timeseries_hour_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["hour", "category", "action", "count"])
        writer.writeheader()
        for (hour, category, action), count in sorted(buckets.items(), key=lambda x: x[0]):
            writer.writerow(
                {
                    "hour": hour.isoformat(sep=" "),
                    "category": category,
                    "action": action,
                    "count": count,
                }
            )

    plt = _maybe_import_matplotlib()
    if plt is None:
        return

    # Bar chart: counts by action (top 12)
    top = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:12]
    if top:
        labels = [f"{c}:{a}" for (c, a), _ in top]
        values = [v for _, v in top]

        fig = plt.figure(figsize=(12, 4))
        ax = fig.add_subplot(1, 1, 1)
        ax.bar(range(len(values)), values)
        ax.set_xticks(range(len(values)))
        ax.set_xticklabels(labels, rotation=45, ha="right")
        ax.set_ylabel("Count")
        ax.set_title("Activity Logs: Top Events")
        fig.tight_layout()
        fig.savefig(out_fig_dir / "activity_logs_top_events.png", dpi=200)
        plt.close(fig)


def write_alert_history_exports(
    alerts_history_path: Path,
    out_csv: Path,
    out_fig_dir: Path,
) -> None:
    _ensure_dir(out_csv.parent)
    _ensure_dir(out_fig_dir)

    rows: list[dict[str, Any]] = []

    if alerts_history_path.exists():
        data = _read_json(alerts_history_path)
        alerts = data.get("alerts", []) if isinstance(data, dict) else []
        if isinstance(alerts, list):
            for a in alerts:
                if isinstance(a, dict):
                    rows.append(a)

    # Normalize + export
    fieldnames = [
        "id",
        "timestamp",
        "alert_type",
        "threshold",
        "actual_count",
        "acknowledged",
        "acknowledged_by",
        "acknowledged_at",
    ]

    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k) for k in fieldnames})

    plt = _maybe_import_matplotlib()
    if plt is None or not rows:
        return

    # Simple timeline plot for actual_count
    parsed: list[tuple[datetime, int, str]] = []
    for r in rows:
        ts = _try_parse_datetime(r.get("timestamp"))
        actual = r.get("actual_count")
        atype = str(r.get("alert_type", "unknown"))
        if ts is None or not isinstance(actual, int):
            continue
        parsed.append((ts, actual, atype))

    if not parsed:
        return

    parsed.sort(key=lambda x: x[0])
    times = [t for t, _, _ in parsed]
    counts = [c for _, c, _ in parsed]

    fig = plt.figure(figsize=(10, 4))
    ax = fig.add_subplot(1, 1, 1)
    ax.plot(times, counts, marker="o", linewidth=1)
    ax.set_ylabel("Actual Count")
    ax.set_title("Alerts: Threshold Breaches Over Time")
    fig.autofmt_xdate(rotation=30, ha="right")
    fig.tight_layout()
    fig.savefig(out_fig_dir / "alerts_timeline.png", dpi=200)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate paper figures/data from the repo")
    parser.add_argument(
        "--video",
        type=str,
        default=None,
        help="Video whose first frame is the zone-overlay backdrop (default: blank canvas)",
    )
    parser.add_argument(
        "--zones",
        type=str,
        default=str(
            REPO_ROOT / "zones.json"
            if (REPO_ROOT / "zones.json").exists()
            else REPO_ROOT / "examples" / "zones.example.json"
        ),
        help="Zones JSON path (default: zones.json)",
    )
    parser.add_argument(
        "--activity-logs",
        type=str,
        default=str(REPO_ROOT / "data" / "activity_logs.json"),
        help="Activity logs JSON path (default: data/activity_logs.json)",
    )
    parser.add_argument(
        "--alerts-history",
        type=str,
        default=str(REPO_ROOT / "data" / "alerts_history.json"),
        help="Alerts history JSON path (default: data/alerts_history.json)",
    )
    parser.add_argument(
        "--out-dir",
        type=str,
        default=str(REPO_ROOT / "paper"),
        help="Output directory (default: paper/)",
    )

    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    fig_dir = out_dir / "figures"
    data_dir = out_dir / "data"
    _ensure_dir(fig_dir)
    _ensure_dir(data_dir)

    if write_system_architecture_svg is not None:
        write_system_architecture_svg(fig_dir / "system_architecture.svg")
    if write_frame_processing_flowchart_svg is not None:
        write_frame_processing_flowchart_svg(fig_dir / "frame_processing_flowchart.svg")

    zones = _load_zones(Path(args.zones))

    write_zone_areas(zones, data_dir / "zone_areas.csv")
    render_zone_overlay(zones, Path(args.video) if args.video else Path(), fig_dir / "zones_overlay.png")

    write_activity_log_exports(
        Path(args.activity_logs),
        data_dir / "activity_logs_by_action.csv",
        data_dir / "activity_logs_timeseries_hour.csv",
        fig_dir,
    )

    write_alert_history_exports(
        Path(args.alerts_history),
        data_dir / "alerts_history.csv",
        fig_dir,
    )

    print(f"Wrote figures to: {fig_dir}")
    print(f"Wrote data to:    {data_dir}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
