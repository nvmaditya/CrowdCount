"""Polygon zones: geometry, validation, persistence and occupancy tracking.

This module is deliberately free of OpenCV and model dependencies so the counting
logic can be tested in isolation.

Zone files look like this (``color`` is RGB, ``enabled`` defaults to ``true``)::

    {
      "zones": [
        {"name": "Entrance", "points": [[0, 0], [400, 0], [400, 300]], "color": [46, 196, 182]}
      ]
    }
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

Point = tuple[float, float]
RGB = tuple[int, int, int]

#: Colours assigned to new zones in creation order (RGB).
ZONE_PALETTE: tuple[RGB, ...] = (
    (46, 196, 182),
    (99, 102, 241),
    (245, 158, 11),
    (236, 72, 153),
    (34, 197, 94),
    (14, 165, 233),
)


class ZoneConfigError(ValueError):
    """Raised when a zone definition or zones file is invalid."""


# --------------------------------------------------------------------------- geometry


def _on_segment(px: float, py: float, a: Point, b: Point, eps: float = 1e-9) -> bool:
    """Return True if point ``(px, py)`` lies on the segment ``a``-``b``."""
    (x1, y1), (x2, y2) = a, b
    cross = (px - x1) * (y2 - y1) - (py - y1) * (x2 - x1)
    if abs(cross) > eps:
        return False
    return (
        min(x1, x2) - eps <= px <= max(x1, x2) + eps
        and min(y1, y2) - eps <= py <= max(y1, y2) + eps
    )


def point_in_polygon(point: Point, polygon: Sequence[Point]) -> bool:
    """Return True if ``point`` is inside ``polygon`` or on its boundary.

    Uses the even-odd ray casting rule, which handles concave polygons. Points on an edge
    or vertex count as inside, matching ``cv2.pointPolygonTest(...) >= 0``.
    """
    if len(polygon) < 3:
        return False
    px, py = point
    inside = False
    n = len(polygon)
    for i in range(n):
        a = polygon[i]
        b = polygon[(i + 1) % n]
        if _on_segment(px, py, a, b):
            return True
        (x1, y1), (x2, y2) = a, b
        if (y1 > py) != (y2 > py):
            x_cross = x1 + (py - y1) * (x2 - x1) / (y2 - y1)
            if px < x_cross:
                inside = not inside
    return inside


def polygon_area(polygon: Sequence[Point]) -> float:
    """Return the absolute area of a simple polygon (shoelace formula)."""
    total = 0.0
    n = len(polygon)
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


# --------------------------------------------------------------------------- model


@dataclass
class Zone:
    """A named polygon on the camera image, in pixel coordinates."""

    name: str
    points: list[tuple[int, int]]
    color: RGB = ZONE_PALETTE[0]
    enabled: bool = True

    def contains(self, point: Point) -> bool:
        """Return True if ``point`` falls inside this zone (boundary inclusive)."""
        return point_in_polygon(point, self.points)

    @property
    def bounds(self) -> tuple[int, int, int, int]:
        """Bounding box as ``(min_x, min_y, max_x, max_y)``."""
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        return min(xs), min(ys), max(xs), max(ys)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "points": [[int(x), int(y)] for x, y in self.points],
            "color": [int(c) for c in self.color],
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, index: int = 0) -> Zone:
        """Build and validate a zone from its JSON representation.

        ``index`` is only used to make error messages point at the right entry.
        """
        where = f"zone #{index + 1}"
        if not isinstance(data, Mapping):
            raise ZoneConfigError(f"{where}: expected an object, got {type(data).__name__}")

        name = data.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ZoneConfigError(f"{where}: 'name' must be a non-empty string")
        name = name.strip()
        where = f"zone '{name}'"

        raw_points = data.get("points")
        if not isinstance(raw_points, Sequence) or isinstance(raw_points, str):
            raise ZoneConfigError(f"{where}: 'points' must be a list of [x, y] pairs")
        points: list[tuple[int, int]] = []
        for p in raw_points:
            if (
                not isinstance(p, Sequence)
                or isinstance(p, str)
                or len(p) != 2
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in p)
            ):
                raise ZoneConfigError(f"{where}: invalid point {p!r}; expected [x, y] numbers")
            points.append((round(p[0]), round(p[1])))
        if len(points) < 3:
            raise ZoneConfigError(f"{where}: a polygon needs at least 3 points, got {len(points)}")

        color_raw = data.get("color", ZONE_PALETTE[index % len(ZONE_PALETTE)])
        if (
            not isinstance(color_raw, Sequence)
            or isinstance(color_raw, str)
            or len(color_raw) != 3
            or not all(isinstance(c, int) and 0 <= c <= 255 for c in color_raw)
        ):
            raise ZoneConfigError(f"{where}: 'color' must be [r, g, b] with values 0-255")
        color = (int(color_raw[0]), int(color_raw[1]), int(color_raw[2]))

        enabled = data.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ZoneConfigError(f"{where}: 'enabled' must be true or false")

        return cls(name=name, points=points, color=color, enabled=enabled)


def parse_zones(data: Any) -> list[Zone]:
    """Validate a decoded zones document and return its zones.

    Accepts either ``{"zones": [...]}`` or a bare list of zone objects.
    """
    items = data.get("zones") if isinstance(data, Mapping) else data
    if items is None:
        items = []
    if not isinstance(items, list):
        raise ZoneConfigError("expected a 'zones' list")
    zones = [Zone.from_dict(item, index=i) for i, item in enumerate(items)]
    seen: set[str] = set()
    for zone in zones:
        if zone.name in seen:
            raise ZoneConfigError(f"duplicate zone name '{zone.name}'")
        seen.add(zone.name)
    return zones


def load_zones(path: str | os.PathLike[str]) -> list[Zone]:
    """Load zones from ``path``. A missing file means no zones.

    Raises:
        ZoneConfigError: if the file is not valid JSON or a zone is malformed.
    """
    path = Path(path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError as exc:
        raise ZoneConfigError(
            f"{path}: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    try:
        return parse_zones(data)
    except ZoneConfigError as exc:
        raise ZoneConfigError(f"{path}: {exc}") from exc


def save_zones(path: str | os.PathLike[str], zones: Iterable[Zone]) -> None:
    """Write zones to ``path`` atomically (readers never see a half-written file)."""
    path = Path(path)
    payload = json.dumps({"zones": [z.to_dict() for z in zones]}, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def zones_outside_frame(zones: Iterable[Zone], width: int, height: int) -> list[str]:
    """Return the names of zones with at least one vertex outside a ``width`` x ``height`` frame."""
    out = []
    for zone in zones:
        min_x, min_y, max_x, max_y = zone.bounds
        if min_x < 0 or min_y < 0 or max_x >= width or max_y >= height:
            out.append(zone.name)
    return out


# --------------------------------------------------------------------------- counting


@dataclass(frozen=True)
class ZoneStats:
    """Counts for one zone at a point in time."""

    current: int
    total_visitors: int


@dataclass
class ZoneTracker:
    """Track per-frame occupancy and cumulative unique visitors for a set of zones.

    *Occupancy* is the number of tracked people whose centre lies in the zone in the
    latest frame. *Visitors* is the number of distinct track IDs ever seen in the zone.
    A person who is lost and re-acquired under a new track ID is counted again, so
    visitor totals are an upper bound on unique people.
    """

    zones: list[Zone] = field(default_factory=list)
    _occupancy: dict[str, int] = field(default_factory=dict, init=False)
    _visitors: dict[str, set[int]] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.set_zones(self.zones)

    def set_zones(self, zones: Sequence[Zone]) -> None:
        """Replace the zone set. Visitor history is kept for zones whose name persists."""
        self.zones = list(zones)
        names = {z.name for z in self.zones if z.enabled}
        self._visitors = {n: self._visitors.get(n, set()) for n in names}
        self._occupancy = {n: 0 for n in names}

    @property
    def active_zones(self) -> list[Zone]:
        return [z for z in self.zones if z.enabled]

    def zones_at(self, point: Point) -> list[str]:
        """Names of enabled zones containing ``point`` (zones may overlap)."""
        return [z.name for z in self.zones if z.enabled and z.contains(point)]

    def update(self, tracks: Iterable[tuple[int, Point]]) -> dict[int, list[str]]:
        """Recompute occupancy from this frame's ``(track_id, centre)`` pairs.

        Returns a mapping of track ID to the zones it is in.
        """
        occupancy = {z.name: 0 for z in self.zones if z.enabled}
        membership: dict[int, list[str]] = {}
        for track_id, centre in tracks:
            names = self.zones_at(centre)
            membership[track_id] = names
            for name in names:
                occupancy[name] += 1
                self._visitors.setdefault(name, set()).add(track_id)
        self._occupancy = occupancy
        return membership

    def reset_visitors(self) -> None:
        """Forget all visitor history (occupancy is unaffected)."""
        self._visitors = {n: set() for n in self._visitors}

    @property
    def occupancy(self) -> dict[str, int]:
        return dict(self._occupancy)

    def stats(self) -> dict[str, ZoneStats]:
        """Snapshot of every enabled zone, including empty ones, in definition order."""
        return {
            z.name: ZoneStats(
                current=self._occupancy.get(z.name, 0),
                total_visitors=len(self._visitors.get(z.name, ())),
            )
            for z in self.zones
            if z.enabled
        }
