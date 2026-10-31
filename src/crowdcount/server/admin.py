"""
Admin API Endpoints
Provides administrative endpoints for user management, camera configuration,
zone management, and activity logging.
"""

from pathlib import Path
import json
import io
import csv
from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, status, Query, Response, Request

from .models import (
    UserCreate, UserResponse, UserRole,
    Camera, CameraCreate, CameraUpdate,
    Zone, ZoneCreate, ZoneUpdate,
    ThresholdConfig, ThresholdUpdate,
    ActivityLog, LogCategory, LogAction,
    AlertRecord
)
from .deps import require_admin, get_current_user, get_client_ip
from .auth import (
    create_user, delete_user, get_all_users, get_user_by_id,
    LoginRequest, login, blacklist_token, decode_token
)
from .audit import (
    log_activity, get_logs, export_logs_csv, cleanup_old_logs,
    log_login_success, log_login_failed, log_logout, log_config_change,
    get_alert_history, record_alert
)
from crowdcount.state import shared_state


# Create router
router = APIRouter()

# Data file paths
DATA_DIR = Path.cwd() / "data"
CONFIG_FILE = DATA_DIR / "config.json"
ZONES_FILE = Path.cwd() / "zones.json"


def _ensure_data_dir():
    """Ensure data directory exists"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _load_config() -> dict:
    """Load system configuration"""
    _ensure_data_dir()
    
    if not CONFIG_FILE.exists():
        default_config = {
            "cameras": [],
            "thresholds": {
                "global_threshold": 50,
                "zone_thresholds": {}
            },
            "log_retention_days": 30,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
        }
        _save_config(default_config)
        return default_config
    
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return {"cameras": [], "thresholds": {"global_threshold": 50, "zone_thresholds": {}}}


def _save_config(config: dict):
    """Save system configuration"""
    _ensure_data_dir()
    config["updated_at"] = datetime.now().isoformat()
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2, default=str)


def _load_zones() -> List[dict]:
    """Load zones from zones.json"""
    if not ZONES_FILE.exists():
        return []
    try:
        with open(ZONES_FILE, 'r') as f:
            data = json.load(f)
            return data.get("zones", [])
    except (json.JSONDecodeError, FileNotFoundError):
        return []


def _save_zones(zones: List[dict]):
    """Save zones to zones.json"""
    with open(ZONES_FILE, 'w') as f:
        json.dump({"zones": zones}, f, indent=2)


# ==================== Authentication Endpoints ====================

@router.post("/auth/login", tags=["Authentication"])
async def auth_login(
    login_request: LoginRequest,
    request: Request
):
    """
    Authenticate user and return JWT token.
    """
    ip_address = await get_client_ip(request)
    
    result = login(login_request)
    
    if result is None:
        # Log failed attempt
        log_login_failed(login_request.username, ip_address)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    # Log successful login
    log_login_success(result["user"].id, result["user"].username, ip_address)
    
    return result


@router.post("/auth/logout", tags=["Authentication"])
async def auth_logout(
    request: Request,
    current_user = Depends(get_current_user)
):
    """
    Logout current user (invalidate token).
    """
    ip_address = await get_client_ip(request)
    
    # Get token from Authorization header
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        blacklist_token(token)
    
    # Log logout
    log_logout(current_user.user_id, current_user.username, ip_address)
    
    return {"success": True, "message": "Logged out successfully"}


@router.get("/auth/me", tags=["Authentication"])
async def get_current_user_info(current_user = Depends(get_current_user)):
    """
    Get current authenticated user information.
    """
    user = get_user_by_id(current_user.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role.value,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_login": user.last_login.isoformat() if user.last_login else None
    }


# ==================== User Management Endpoints ====================

@router.get("/admin/users", response_model=List[UserResponse], tags=["User Management"])
async def list_users(current_user = Depends(require_admin)):
    """
    List all users. Admin only.
    """
    return get_all_users()


@router.post("/admin/users", response_model=UserResponse, tags=["User Management"])
async def create_new_user(
    user_create: UserCreate,
    request: Request,
    current_user = Depends(require_admin)
):
    """
    Create a new user. Admin only.
    """
    ip_address = await get_client_ip(request)
    
    new_user = create_user(user_create)
    if new_user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already exists"
        )
    
    # Log user creation
    log_config_change(
        action=LogAction.USER_CREATED,
        user_id=current_user.user_id,
        username=current_user.username,
        ip_address=ip_address,
        details=f"Created user: {new_user.username} with role {new_user.role.value}",
        metadata={"new_user_id": new_user.id, "new_user_role": new_user.role.value}
    )
    
    return new_user


@router.delete("/admin/users/{user_id}", tags=["User Management"])
async def delete_existing_user(
    user_id: str,
    request: Request,
    current_user = Depends(require_admin)
):
    """
    Delete a user. Admin only. Cannot delete self.
    """
    ip_address = await get_client_ip(request)
    
    if user_id == current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account"
        )
    
    # Get user info before deletion for logging
    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    deleted = delete_user(user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Log user deletion
    log_config_change(
        action=LogAction.USER_DELETED,
        user_id=current_user.user_id,
        username=current_user.username,
        ip_address=ip_address,
        details=f"Deleted user: {user.username}",
        metadata={"deleted_user_id": user_id}
    )
    
    return {"success": True, "message": f"User {user.username} deleted"}


# ==================== Camera Management Endpoints ====================

@router.get("/admin/cameras", tags=["Camera Management"])
async def list_cameras(current_user = Depends(require_admin)):
    """
    List all configured cameras. Admin only.
    """
    config = _load_config()
    return {"cameras": config.get("cameras", [])}


@router.post("/admin/cameras", tags=["Camera Management"])
async def add_camera(
    camera: CameraCreate,
    request: Request,
    current_user = Depends(require_admin)
):
    """
    Add a new camera. Admin only.
    """
    ip_address = await get_client_ip(request)
    config = _load_config()
    
    # Create camera with ID
    new_camera = Camera(**camera.dict())
    config.setdefault("cameras", []).append(new_camera.dict())
    _save_config(config)
    
    # Log camera addition
    log_config_change(
        action=LogAction.CAMERA_ADDED,
        user_id=current_user.user_id,
        username=current_user.username,
        ip_address=ip_address,
        details=f"Added camera: {new_camera.name}",
        metadata={"camera_id": new_camera.id, "source": new_camera.source_url}
    )
    
    return {"success": True, "camera": new_camera.dict()}


@router.put("/admin/cameras/{camera_id}", tags=["Camera Management"])
async def update_camera(
    camera_id: str,
    camera_update: CameraUpdate,
    request: Request,
    current_user = Depends(require_admin)
):
    """
    Update a camera configuration. Admin only.
    """
    ip_address = await get_client_ip(request)
    config = _load_config()
    cameras = config.get("cameras", [])
    
    for i, cam in enumerate(cameras):
        if cam.get("id") == camera_id:
            # Update only provided fields
            update_data = camera_update.dict(exclude_unset=True)
            cameras[i].update(update_data)
            config["cameras"] = cameras
            _save_config(config)
            
            # Log camera update
            log_config_change(
                action=LogAction.CAMERA_UPDATED,
                user_id=current_user.user_id,
                username=current_user.username,
                ip_address=ip_address,
                details=f"Updated camera: {cameras[i].get('name', camera_id)}",
                metadata={"camera_id": camera_id, "updates": update_data}
            )
            
            return {"success": True, "camera": cameras[i]}
    
    raise HTTPException(status_code=404, detail="Camera not found")


@router.delete("/admin/cameras/{camera_id}", tags=["Camera Management"])
async def delete_camera(
    camera_id: str,
    request: Request,
    current_user = Depends(require_admin)
):
    """
    Delete a camera. Admin only.
    """
    ip_address = await get_client_ip(request)
    config = _load_config()
    cameras = config.get("cameras", [])
    
    for i, cam in enumerate(cameras):
        if cam.get("id") == camera_id:
            deleted_camera = cameras.pop(i)
            config["cameras"] = cameras
            _save_config(config)
            
            # Log camera deletion
            log_config_change(
                action=LogAction.CAMERA_DELETED,
                user_id=current_user.user_id,
                username=current_user.username,
                ip_address=ip_address,
                details=f"Deleted camera: {deleted_camera.get('name', camera_id)}",
                metadata={"camera_id": camera_id}
            )
            
            return {"success": True, "message": "Camera deleted"}
    
    raise HTTPException(status_code=404, detail="Camera not found")


# ==================== Threshold Configuration Endpoints ====================

@router.get("/admin/config/thresholds", tags=["Configuration"])
async def get_thresholds(current_user = Depends(require_admin)):
    """
    Get current threshold configuration. Admin only.
    """
    config = _load_config()
    thresholds = config.get("thresholds", {
        "global_threshold": shared_state.get_global_threshold(),
        "zone_thresholds": {}
    })
    return thresholds


@router.put("/admin/config/thresholds", tags=["Configuration"])
async def update_thresholds(
    threshold_update: ThresholdUpdate,
    request: Request,
    current_user = Depends(require_admin)
):
    """
    Update threshold configuration. Admin only.
    """
    ip_address = await get_client_ip(request)
    config = _load_config()
    thresholds = config.setdefault("thresholds", {
        "global_threshold": 50,
        "zone_thresholds": {}
    })
    
    # Update global threshold
    if threshold_update.global_threshold is not None:
        thresholds["global_threshold"] = threshold_update.global_threshold
        shared_state.set_global_threshold(threshold_update.global_threshold)
    
    # Update zone thresholds
    if threshold_update.zone_thresholds is not None:
        thresholds.setdefault("zone_thresholds", {}).update(threshold_update.zone_thresholds)
        for zone_name, threshold in threshold_update.zone_thresholds.items():
            shared_state.set_zone_threshold(zone_name, threshold)
    
    config["thresholds"] = thresholds
    _save_config(config)
    
    # Log threshold update
    log_config_change(
        action=LogAction.THRESHOLD_UPDATED,
        user_id=current_user.user_id,
        username=current_user.username,
        ip_address=ip_address,
        details="Updated thresholds",
        metadata=threshold_update.dict(exclude_unset=True)
    )
    
    return {"success": True, "thresholds": thresholds}


# ==================== Zone Management Endpoints ====================

@router.get("/admin/zones", tags=["Zone Management"])
async def list_zones_admin(current_user = Depends(require_admin)):
    """
    List all zones with full configuration. Admin only.
    """
    zones = _load_zones()
    return {"zones": zones}


@router.post("/admin/zones", tags=["Zone Management"])
async def create_zone(
    zone: ZoneCreate,
    request: Request,
    current_user = Depends(require_admin)
):
    """
    Create a new zone. Admin only.
    """
    ip_address = await get_client_ip(request)
    zones = _load_zones()
    
    # Check for duplicate name
    for z in zones:
        if z.get("name") == zone.name:
            raise HTTPException(
                status_code=400,
                detail=f"Zone with name '{zone.name}' already exists"
            )
    
    zones.append(zone.dict())
    _save_zones(zones)
    
    # Log zone creation
    log_config_change(
        action=LogAction.ZONE_ADDED,
        user_id=current_user.user_id,
        username=current_user.username,
        ip_address=ip_address,
        details=f"Created zone: {zone.name}",
        metadata={"zone_name": zone.name, "points_count": len(zone.points)}
    )
    
    return {"success": True, "zone": zone.dict()}


@router.put("/admin/zones/{zone_name}", tags=["Zone Management"])
async def update_zone(
    zone_name: str,
    zone_update: ZoneUpdate,
    request: Request,
    current_user = Depends(require_admin)
):
    """
    Update a zone. Admin only.
    """
    ip_address = await get_client_ip(request)
    zones = _load_zones()
    
    for i, z in enumerate(zones):
        if z.get("name") == zone_name:
            update_data = zone_update.dict(exclude_unset=True)
            zones[i].update(update_data)
            _save_zones(zones)
            
            # Log zone update
            log_config_change(
                action=LogAction.ZONE_UPDATED,
                user_id=current_user.user_id,
                username=current_user.username,
                ip_address=ip_address,
                details=f"Updated zone: {zone_name}",
                metadata={"zone_name": zone_name, "updates": update_data}
            )
            
            return {"success": True, "zone": zones[i]}
    
    raise HTTPException(status_code=404, detail="Zone not found")


@router.delete("/admin/zones/{zone_name}", tags=["Zone Management"])
async def delete_zone(
    zone_name: str,
    request: Request,
    current_user = Depends(require_admin)
):
    """
    Delete a zone. Admin only.
    """
    ip_address = await get_client_ip(request)
    zones = _load_zones()
    
    for i, z in enumerate(zones):
        if z.get("name") == zone_name:
            zones.pop(i)
            _save_zones(zones)
            
            # Log zone deletion
            log_config_change(
                action=LogAction.ZONE_DELETED,
                user_id=current_user.user_id,
                username=current_user.username,
                ip_address=ip_address,
                details=f"Deleted zone: {zone_name}",
                metadata={"zone_name": zone_name}
            )
            
            return {"success": True, "message": f"Zone '{zone_name}' deleted"}
    
    raise HTTPException(status_code=404, detail="Zone not found")


# ==================== Activity Logs Endpoints ====================

@router.get("/admin/logs", tags=["Activity Logs"])
async def get_activity_logs(
    category: Optional[LogCategory] = None,
    action: Optional[LogAction] = None,
    user_id: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    current_user = Depends(require_admin)
):
    """
    Query activity logs with filters. Admin only.
    """
    logs = get_logs(
        category=category,
        action=action,
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        offset=offset
    )
    
    return {
        "logs": [log.dict() for log in logs],
        "count": len(logs),
        "limit": limit,
        "offset": offset
    }


@router.get("/admin/logs/export/csv", tags=["Activity Logs"])
async def export_logs_as_csv(
    category: Optional[LogCategory] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    current_user = Depends(require_admin)
):
    """
    Export activity logs as CSV. Admin only.
    """
    csv_content = export_logs_csv(
        category=category,
        start_date=start_date,
        end_date=end_date
    )
    
    filename = f"activity_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.get("/admin/logs/export/pdf", tags=["Activity Logs"])
async def export_logs_as_pdf(
    category: Optional[LogCategory] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    current_user = Depends(require_admin)
):
    """
    Export activity logs as PDF. Admin only.
    """
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter, landscape
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import inch
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="PDF export not available. Install reportlab: pip install reportlab"
        )
    
    logs = get_logs(
        category=category,
        start_date=start_date,
        end_date=end_date,
        limit=1000
    )
    
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(letter))
    elements = []
    styles = getSampleStyleSheet()
    
    # Title
    elements.append(Paragraph("Activity Logs Report", styles['Heading1']))
    elements.append(Paragraph(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", styles['Normal']))
    elements.append(Spacer(1, 20))
    
    # Table data
    table_data = [["Timestamp", "Category", "Action", "User", "IP Address", "Details"]]
    for log in logs[:100]:  # Limit to 100 for PDF
        table_data.append([
            log.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
            log.category.value,
            log.action.value,
            log.username or "-",
            log.ip_address or "-",
            (log.details or "-")[:50]
        ])
    
    if len(table_data) > 1:
        table = Table(table_data, colWidths=[1.3*inch, 0.8*inch, 1.2*inch, 1*inch, 1*inch, 2.5*inch])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 9),
            ('FONTSIZE', (0, 1), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.lightgrey])
        ]))
        elements.append(table)
    else:
        elements.append(Paragraph("No logs found for the specified filters.", styles['Normal']))
    
    doc.build(elements)
    buffer.seek(0)
    
    filename = f"activity_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    
    return Response(
        content=buffer.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.post("/admin/logs/cleanup", tags=["Activity Logs"])
async def cleanup_logs(
    retention_days: int = Query(default=30, ge=1, le=365),
    current_user = Depends(require_admin)
):
    """
    Clean up old activity logs. Admin only.
    """
    deleted_count = cleanup_old_logs(retention_days)
    
    return {
        "success": True,
        "deleted_count": deleted_count,
        "retention_days": retention_days
    }


# ==================== Alert History Endpoints ====================

@router.get("/admin/alerts/history", tags=["Alerts"])
async def get_alerts_history(
    alert_type: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    limit: int = Query(default=100, ge=1, le=1000),
    current_user = Depends(require_admin)
):
    """
    Get historical alert records. Admin only.
    """
    alerts = get_alert_history(
        alert_type=alert_type,
        start_date=start_date,
        end_date=end_date,
        limit=limit
    )
    
    return {
        "alerts": [alert.dict() for alert in alerts],
        "count": len(alerts)
    }


# ==================== System Configuration ====================

@router.get("/admin/config", tags=["Configuration"])
async def get_system_config(current_user = Depends(require_admin)):
    """
    Get full system configuration. Admin only.
    """
    config = _load_config()
    return config


@router.get("/admin/config/retention", tags=["Configuration"])
async def get_log_retention(current_user = Depends(require_admin)):
    """
    Get log retention configuration. Admin only.
    """
    config = _load_config()
    return {"log_retention_days": config.get("log_retention_days", 30)}


@router.put("/admin/config/retention", tags=["Configuration"])
async def set_log_retention(
    retention_days: int = Query(..., ge=1, le=365),
    request: Request = None,
    current_user = Depends(require_admin)
):
    """
    Set log retention period. Admin only.
    """
    ip_address = await get_client_ip(request) if request else "unknown"
    config = _load_config()
    config["log_retention_days"] = retention_days
    _save_config(config)
    
    log_config_change(
        action=LogAction.THRESHOLD_UPDATED,
        user_id=current_user.user_id,
        username=current_user.username,
        ip_address=ip_address,
        details=f"Set log retention to {retention_days} days",
        metadata={"log_retention_days": retention_days}
    )
    
    return {"success": True, "log_retention_days": retention_days}
