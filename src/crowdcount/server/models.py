"""
Pydantic Models for Admin Panel
Defines data models for users, authentication, configuration, and logging.
"""

from pydantic import BaseModel, Field, validator
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum
import uuid


# ==================== Enums ====================

class UserRole(str, Enum):
    """User role enumeration"""
    ADMIN = "admin"
    USER = "user"


class LogCategory(str, Enum):
    """Activity log category enumeration"""
    AUTH = "AUTH"
    CONFIG = "CONFIG"
    ALERT = "ALERT"
    SYSTEM = "SYSTEM"


class LogAction(str, Enum):
    """Specific log actions"""
    # Auth actions
    LOGIN_SUCCESS = "LOGIN_SUCCESS"
    LOGIN_FAILED = "LOGIN_FAILED"
    LOGOUT = "LOGOUT"
    
    # Config actions
    CAMERA_ADDED = "CAMERA_ADDED"
    CAMERA_UPDATED = "CAMERA_UPDATED"
    CAMERA_DELETED = "CAMERA_DELETED"
    ZONE_ADDED = "ZONE_ADDED"
    ZONE_UPDATED = "ZONE_UPDATED"
    ZONE_DELETED = "ZONE_DELETED"
    THRESHOLD_UPDATED = "THRESHOLD_UPDATED"
    USER_CREATED = "USER_CREATED"
    USER_DELETED = "USER_DELETED"
    
    # Alert actions
    THRESHOLD_EXCEEDED = "THRESHOLD_EXCEEDED"
    
    # System actions
    DETECTOR_STARTED = "DETECTOR_STARTED"
    DETECTOR_STOPPED = "DETECTOR_STOPPED"
    SYSTEM_ERROR = "SYSTEM_ERROR"


# ==================== User Models ====================

class UserBase(BaseModel):
    """Base user model"""
    username: str = Field(..., min_length=3, max_length=50)
    role: UserRole = UserRole.USER


class UserCreate(UserBase):
    """Model for creating a new user"""
    password: str = Field(..., min_length=6, max_length=100)


class UserUpdate(BaseModel):
    """Model for updating user"""
    username: Optional[str] = Field(None, min_length=3, max_length=50)
    password: Optional[str] = Field(None, min_length=6, max_length=100)
    role: Optional[UserRole] = None


class UserInDB(UserBase):
    """User model as stored in database/JSON"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    password_hash: str
    created_at: datetime = Field(default_factory=datetime.now)
    last_login: Optional[datetime] = None


class UserResponse(BaseModel):
    """User model for API responses (no password)"""
    id: str
    username: str
    role: UserRole
    created_at: datetime
    last_login: Optional[datetime] = None


# ==================== Authentication Models ====================

class LoginRequest(BaseModel):
    """Login request model"""
    username: str
    password: str


class TokenResponse(BaseModel):
    """JWT token response"""
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse


class TokenData(BaseModel):
    """Data extracted from JWT token"""
    user_id: str
    username: str
    role: UserRole
    exp: datetime


# ==================== Camera Models ====================

class CameraBase(BaseModel):
    """Base camera configuration model"""
    name: str = Field(..., min_length=1, max_length=100)
    source_url: str = Field(..., description="Camera source (file path, RTSP URL, or device ID)")
    enabled: bool = True


class CameraCreate(CameraBase):
    """Model for creating a new camera"""
    pass


class CameraUpdate(BaseModel):
    """Model for updating camera"""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    source_url: Optional[str] = None
    enabled: Optional[bool] = None


class Camera(CameraBase):
    """Camera model with ID"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=datetime.now)


# ==================== Zone Models ====================

class ZoneBase(BaseModel):
    """Base zone model"""
    name: str = Field(..., min_length=1, max_length=50)
    points: List[List[int]] = Field(..., description="List of [x, y] polygon points")
    color: List[int] = Field(default=[0, 255, 0], description="RGB color for zone")
    enabled: bool = True
    threshold: Optional[int] = Field(None, ge=1, description="Zone-specific threshold")


class ZoneCreate(ZoneBase):
    """Model for creating a new zone"""
    pass


class ZoneUpdate(BaseModel):
    """Model for updating zone"""
    name: Optional[str] = Field(None, min_length=1, max_length=50)
    points: Optional[List[List[int]]] = None
    color: Optional[List[int]] = None
    enabled: Optional[bool] = None
    threshold: Optional[int] = Field(None, ge=1)


class Zone(ZoneBase):
    """Zone model (compatible with zones.json)"""
    pass


# ==================== Threshold Models ====================

class ThresholdConfig(BaseModel):
    """Threshold configuration model"""
    global_threshold: int = Field(default=50, ge=1, description="Global alert threshold")
    zone_thresholds: Dict[str, int] = Field(default_factory=dict, description="Per-zone thresholds")


class ThresholdUpdate(BaseModel):
    """Model for updating thresholds"""
    global_threshold: Optional[int] = Field(None, ge=1)
    zone_thresholds: Optional[Dict[str, int]] = None


# ==================== Activity Log Models ====================

class ActivityLogBase(BaseModel):
    """Base activity log model"""
    category: LogCategory
    action: LogAction
    details: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class ActivityLogCreate(ActivityLogBase):
    """Model for creating activity log"""
    user_id: Optional[str] = None
    username: Optional[str] = None
    ip_address: Optional[str] = None


class ActivityLog(ActivityLogBase):
    """Activity log model with all fields"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=datetime.now)
    user_id: Optional[str] = None
    username: Optional[str] = None
    ip_address: Optional[str] = None


class ActivityLogFilter(BaseModel):
    """Filter for querying activity logs"""
    category: Optional[LogCategory] = None
    action: Optional[LogAction] = None
    user_id: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)


# ==================== Alert History Models ====================

class AlertRecord(BaseModel):
    """Record of an alert occurrence"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=datetime.now)
    alert_type: str  # 'global' or zone name
    threshold: int
    actual_count: int
    acknowledged: bool = False
    acknowledged_by: Optional[str] = None
    acknowledged_at: Optional[datetime] = None


# ==================== Config Models ====================

class SystemConfig(BaseModel):
    """System configuration model"""
    cameras: List[Camera] = Field(default_factory=list)
    thresholds: ThresholdConfig = Field(default_factory=ThresholdConfig)
    log_retention_days: int = Field(default=30, ge=1, le=365)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
