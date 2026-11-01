"""Shared services and FastAPI dependencies (auth, role checks, client IP)."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from crowdcount.config import Settings
from crowdcount.server.audit import AuditLog
from crowdcount.server.auth import TokenService, UserStore, load_or_create_secret
from crowdcount.server.config_store import ConfigStore
from crowdcount.server.models import TokenData, UserRole
from crowdcount.server.storage import JsonStore
from crowdcount.state import SharedState


@dataclass
class Services:
    """Everything request handlers need, built once per app."""

    settings: Settings
    state: SharedState
    users: UserStore
    tokens: TokenService
    audit: AuditLog
    config: ConfigStore
    trust_proxy: bool = False
    zones_lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def zones_file(self) -> Path:
        return self.settings.zones_file

    @classmethod
    def create(cls, settings: Settings, state: SharedState) -> Services:
        data = settings.data_dir
        return cls(
            settings=settings,
            state=state,
            users=UserStore(JsonStore(data / "users.json", lambda: {"users": []}, private=True)),
            tokens=TokenService(load_or_create_secret(data, os.environ)),
            audit=AuditLog(
                JsonStore(data / "activity_logs.json", lambda: {"logs": []}),
                JsonStore(data / "alerts_history.json", lambda: {"alerts": []}),
            ),
            config=ConfigStore(JsonStore(data / "config.json", dict)),
            trust_proxy=os.environ.get("CROWDCOUNT_TRUST_PROXY", "").lower() in {"1", "true", "yes"},
        )


def get_services(request: Request) -> Services:
    return request.app.state.services


_bearer = HTTPBearer(auto_error=False)


def bearer_token(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str | None:
    return credentials.credentials if credentials else None


def current_user(
    token: str | None = Depends(bearer_token), services: Services = Depends(get_services)
) -> TokenData:
    """The authenticated user; 401 if the token is missing, invalid, expired or revoked."""
    data = services.tokens.decode(token) if token else None
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return data


def require_admin(user: TokenData = Depends(current_user)) -> TokenData:
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")
    return user


def client_ip(request: Request, services: Services = Depends(get_services)) -> str:
    """Client address for the audit log. Proxy headers are honoured only when
    ``CROWDCOUNT_TRUST_PROXY`` is set, since clients can forge them."""
    if services.trust_proxy:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        if real_ip := request.headers.get("x-real-ip"):
            return real_ip
    return request.client.host if request.client else "unknown"
