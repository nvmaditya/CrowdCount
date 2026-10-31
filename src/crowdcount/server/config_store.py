"""Runtime settings edited from the admin panel: thresholds, cameras, log retention.

Stored in ``<data_dir>/config.json`` (same format as CrowdCount 1.x).
"""

from __future__ import annotations

import copy
import logging
from typing import Any

from crowdcount.server.models import Camera, CameraCreate, CameraUpdate, RuleIn, utcnow
from crowdcount.server.storage import JsonStore
from crowdcount.state import DEFAULT_GLOBAL_THRESHOLD, DEFAULT_WARN_PERCENT

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 30

DEFAULT_CONFIG: dict[str, Any] = {
    "cameras": [],
    "thresholds": {"global_threshold": DEFAULT_GLOBAL_THRESHOLD, "zone_thresholds": {}},
    "rule_options": {"global": {}, "zones": {}},
    "log_retention_days": DEFAULT_RETENTION_DAYS,
}


def _with_defaults(data: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(DEFAULT_CONFIG)
    merged.update({k: v for k, v in data.items() if k not in ("thresholds", "rule_options")})
    merged["thresholds"].update(data.get("thresholds") or {})
    options = data.get("rule_options")
    if isinstance(options, dict):
        for key in ("global", "zones"):
            if isinstance(options.get(key), dict):
                merged["rule_options"][key] = options[key]
    return merged


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _rule_dict(
    scope: str, limit: Any, options: Any, fallback_limit: int | None = None
) -> dict[str, Any] | None:
    """One rule from stored values. Bad values (hand-edited files) fall back to defaults with a warning.

    An invalid limit uses ``fallback_limit``; without one the rule is dropped (``None``).
    """
    if not _is_int(limit) or limit < 1:
        logger.warning("Ignoring invalid alert limit %r for %s", limit, scope)
        if fallback_limit is None:
            return None
        limit = fallback_limit
    options = options if isinstance(options, dict) else {}
    rule: dict[str, Any] = {"limit": limit}
    for name, default, valid in (
        ("warn_percent", DEFAULT_WARN_PERCENT, lambda v: _is_int(v) and 1 <= v <= 100),
        ("enabled", True, lambda v: isinstance(v, bool)),
        ("banner", True, lambda v: isinstance(v, bool)),
    ):
        value = options.get(name, default)
        if not valid(value):
            logger.warning("Ignoring invalid %s %r for %s; using %r", name, value, scope, default)
            value = default
        rule[name] = value
    return rule


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
                    cfg["rule_options"]["zones"].pop(name, None)
                else:
                    zones[name] = value
            return copy.deepcopy(t)

        return self._update(change)

    # ------------------------------------------------------------------ alert rules

    def rules(self) -> dict[str, Any]:
        """The global rule and one rule per zone threshold, with defaults filled in.

        Never raises on bad stored values: invalid options fall back to defaults, an invalid
        global limit to ``DEFAULT_GLOBAL_THRESHOLD``, and a zone with an invalid limit has no rule.
        """
        cfg = self.get()
        thresholds, options = cfg["thresholds"], cfg["rule_options"]
        zone_options = options["zones"]
        zones = {}
        for name, limit in thresholds["zone_thresholds"].items():
            rule = _rule_dict(f"zone '{name}'", limit, zone_options.get(name))
            if rule is not None:
                zones[name] = rule
        glob = _rule_dict(
            "Total occupancy", thresholds["global_threshold"], options["global"], DEFAULT_GLOBAL_THRESHOLD
        )
        return {"global": glob, "zones": zones}

    def set_global_rule(self, rule: RuleIn) -> None:
        def change(cfg: dict[str, Any]) -> None:
            cfg["thresholds"]["global_threshold"] = rule.limit
            cfg["rule_options"]["global"] = rule.model_dump(exclude={"limit"})

        self._update(change)

    def set_zone_rule(self, zone: str, rule: RuleIn | None) -> bool:
        """Set a zone's rule; ``None`` removes it. ``False`` only when removing a rule that isn't there."""

        def change(cfg: dict[str, Any]) -> bool:
            limits = cfg["thresholds"]["zone_thresholds"]
            options = cfg["rule_options"]["zones"]
            if rule is None:
                existed = zone in limits
                limits.pop(zone, None)
                options.pop(zone, None)
                return existed
            limits[zone] = rule.limit
            options[zone] = rule.model_dump(exclude={"limit"})
            return True

        return self._update(change)

    def rename_zone_rule(self, old: str, new: str | None) -> None:
        """Move (or with ``new=None``, drop) a zone's threshold and rule options when the zone changes."""

        def change(cfg: dict[str, Any]) -> None:
            for bucket in (cfg["thresholds"]["zone_thresholds"], cfg["rule_options"]["zones"]):
                if old in bucket:
                    value = bucket.pop(old)
                    if new is not None:
                        bucket[new] = value

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
