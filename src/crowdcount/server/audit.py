"""
Activity Logging Service
Provides thread-safe activity logging and log management.
"""

import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Dict, Any
import io
import csv

from .models import (
    ActivityLog, ActivityLogCreate, ActivityLogFilter,
    LogCategory, LogAction, AlertRecord
)


# Data file paths
DATA_DIR = Path.cwd() / "data"
LOGS_FILE = DATA_DIR / "activity_logs.json"
ALERTS_FILE = DATA_DIR / "alerts_history.json"

# Thread lock for file operations
_logs_lock = threading.RLock()
_alerts_lock = threading.RLock()

# Default retention period
DEFAULT_RETENTION_DAYS = 30


def _ensure_data_dir():
    """Ensure data directory exists"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)


# ==================== Activity Log Functions ====================

def _load_logs() -> List[dict]:
    """Load activity logs from JSON file"""
    _ensure_data_dir()
    
    with _logs_lock:
        if not LOGS_FILE.exists():
            return []
        try:
            with open(LOGS_FILE, 'r') as f:
                data = json.load(f)
                return data.get("logs", [])
        except (json.JSONDecodeError, FileNotFoundError):
            return []


def _save_logs(logs: List[dict]):
    """Save activity logs to JSON file"""
    _ensure_data_dir()
    
    with _logs_lock:
        with open(LOGS_FILE, 'w') as f:
            json.dump({"logs": logs}, f, indent=2, default=str)


def log_activity(
    category: LogCategory,
    action: LogAction,
    user_id: Optional[str] = None,
    username: Optional[str] = None,
    ip_address: Optional[str] = None,
    details: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> ActivityLog:
    """
    Create a new activity log entry.
    Thread-safe logging function.
    """
    log_entry = ActivityLog(
        category=category,
        action=action,
        user_id=user_id,
        username=username,
        ip_address=ip_address,
        details=details,
        metadata=metadata,
        timestamp=datetime.now()
    )
    
    logs = _load_logs()
    logs.append(log_entry.dict())
    _save_logs(logs)
    
    return log_entry


def get_logs(
    category: Optional[LogCategory] = None,
    action: Optional[LogAction] = None,
    user_id: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    limit: int = 100,
    offset: int = 0
) -> List[ActivityLog]:
    """
    Query activity logs with optional filters.
    Returns logs in reverse chronological order (newest first).
    """
    logs = _load_logs()
    
    # Convert to ActivityLog objects
    log_objects = []
    for log_data in logs:
        try:
            log_objects.append(ActivityLog(**log_data))
        except Exception:
            continue
    
    # Apply filters
    filtered_logs = log_objects
    
    if category:
        filtered_logs = [l for l in filtered_logs if l.category == category]
    
    if action:
        filtered_logs = [l for l in filtered_logs if l.action == action]
    
    if user_id:
        filtered_logs = [l for l in filtered_logs if l.user_id == user_id]
    
    if start_date:
        filtered_logs = [l for l in filtered_logs if l.timestamp >= start_date]
    
    if end_date:
        filtered_logs = [l for l in filtered_logs if l.timestamp <= end_date]
    
    # Sort by timestamp (newest first)
    filtered_logs.sort(key=lambda x: x.timestamp, reverse=True)
    
    # Apply pagination
    return filtered_logs[offset:offset + limit]


def get_log_count(
    category: Optional[LogCategory] = None,
    action: Optional[LogAction] = None,
    user_id: Optional[str] = None
) -> int:
    """Get total count of logs matching filters"""
    logs = get_logs(category=category, action=action, user_id=user_id, limit=10000)
    return len(logs)


def cleanup_old_logs(retention_days: int = DEFAULT_RETENTION_DAYS) -> int:
    """
    Remove logs older than retention period.
    Returns number of deleted logs.
    """
    cutoff_date = datetime.now() - timedelta(days=retention_days)
    
    logs = _load_logs()
    original_count = len(logs)
    
    # Filter out old logs
    filtered_logs = []
    for log_data in logs:
        try:
            timestamp = log_data.get("timestamp")
            if isinstance(timestamp, str):
                timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            if timestamp >= cutoff_date:
                filtered_logs.append(log_data)
        except Exception:
            # Keep logs that can't be parsed
            filtered_logs.append(log_data)
    
    _save_logs(filtered_logs)
    
    deleted_count = original_count - len(filtered_logs)
    return deleted_count


def export_logs_csv(
    category: Optional[LogCategory] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
) -> str:
    """
    Export logs to CSV format.
    Returns CSV string.
    """
    logs = get_logs(
        category=category,
        start_date=start_date,
        end_date=end_date,
        limit=10000
    )
    
    output = io.StringIO()
    fieldnames = ['timestamp', 'category', 'action', 'username', 'user_id', 'ip_address', 'details']
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    
    for log in logs:
        writer.writerow({
            'timestamp': log.timestamp.isoformat(),
            'category': log.category.value,
            'action': log.action.value,
            'username': log.username or '',
            'user_id': log.user_id or '',
            'ip_address': log.ip_address or '',
            'details': log.details or ''
        })
    
    return output.getvalue()


# ==================== Alert History Functions ====================

def _load_alerts() -> List[dict]:
    """Load alert history from JSON file"""
    _ensure_data_dir()
    
    with _alerts_lock:
        if not ALERTS_FILE.exists():
            return []
        try:
            with open(ALERTS_FILE, 'r') as f:
                data = json.load(f)
                return data.get("alerts", [])
        except (json.JSONDecodeError, FileNotFoundError):
            return []


def _save_alerts(alerts: List[dict]):
    """Save alert history to JSON file"""
    _ensure_data_dir()
    
    with _alerts_lock:
        with open(ALERTS_FILE, 'w') as f:
            json.dump({"alerts": alerts}, f, indent=2, default=str)


def record_alert(
    alert_type: str,
    threshold: int,
    actual_count: int
) -> AlertRecord:
    """
    Record a threshold breach alert.
    """
    alert = AlertRecord(
        alert_type=alert_type,
        threshold=threshold,
        actual_count=actual_count,
        timestamp=datetime.now()
    )
    
    alerts = _load_alerts()
    alerts.append(alert.dict())
    _save_alerts(alerts)
    
    # Also log as activity
    log_activity(
        category=LogCategory.ALERT,
        action=LogAction.THRESHOLD_EXCEEDED,
        details=f"{alert_type} threshold exceeded: {actual_count} > {threshold}"
    )
    
    return alert


def get_alert_history(
    alert_type: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    limit: int = 100
) -> List[AlertRecord]:
    """Get alert history with optional filters"""
    alerts = _load_alerts()
    
    alert_objects = []
    for alert_data in alerts:
        try:
            alert_objects.append(AlertRecord(**alert_data))
        except Exception:
            continue
    
    # Apply filters
    if alert_type:
        alert_objects = [a for a in alert_objects if a.alert_type == alert_type]
    
    if start_date:
        alert_objects = [a for a in alert_objects if a.timestamp >= start_date]
    
    if end_date:
        alert_objects = [a for a in alert_objects if a.timestamp <= end_date]
    
    # Sort by timestamp (newest first)
    alert_objects.sort(key=lambda x: x.timestamp, reverse=True)
    
    return alert_objects[:limit]


def acknowledge_alert(
    alert_id: str,
    acknowledged_by: str
) -> bool:
    """Acknowledge an alert"""
    alerts = _load_alerts()
    
    for alert in alerts:
        if alert.get("id") == alert_id:
            alert["acknowledged"] = True
            alert["acknowledged_by"] = acknowledged_by
            alert["acknowledged_at"] = datetime.now().isoformat()
            _save_alerts(alerts)
            return True
    
    return False


# ==================== Convenience Logging Functions ====================

def log_login_success(user_id: str, username: str, ip_address: str):
    """Log successful login"""
    return log_activity(
        category=LogCategory.AUTH,
        action=LogAction.LOGIN_SUCCESS,
        user_id=user_id,
        username=username,
        ip_address=ip_address,
        details=f"User {username} logged in successfully"
    )


def log_login_failed(username: str, ip_address: str):
    """Log failed login attempt"""
    return log_activity(
        category=LogCategory.AUTH,
        action=LogAction.LOGIN_FAILED,
        username=username,
        ip_address=ip_address,
        details=f"Failed login attempt for user {username}"
    )


def log_logout(user_id: str, username: str, ip_address: str):
    """Log logout"""
    return log_activity(
        category=LogCategory.AUTH,
        action=LogAction.LOGOUT,
        user_id=user_id,
        username=username,
        ip_address=ip_address,
        details=f"User {username} logged out"
    )


def log_config_change(
    action: LogAction,
    user_id: str,
    username: str,
    ip_address: str,
    details: str,
    metadata: Optional[Dict[str, Any]] = None
):
    """Log configuration change"""
    return log_activity(
        category=LogCategory.CONFIG,
        action=action,
        user_id=user_id,
        username=username,
        ip_address=ip_address,
        details=details,
        metadata=metadata
    )


def log_system_event(action: LogAction, details: str, metadata: Optional[Dict[str, Any]] = None):
    """Log system event"""
    return log_activity(
        category=LogCategory.SYSTEM,
        action=action,
        details=details,
        metadata=metadata
    )
