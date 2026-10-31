"""Request/response schemas for the HTTP API (Pydantic v2)."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from crowdcount.zones import Zone, ZoneConfigError


def utcnow() -> datetime:
    """Current time as a timezone-aware local datetime."""
    return datetime.now().astimezone()


def as_aware(value: datetime) -> datetime:
    """Treat naive datetimes (old records, browser date inputs) as local time."""
    return value.astimezone() if value.tzinfo is None else value


# ------------------------------------------------------------------ enums


class UserRole(str, Enum):
    ADMIN = "admin"
    USER = "user"


class LogCategory(str, Enum):
    AUTH = "AUTH"
    CONFIG = "CONFIG"
    ALERT = "ALERT"
    SYSTEM = "SYSTEM"


class LogAction(str, Enum):
    LOGIN_SUCCESS = "LOGIN_SUCCESS"
    LOGIN_FAILED = "LOGIN_FAILED"
    LOGOUT = "LOGOUT"
    PASSWORD_CHANGED = "PASSWORD_CHANGED"
    CAMERA_ADDED = "CAMERA_ADDED"
    CAMERA_UPDATED = "CAMERA_UPDATED"
    CAMERA_DELETED = "CAMERA_DELETED"
    ZONE_ADDED = "ZONE_ADDED"
    ZONE_UPDATED = "ZONE_UPDATED"
    ZONE_DELETED = "ZONE_DELETED"
    THRESHOLD_UPDATED = "THRESHOLD_UPDATED"
    RETENTION_UPDATED = "RETENTION_UPDATED"
    USER_CREATED = "USER_CREATED"
    USER_DELETED = "USER_DELETED"
    THRESHOLD_EXCEEDED = "THRESHOLD_EXCEEDED"
    ALERT_ACKNOWLEDGED = "ALERT_ACKNOWLEDGED"
    HEATMAP_RESET = "HEATMAP_RESET"
    LOGS_CLEANED = "LOGS_CLEANED"
    DETECTOR_STARTED = "DETECTOR_STARTED"
    DETECTOR_STOPPED = "DETECTOR_STOPPED"
    SYSTEM_ERROR = "SYSTEM_ERROR"


# ------------------------------------------------------------------ users & auth

MAX_PASSWORD_BYTES = 72  # bcrypt only looks at the first 72 bytes


def _check_password(value: str) -> str:
    if len(value.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ValueError(f"password must be at most {MAX_PASSWORD_BYTES} bytes")
    return value


class UserInDB(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    username: str
    password_hash: str
    role: UserRole = UserRole.USER
    created_at: datetime = Field(default_factory=utcnow)
    last_login: datetime | None = None


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9_.@-]+$")
    password: str = Field(min_length=8)
    role: UserRole = UserRole.USER

    _pw = field_validator("password")(_check_password)


class UserResponse(BaseModel):
    id: str
    username: str
    role: UserRole
    created_at: datetime
    last_login: datetime | None = None


class LoginRequest(BaseModel):
    username: str = Field(max_length=50)
    password: str = Field(max_length=200)


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)

    _pw = field_validator("new_password")(_check_password)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse


class TokenData(BaseModel):
    user_id: str
    username: str
    role: UserRole
    exp: datetime
    jti: str


# ------------------------------------------------------------------ cameras


class CameraCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    source_url: str = Field(
        min_length=1, max_length=500, description="Camera index, file path, or stream URL"
    )
    enabled: bool = True


class CameraUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    source_url: str | None = Field(None, min_length=1, max_length=500)
    enabled: bool | None = None


class Camera(CameraCreate):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=utcnow)


# ------------------------------------------------------------------ zones


class ZoneIn(BaseModel):
    """A zone as sent by the admin panel. Validated with the same rules as zones.json."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=50)
    points: list[list[float]] = Field(min_length=3, max_length=200)
    color: list[int] | None = None
    enabled: bool = True

    @model_validator(mode="after")
    def _validate_geometry(self) -> ZoneIn:
        try:
            self.to_zone()
        except ZoneConfigError as exc:
            raise ValueError(str(exc)) from exc
        return self

    def to_zone(self, index: int = 0) -> Zone:
        data: dict[str, Any] = self.model_dump(exclude_none=True)
        return Zone.from_dict(data, index=index)


class ZoneUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=50)
    points: list[list[float]] | None = Field(None, min_length=3, max_length=200)
    color: list[int] | None = None
    enabled: bool | None = None


# ------------------------------------------------------------------ thresholds & config


class ThresholdUpdate(BaseModel):
    """``zone_thresholds`` values of ``null`` remove that zone's threshold."""

    global_threshold: int | None = Field(None, ge=1, le=100_000)
    zone_thresholds: dict[str, int | None] | None = None

    @field_validator("zone_thresholds")
    @classmethod
    def _positive(cls, value: dict[str, int | None] | None) -> dict[str, int | None] | None:
        for zone, threshold in (value or {}).items():
            if threshold is not None and threshold < 1:
                raise ValueError(f"threshold for '{zone}' must be >= 1")
        return value


# ------------------------------------------------------------------ logs & alerts


class ActivityLog(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=utcnow)
    category: LogCategory
    action: LogAction
    user_id: str | None = None
    username: str | None = None
    ip_address: str | None = None
    details: str | None = None
    metadata: dict[str, Any] | None = None

    @field_validator("timestamp")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return as_aware(value)


class AlertRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=utcnow)
    alert_type: str  # "global" or a zone name
    threshold: int
    actual_count: int
    acknowledged: bool = False
    acknowledged_by: str | None = None
    acknowledged_at: datetime | None = None

    @field_validator("timestamp")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return as_aware(value)
