"""Authentication endpoints (``/api/auth``) and the admin API (``/api/admin``)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from crowdcount.server.auth import LastAdminError, UserExistsError, verify_password
from crowdcount.server.deps import (
    Services,
    bearer_token,
    client_ip,
    current_user,
    get_services,
    require_admin,
)
from crowdcount.server.models import (
    Camera,
    CameraCreate,
    CameraUpdate,
    LogAction,
    LogCategory,
    LoginRequest,
    PasswordChange,
    ThresholdUpdate,
    TokenData,
    TokenResponse,
    UserCreate,
    UserResponse,
    ZoneIn,
    ZoneUpdate,
)
from crowdcount.server.reports import activity_report
from crowdcount.zones import Zone, ZoneConfigError, load_zones, save_zones

auth_router = APIRouter(prefix="/api/auth", tags=["Authentication"])
admin_router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])


def _user_response(user) -> UserResponse:  # type: ignore[no-untyped-def]
    return UserResponse(
        id=user.id,
        username=user.username,
        role=user.role,
        created_at=user.created_at,
        last_login=user.last_login,
    )


def _audit(services: Services, user: TokenData, ip: str, action: LogAction, details: str, **metadata) -> None:  # type: ignore[no-untyped-def]
    services.audit.log(
        LogCategory.CONFIG,
        action,
        user_id=user.user_id,
        username=user.username,
        ip_address=ip,
        details=details,
        metadata=metadata or None,
    )


# ====================================================================== auth


@auth_router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, services: Services = Depends(get_services), ip: str = Depends(client_ip)):
    user = services.users.authenticate(body.username, body.password)
    if user is None:
        services.audit.log(
            LogCategory.AUTH,
            LogAction.LOGIN_FAILED,
            username=body.username,
            ip_address=ip,
            details=f"Failed sign-in for '{body.username}'",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    services.users.touch_login(user.id)
    token, expires_in = services.tokens.issue(user)
    services.audit.log(
        LogCategory.AUTH, LogAction.LOGIN_SUCCESS, user_id=user.id, username=user.username, ip_address=ip
    )
    refreshed = services.users.get(user.id) or user
    return TokenResponse(access_token=token, expires_in=expires_in, user=_user_response(refreshed))


@auth_router.post("/logout")
def logout(
    user: TokenData = Depends(current_user),
    token: str | None = Depends(bearer_token),
    services: Services = Depends(get_services),
    ip: str = Depends(client_ip),
) -> dict:
    if token:
        services.tokens.revoke(token)
    services.audit.log(
        LogCategory.AUTH, LogAction.LOGOUT, user_id=user.user_id, username=user.username, ip_address=ip
    )
    return {"success": True}


@auth_router.get("/me", response_model=UserResponse)
def me(user: TokenData = Depends(current_user), services: Services = Depends(get_services)):
    record = services.users.get(user.user_id)
    if record is None:
        raise HTTPException(status_code=404, detail="User no longer exists")
    return _user_response(record)


@auth_router.post("/password")
def change_password(
    body: PasswordChange,
    user: TokenData = Depends(current_user),
    services: Services = Depends(get_services),
    ip: str = Depends(client_ip),
) -> dict:
    record = services.users.get(user.user_id)
    if record is None or not verify_password(body.current_password, record.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    services.users.set_password(record.username, body.new_password)
    services.audit.log(
        LogCategory.AUTH,
        LogAction.PASSWORD_CHANGED,
        user_id=user.user_id,
        username=user.username,
        ip_address=ip,
    )
    return {"success": True}


# ====================================================================== users


@admin_router.get("/users", response_model=list[UserResponse], tags=["Users"])
def list_users(services: Services = Depends(get_services)):
    return [_user_response(u) for u in services.users.all()]


@admin_router.post("/users", response_model=UserResponse, status_code=201, tags=["Users"])
def create_user(
    body: UserCreate,
    admin: TokenData = Depends(require_admin),
    services: Services = Depends(get_services),
    ip: str = Depends(client_ip),
):
    try:
        user = services.users.create(body.username, body.password, body.role)
    except UserExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _audit(
        services,
        admin,
        ip,
        LogAction.USER_CREATED,
        f"Created {user.role.value} '{user.username}'",
        new_user_id=user.id,
    )
    return _user_response(user)


@admin_router.delete("/users/{user_id}", tags=["Users"])
def delete_user(
    user_id: str,
    admin: TokenData = Depends(require_admin),
    services: Services = Depends(get_services),
    ip: str = Depends(client_ip),
) -> dict:
    if user_id == admin.user_id:
        raise HTTPException(status_code=400, detail="You cannot delete your own account")
    try:
        user = services.users.delete(user_id)
    except LastAdminError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    _audit(
        services,
        admin,
        ip,
        LogAction.USER_DELETED,
        f"Deleted user '{user.username}'",
        deleted_user_id=user_id,
    )
    return {"success": True}


# ====================================================================== cameras


@admin_router.get("/cameras", tags=["Cameras"])
def list_cameras(services: Services = Depends(get_services)) -> dict:
    """Saved video sources. Start one with ``crowdcount run --camera NAME``."""
    return {"cameras": [c.model_dump(mode="json") for c in services.config.cameras()]}


@admin_router.post("/cameras", response_model=Camera, status_code=201, tags=["Cameras"])
def add_camera(
    body: CameraCreate,
    admin: TokenData = Depends(require_admin),
    services: Services = Depends(get_services),
    ip: str = Depends(client_ip),
):
    if services.config.find_camera(body.name):
        raise HTTPException(status_code=409, detail=f"a camera named '{body.name}' already exists")
    camera = services.config.add_camera(body)
    _audit(services, admin, ip, LogAction.CAMERA_ADDED, f"Added camera '{camera.name}'", camera_id=camera.id)
    return camera


@admin_router.put("/cameras/{camera_id}", response_model=Camera, tags=["Cameras"])
def update_camera(
    camera_id: str,
    body: CameraUpdate,
    admin: TokenData = Depends(require_admin),
    services: Services = Depends(get_services),
    ip: str = Depends(client_ip),
):
    camera = services.config.update_camera(camera_id, body)
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    changes = body.model_dump(exclude_unset=True)
    _audit(services, admin, ip, LogAction.CAMERA_UPDATED, f"Updated camera '{camera.name}'", changes=changes)
    return camera


@admin_router.delete("/cameras/{camera_id}", tags=["Cameras"])
def delete_camera(
    camera_id: str,
    admin: TokenData = Depends(require_admin),
    services: Services = Depends(get_services),
    ip: str = Depends(client_ip),
) -> dict:
    camera = services.config.delete_camera(camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    _audit(
        services, admin, ip, LogAction.CAMERA_DELETED, f"Deleted camera '{camera.name}'", camera_id=camera_id
    )
    return {"success": True}


# ====================================================================== thresholds


@admin_router.get("/config/thresholds", tags=["Configuration"])
def get_thresholds(services: Services = Depends(get_services)) -> dict:
    return services.config.thresholds()


@admin_router.put("/config/thresholds", tags=["Configuration"])
def update_thresholds(
    body: ThresholdUpdate,
    admin: TokenData = Depends(require_admin),
    services: Services = Depends(get_services),
    ip: str = Depends(client_ip),
) -> dict:
    """Persist thresholds and apply them to the live detector immediately."""
    thresholds = services.config.update_thresholds(body.global_threshold, body.zone_thresholds)
    if body.global_threshold is not None:
        services.state.set_global_threshold(body.global_threshold)
    for zone, value in (body.zone_thresholds or {}).items():
        services.state.set_zone_threshold(zone, value)
    _audit(
        services,
        admin,
        ip,
        LogAction.THRESHOLD_UPDATED,
        "Updated alert thresholds",
        **body.model_dump(exclude_unset=True),
    )
    return {"success": True, "thresholds": thresholds}


# ====================================================================== zones


def _load_zones_or_500(services: Services) -> list[Zone]:
    try:
        return load_zones(services.zones_file)
    except ZoneConfigError as exc:
        raise HTTPException(status_code=500, detail=f"zones file is invalid: {exc}") from exc


@admin_router.get("/zones", tags=["Zones"])
def list_zones(services: Services = Depends(get_services)) -> dict:
    """Zones from the zones file the detector watches (changes apply live)."""
    zones = _load_zones_or_500(services)
    return {"zones": [z.to_dict() for z in zones], "file": str(services.zones_file)}


@admin_router.post("/zones", status_code=201, tags=["Zones"])
def create_zone(
    body: ZoneIn,
    admin: TokenData = Depends(require_admin),
    services: Services = Depends(get_services),
    ip: str = Depends(client_ip),
) -> dict:
    with services.zones_lock:
        zones = _load_zones_or_500(services)
        if any(z.name == body.name.strip() for z in zones):
            raise HTTPException(status_code=409, detail=f"a zone named '{body.name}' already exists")
        zone = body.to_zone(index=len(zones))
        save_zones(services.zones_file, [*zones, zone])
    _audit(services, admin, ip, LogAction.ZONE_ADDED, f"Created zone '{zone.name}'", points=len(zone.points))
    return {"success": True, "zone": zone.to_dict()}


@admin_router.put("/zones/{zone_name:path}", tags=["Zones"])
def update_zone(
    zone_name: str,
    body: ZoneUpdate,
    admin: TokenData = Depends(require_admin),
    services: Services = Depends(get_services),
    ip: str = Depends(client_ip),
) -> dict:
    changes = body.model_dump(exclude_unset=True)
    with services.zones_lock:
        zones = _load_zones_or_500(services)
        index = next((i for i, z in enumerate(zones) if z.name == zone_name), None)
        if index is None:
            raise HTTPException(status_code=404, detail="Zone not found")
        merged = {**zones[index].to_dict(), **{k: v for k, v in changes.items() if v is not None}}
        try:
            updated = Zone.from_dict(merged, index=index)
        except ZoneConfigError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if updated.name != zone_name and any(z.name == updated.name for z in zones):
            raise HTTPException(status_code=409, detail=f"a zone named '{updated.name}' already exists")
        zones[index] = updated
        save_zones(services.zones_file, zones)
    if updated.name != zone_name:
        services.config.rename_zone_threshold(zone_name, updated.name)
        threshold = services.state.get_zone_thresholds().get(zone_name)
        services.state.set_zone_threshold(zone_name, None)
        services.state.set_zone_threshold(updated.name, threshold)
    _audit(services, admin, ip, LogAction.ZONE_UPDATED, f"Updated zone '{zone_name}'", changes=list(changes))
    return {"success": True, "zone": updated.to_dict()}


@admin_router.delete("/zones/{zone_name:path}", tags=["Zones"])
def delete_zone(
    zone_name: str,
    admin: TokenData = Depends(require_admin),
    services: Services = Depends(get_services),
    ip: str = Depends(client_ip),
) -> dict:
    with services.zones_lock:
        zones = _load_zones_or_500(services)
        remaining = [z for z in zones if z.name != zone_name]
        if len(remaining) == len(zones):
            raise HTTPException(status_code=404, detail="Zone not found")
        save_zones(services.zones_file, remaining)
    services.config.rename_zone_threshold(zone_name, None)
    services.state.set_zone_threshold(zone_name, None)
    _audit(services, admin, ip, LogAction.ZONE_DELETED, f"Deleted zone '{zone_name}'")
    return {"success": True}


# ====================================================================== activity logs


@admin_router.get("/logs", tags=["Activity log"])
def get_logs(
    category: LogCategory | None = None,
    action: LogAction | None = None,
    user_id: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    services: Services = Depends(get_services),
) -> dict:
    logs, total = services.audit.query(
        category=category,
        action=action,
        user_id=user_id,
        start=start_date,
        end=end_date,
        limit=limit,
        offset=offset,
    )
    return {
        "logs": [e.model_dump(mode="json") for e in logs],
        "count": len(logs),
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@admin_router.get("/logs/export/csv", tags=["Activity log"])
def export_logs_csv(
    category: LogCategory | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    services: Services = Depends(get_services),
) -> Response:
    content = services.audit.export_csv(category=category, start=start_date, end=end_date)
    return Response(
        content=content,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="activity_log_{datetime.now():%Y%m%d_%H%M%S}.csv"'
        },
    )


@admin_router.get("/logs/export/pdf", tags=["Activity log"])
def export_logs_pdf(
    category: LogCategory | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    services: Services = Depends(get_services),
) -> Response:
    logs, total = services.audit.query(category=category, start=start_date, end=end_date, limit=500)
    return Response(
        content=activity_report(logs, total),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="activity_log_{datetime.now():%Y%m%d_%H%M%S}.pdf"'
        },
    )


@admin_router.post("/logs/cleanup", tags=["Activity log"])
def cleanup_logs(
    retention_days: int = Query(30, ge=1, le=3650),
    admin: TokenData = Depends(require_admin),
    services: Services = Depends(get_services),
    ip: str = Depends(client_ip),
) -> dict:
    removed = services.audit.cleanup(retention_days)
    _audit(
        services,
        admin,
        ip,
        LogAction.LOGS_CLEANED,
        f"Removed {removed} entries older than {retention_days} days",
    )
    return {"success": True, "deleted_count": removed, "retention_days": retention_days}


# ====================================================================== alert history


@admin_router.get("/alerts/history", tags=["Alerts"])
def alert_history(
    alert_type: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    limit: int = Query(100, ge=1, le=1000),
    services: Services = Depends(get_services),
) -> dict:
    records = services.audit.alerts(alert_type=alert_type, start=start_date, end=end_date, limit=limit)
    return {"alerts": [r.model_dump(mode="json") for r in records], "count": len(records)}


@admin_router.post("/alerts/{alert_id}/acknowledge", tags=["Alerts"])
def acknowledge_alert(
    alert_id: str,
    admin: TokenData = Depends(require_admin),
    services: Services = Depends(get_services),
    ip: str = Depends(client_ip),
) -> dict:
    record = services.audit.acknowledge(alert_id, admin.username)
    if record is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    services.audit.log(
        LogCategory.ALERT,
        LogAction.ALERT_ACKNOWLEDGED,
        user_id=admin.user_id,
        username=admin.username,
        ip_address=ip,
        details=f"Acknowledged {record.alert_type} alert ({record.actual_count} > {record.threshold})",
    )
    return {"success": True, "alert": record.model_dump(mode="json")}


# ====================================================================== system config


@admin_router.get("/config", tags=["Configuration"])
def get_config(services: Services = Depends(get_services)) -> dict:
    s = services.settings
    return {
        **services.config.get(),
        "runtime": {
            "zones_file": str(s.zones_file),
            "data_dir": str(s.data_dir),
            "model": s.model,
            "tracker": s.tracker,
            "device": s.device,
        },
    }


@admin_router.get("/config/retention", tags=["Configuration"])
def get_retention(services: Services = Depends(get_services)) -> dict:
    return {"log_retention_days": services.config.retention_days()}


@admin_router.put("/config/retention", tags=["Configuration"])
def set_retention(
    retention_days: int = Query(..., ge=1, le=3650),
    admin: TokenData = Depends(require_admin),
    services: Services = Depends(get_services),
    ip: str = Depends(client_ip),
) -> dict:
    """Entries older than this are removed at every server start."""
    services.config.set_retention_days(retention_days)
    _audit(services, admin, ip, LogAction.RETENTION_UPDATED, f"Set log retention to {retention_days} days")
    return {"success": True, "log_retention_days": retention_days}
