"""Run-metrics logging for paper/analysis.

This module is intentionally dependency-light (stdlib only) so it can be enabled
without adding new packages.

It writes CSV/JSON files suitable for plotting:
- frame_timing.csv: per-processed-frame timing + instantaneous FPS
- confidence_scores.csv: one row per accepted detection with confidence
- zone_timeseries.csv: per-frame total + per-zone occupancy
- track_summaries.csv: per-track lifetime stats and max trail length
- history_dump.json: `/history`-shaped payload dumped from SharedState
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


@dataclass
class TrackStats:
    track_id: int
    first_frame: int
    last_frame: int
    first_timestamp: str
    last_timestamp: str
    frames_seen: int = 0
    max_trail_len: int = 0
    last_confidence: float = 0.0


class MetricsLogger:
    def __init__(self, out_dir: Path, zone_names: Iterable[str]):
        self.out_dir = Path(out_dir)
        _ensure_dir(self.out_dir)

        self.zone_names: List[str] = list(zone_names)

        self._frame_f = (self.out_dir / "frame_timing.csv").open("w", newline="", encoding="utf-8")
        self._conf_f = (self.out_dir / "confidence_scores.csv").open("w", newline="", encoding="utf-8")
        self._zone_f = (self.out_dir / "zone_timeseries.csv").open("w", newline="", encoding="utf-8")

        self._frame_w = csv.DictWriter(
            self._frame_f,
            fieldnames=[
                "timestamp",
                "frame_index",
                "frames_read",
                "source_fps",
                "frame_width",
                "frame_height",
                "speed_frame_skip",
                "people_count",
                "infer_ms",
                "post_ms",
                "frame_ms",
                "fps_inst",
            ],
        )
        self._frame_w.writeheader()

        self._conf_w = csv.DictWriter(
            self._conf_f,
            fieldnames=[
                "timestamp",
                "frame_index",
                "track_id",
                "confidence",
                "zones",
                "x1",
                "y1",
                "x2",
                "y2",
                "center_x",
                "center_y",
                "box_area",
            ],
        )
        self._conf_w.writeheader()

        self._zone_w = csv.DictWriter(
            self._zone_f,
            fieldnames=["timestamp", "frame_index", "total_count", *self.zone_names],
        )
        self._zone_w.writeheader()

        self._tracks: Dict[int, TrackStats] = {}

    def log_frame_timing(
        self,
        *,
        timestamp: str,
        frame_index: int,
        frames_read: int,
        source_fps: int,
        frame_width: int,
        frame_height: int,
        speed_frame_skip: int,
        people_count: int,
        infer_ms: float,
        post_ms: float,
        frame_ms: float,
        fps_inst: float,
    ) -> None:
        self._frame_w.writerow(
            {
                "timestamp": timestamp,
                "frame_index": frame_index,
                "frames_read": frames_read,
                "source_fps": source_fps,
                "frame_width": frame_width,
                "frame_height": frame_height,
                "speed_frame_skip": speed_frame_skip,
                "people_count": people_count,
                "infer_ms": f"{infer_ms:.3f}",
                "post_ms": f"{post_ms:.3f}",
                "frame_ms": f"{frame_ms:.3f}",
                "fps_inst": f"{fps_inst:.3f}",
            }
        )

    def log_detection(
        self,
        *,
        timestamp: str,
        frame_index: int,
        track_id: int,
        confidence: float,
        zones: List[str],
        bbox: List[int],
        center: List[int] | tuple[int, int],
    ) -> None:
        x1, y1, x2, y2 = bbox
        box_area = max(0, (x2 - x1) * (y2 - y1))
        cx, cy = int(center[0]), int(center[1])
        self._conf_w.writerow(
            {
                "timestamp": timestamp,
                "frame_index": frame_index,
                "track_id": track_id,
                "confidence": f"{confidence:.6f}",
                "zones": ";".join(zones),
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
                "center_x": cx,
                "center_y": cy,
                "box_area": box_area,
            }
        )

    def log_zone_counts(self, *, timestamp: str, frame_index: int, total_count: int, zone_counts: Dict[str, int]) -> None:
        row = {"timestamp": timestamp, "frame_index": frame_index, "total_count": total_count}
        for zn in self.zone_names:
            row[zn] = int(zone_counts.get(zn, 0))
        self._zone_w.writerow(row)

    def note_track_seen(
        self,
        *,
        timestamp: str,
        frame_index: int,
        track_id: int,
        confidence: float,
        trail_len: int,
    ) -> None:
        stats = self._tracks.get(track_id)
        if stats is None:
            stats = TrackStats(
                track_id=track_id,
                first_frame=frame_index,
                last_frame=frame_index,
                first_timestamp=timestamp,
                last_timestamp=timestamp,
            )
            self._tracks[track_id] = stats

        stats.last_frame = frame_index
        stats.last_timestamp = timestamp
        stats.frames_seen += 1
        stats.max_trail_len = max(stats.max_trail_len, int(trail_len))
        stats.last_confidence = float(confidence)

    def write_track_summaries(self) -> None:
        out_path = self.out_dir / "track_summaries.csv"
        with out_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(
                f,
                fieldnames=[
                    "track_id",
                    "first_frame",
                    "last_frame",
                    "frames_seen",
                    "first_timestamp",
                    "last_timestamp",
                    "duration_frames",
                    "max_trail_len",
                    "last_confidence",
                ],
            )
            w.writeheader()
            for track_id in sorted(self._tracks.keys()):
                st = self._tracks[track_id]
                w.writerow(
                    {
                        "track_id": st.track_id,
                        "first_frame": st.first_frame,
                        "last_frame": st.last_frame,
                        "frames_seen": st.frames_seen,
                        "first_timestamp": st.first_timestamp,
                        "last_timestamp": st.last_timestamp,
                        "duration_frames": max(0, st.last_frame - st.first_frame + 1),
                        "max_trail_len": st.max_trail_len,
                        "last_confidence": f"{st.last_confidence:.6f}",
                    }
                )

    def write_history_dump(self, payload: dict) -> None:
        out_path = self.out_dir / "history_dump.json"
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def close(self) -> None:
        try:
            self._frame_f.close()
        finally:
            try:
                self._conf_f.close()
            finally:
                self._zone_f.close()
