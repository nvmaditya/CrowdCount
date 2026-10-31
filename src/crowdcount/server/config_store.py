"""Runtime settings edited from the admin panel: thresholds, cameras, log retention.

Stored in ``<data_dir>/config.json`` (same format as CrowdCount 1.x).
"""

from __future__ import annotations

import copy
from typing import Any

from crowdcount.server.models import Camera, CameraCreate, CameraUpdate, utcnow
from crowdcount.server.storage import JsonStore
from crowdcount.state import DEFAULT_GLOBAL_THRESHOLD

DEFAULT_RETENTION_DAYS = 30

DEFAULT_CONFIG: dict[str, Any] = {
    "cameras": [],
    "thresholds": {"global_threshold": DEFAULT_GLOBAL_THRESHOLD, "zone_thresholds": {}},
    "log_retention_days": DEFAULT_RETENTION_DAYS,
}


def _with_defaults(data: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(DEFAULT_CONFIG)
    merged.update({k: v for k, v in data.items() if k != "thresholds"})
    merged["thresholds"].update(data.get("thresholds") or {})
    return merged


class ConfigStore:
    def __init__(self, store: JsonStore) -> None:
        self._store = store

    def get(self) -> dict[str, Any]:
        return _with_defaults(self._store.read())

    def _update(self, fn):  # type: ignore[no-untyped-def]
        def wrapped(data: dict[str, Any]) -> Any:
            full = _with_defaults(data)
            result = fn(full)
            full["updated_at"] = utcnow().isoformat()
            data.clear()
            data.update(full)
            return result

        return self._store.update(wrapped)

    # ------------------------------------------------------------------ thresholds

    def thresholds(self) -> dict[str, Any]:
        t = self.get()["thresholds"]
        return {"global_threshold": int(t["global_threshold"]), "zone_thresholds": dict(t["zone_thresholds"])}

    def update_thresholds(
        self, global_threshold: int | None = None, zone_thresholds: dict[str, int | None] | None = None
    ) -> dict[str, Any]:
        """Apply changes; a ``None`` zone threshold removes it. Returns the new thresholds."""

        def change(cfg: dict[str, Any]) -> dict[str, Any]:
            t = cfg["thresholds"]
            if global_threshold is not None:
                t["global_threshold"] = global_threshold
            zones = t.setdefault("zone_thresholds", {})
            for name, value in (zone_thresholds or {}).items():
                if value is None:
                    zones.pop(name, None)
                else:
                    zones[name] = value
            return copy.deepcopy(t)

        return self._update(change)

    def rename_zone_threshold(self, old: str, new: str | None) -> None:
        """Move (or with ``new=None``, drop) a zone's threshold when the zone changes."""

        def change(cfg: dict[str, Any]) -> None:
            zones = cfg["thresholds"]["zone_thresholds"]
            if old in zones:
                value = zones.pop(old)
                if new is not None:
                    zones[new] = value

        self._update(change)

    # ------------------------------------------------------------------ cameras

    def cameras(self) -> list[Camera]:
        return [Camera(**c) for c in self.get()["cameras"]]

    def add_camera(self, camera: CameraCreate) -> Camera:
        new = Camera(**camera.model_dump())
        self._update(lambda cfg: cfg["cameras"].append(new.model_dump(mode="json")))
        return new

    def update_camera(self, camera_id: str, update: CameraUpdate) -> Camera | None:
        changes = update.model_dump(exclude_unset=True)

        def change(cfg: dict[str, Any]) -> Camera | None:
            for cam in cfg["cameras"]:
                if cam.get("id") == camera_id:
                    cam.update(changes)
                    return Camera(**cam)
            return None

        return self._update(change)

    def delete_camera(self, camera_id: str) -> Camera | None:
        def change(cfg: dict[str, Any]) -> Camera | None:
            for cam in cfg["cameras"]:
                if cam.get("id") == camera_id:
                    cfg["cameras"].remove(cam)
                    return Camera(**cam)
            return None

        return self._update(change)

    def find_camera(self, name: str) -> Camera | None:
        """Look up a camera by name (case-insensitive)."""
        return next((c for c in self.cameras() if c.name.lower() == name.lower()), None)

    # ------------------------------------------------------------------ retention

    def retention_days(self) -> int:
        return int(self.get()["log_retention_days"])

    def set_retention_days(self, days: int) -> None:
        self._update(lambda cfg: cfg.__setitem__("log_retention_days", days))
