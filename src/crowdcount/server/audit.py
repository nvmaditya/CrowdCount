"""Activity log and alert history, persisted as JSON in the data directory."""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timedelta
from typing import Any

from crowdcount.server.models import (
    ActivityLog,
    AlertRecord,
    LogAction,
    LogCategory,
    as_aware,
    utcnow,
)
from crowdcount.server.storage import JsonStore
from crowdcount.state import Alert

logger = logging.getLogger(__name__)

#: Hard cap on stored entries per file, independent of the retention period.
MAX_ENTRIES = 20_000


def _in_range(ts: datetime, start: datetime | None, end: datetime | None) -> bool:
    return (start is None or ts >= as_aware(start)) and (end is None or ts <= as_aware(end))


class AuditLog:
    """Append-only activity log plus a record of every threshold breach."""

    def __init__(self, logs: JsonStore, alerts: JsonStore, max_entries: int = MAX_ENTRIES) -> None:
        self._logs = logs
        self._alerts = alerts
        self._max = max_entries

    # ------------------------------------------------------------------ activity log

    def log(
        self,
        category: LogCategory,
        action: LogAction,
        *,
        user_id: str | None = None,
        username: str | None = None,
        ip_address: str | None = None,
        details: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ActivityLog:
        entry = ActivityLog(
            category=category,
            action=action,
            user_id=user_id,
            username=username,
            ip_address=ip_address,
            details=details,
            metadata=metadata,
        )

        def append(data: dict[str, Any]) -> None:
            logs = data.setdefault("logs", [])
            logs.append(entry.model_dump(mode="json"))
            if len(logs) > self._max:
                del logs[: len(logs) - self._max]

        self._logs.update(append)
        return entry

    def _all_logs(self) -> list[ActivityLog]:
        out = []
        for raw in self._logs.read().get("logs", []):
            try:
                out.append(ActivityLog(**raw))
            except (TypeError, ValueError):
                continue  # skip malformed rows rather than failing the whole query
        return out

    def query(
        self,
        *,
        category: LogCategory | None = None,
        action: LogAction | None = None,
        user_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[ActivityLog], int]:
        """Matching entries, newest first, plus the total number of matches."""
        logs = [
            entry
            for entry in self._all_logs()
            if (category is None or entry.category == category)
            and (action is None or entry.action == action)
            and (user_id is None or entry.user_id == user_id)
            and _in_range(entry.timestamp, start, end)
        ]
        logs.sort(key=lambda e: e.timestamp, reverse=True)
        return logs[offset : offset + limit], len(logs)

    def export_csv(self, **filters: Any) -> str:
        logs, _ = self.query(limit=self._max, **filters)
        out = io.StringIO()
        fields = ["timestamp", "category", "action", "username", "user_id", "ip_address", "details"]
        writer = csv.DictWriter(out, fieldnames=fields)
        writer.writeheader()
        for e in logs:
            writer.writerow(
                {
                    "timestamp": e.timestamp.isoformat(),
                    "category": e.category.value,
                    "action": e.action.value,
                    "username": e.username or "",
                    "user_id": e.user_id or "",
                    "ip_address": e.ip_address or "",
                    "details": e.details or "",
                }
            )
        return out.getvalue()

    def cleanup(self, retention_days: int) -> int:
        """Delete log entries and alert records older than ``retention_days``."""
        cutoff = utcnow() - timedelta(days=retention_days)

        def prune(key: str, model: type[ActivityLog] | type[AlertRecord]):
            def fn(data: dict[str, Any]) -> int:
                kept = []
                for raw in data.get(key, []):
                    try:
                        if model(**raw).timestamp < cutoff:
                            continue
                    except (TypeError, ValueError):
                        pass  # keep rows we can't parse; never silently drop data
                    kept.append(raw)
                removed = len(data.get(key, [])) - len(kept)
                data[key] = kept
                return removed

            return fn

        return self._logs.update(prune("logs", ActivityLog)) + self._alerts.update(
            prune("alerts", AlertRecord)
        )

    # ------------------------------------------------------------------ alerts

    def record_alert(self, alert: Alert) -> AlertRecord:
        """Persist a newly raised alert (near or over). Registered as a ``SharedState`` alert listener."""
        record = AlertRecord(
            id=alert.id,
            alert_type=alert.key,
            level=alert.level,
            threshold=alert.threshold,
            actual_count=alert.current,
        )

        def append(data: dict[str, Any]) -> None:
            alerts = data.setdefault("alerts", [])
            alerts.append(record.model_dump(mode="json"))
            if len(alerts) > self._max:
                del alerts[: len(alerts) - self._max]

        self._alerts.update(append)
        scope = "Total" if alert.kind == "global" else f"Zone '{alert.zone}'"
        metadata = {"alert_id": record.id, "alert_type": alert.key, "level": alert.level}
        if alert.level == "over":
            self.log(
                LogCategory.ALERT,
                LogAction.THRESHOLD_EXCEEDED,
                details=f"{scope} count {alert.current} exceeded threshold {alert.threshold}",
                metadata=metadata,
            )
            logger.warning("Alert: %s count %d exceeded threshold %d", scope, alert.current, alert.threshold)
        else:
            self.log(
                LogCategory.ALERT,
                LogAction.THRESHOLD_WARNING,
                details=f"{scope} count {alert.current} reached the early warning for threshold {alert.threshold}",
                metadata=metadata,
            )
            logger.info(
                "Alert: %s count %d reached the early warning for threshold %d",
                scope,
                alert.current,
                alert.threshold,
            )
        return record

    def alerts(
        self,
        *,
        alert_type: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100,
    ) -> list[AlertRecord]:
        records = []
        for raw in self._alerts.read().get("alerts", []):
            try:
                records.append(AlertRecord(**raw))
            except (TypeError, ValueError):
                continue
        records = [
            r
            for r in records
            if (alert_type is None or r.alert_type == alert_type) and _in_range(r.timestamp, start, end)
        ]
        records.sort(key=lambda r: r.timestamp, reverse=True)
        return records[:limit]

    def acknowledge(self, alert_id: str, username: str) -> AlertRecord | None:
        def ack(data: dict[str, Any]) -> AlertRecord | None:
            for raw in data.get("alerts", []):
                if raw.get("id") == alert_id:
                    raw.update(
                        acknowledged=True, acknowledged_by=username, acknowledged_at=utcnow().isoformat()
                    )
                    return AlertRecord(**raw)
            return None

        return self._alerts.update(ack)
