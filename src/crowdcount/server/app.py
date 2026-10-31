"""FastAPI application factory.

``create_app`` wires the dashboard API, auth/admin API and the static web UI around a
:class:`~crowdcount.state.SharedState`. Run it with ``crowdcount serve`` / ``crowdcount
run``, or directly: ``uvicorn --factory crowdcount.server.app:create_app``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from crowdcount import __version__
from crowdcount.config import Settings, load_settings
from crowdcount.server.admin import admin_router, auth_router
from crowdcount.server.api import router as api_router
from crowdcount.server.auth import INITIAL_ADMIN_USERNAME
from crowdcount.server.deps import Services
from crowdcount.state import SharedState, shared_state

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

# The UI ships its own assets and uses no inline scripts, so a strict policy fits.
_CSP = (
    "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; connect-src 'self'; font-src 'self'; object-src 'none'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)


def _startup(services: Services, public_url: str | None) -> None:
    state, config = services.state, services.config

    thresholds = config.thresholds()
    state.set_global_threshold(thresholds["global_threshold"])
    for zone, value in thresholds["zone_thresholds"].items():
        state.set_zone_threshold(zone, value)

    generated = services.users.ensure_admin(os.environ)
    if generated:
        where = f" at {public_url}login.html" if public_url else ""
        bar = "=" * 64
        logger.warning(
            "\n%s\n  Created the first admin account%s\n    username: %s\n    password: %s\n"
            "  This password is shown only once. Change it after signing in, or set\n"
            "  CROWDCOUNT_ADMIN_PASSWORD before the first start to choose your own.\n%s",
            bar,
            where,
            INITIAL_ADMIN_USERNAME,
            generated,
            bar,
        )

    removed = services.audit.cleanup(config.retention_days())
    if removed:
        logger.info("Removed %d log/alert entries older than %d days", removed, config.retention_days())


def create_app(
    settings: Settings | None = None,
    state: SharedState | None = None,
    *,
    public_url: str | None = None,
) -> FastAPI:
    """Build the app. Defaults: settings from ``load_settings()``, the process-wide state."""
    settings = settings or load_settings()
    services = Services.create(settings, state or shared_state)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        _startup(services, public_url)
        services.state.add_alert_listener(services.audit.record_alert)
        try:
            yield
        finally:
            services.state.remove_alert_listener(services.audit.record_alert)

    app = FastAPI(
        title="CrowdCount",
        version=__version__,
        description="Live occupancy, zones, alerts and administration for the CrowdCount detector.",
        lifespan=lifespan,
    )
    app.state.services = services

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if not request.url.path.startswith(("/docs", "/redoc")):
            response.headers.setdefault("Content-Security-Policy", _CSP)
        return response

    app.include_router(api_router)
    app.include_router(auth_router)
    app.include_router(admin_router)

    @app.get("/static/{path:path}", include_in_schema=False)
    def legacy_static(path: str) -> RedirectResponse:
        """CrowdCount 1.x served the UI under /static/; keep old bookmarks working."""
        return RedirectResponse(url=f"/{path}", status_code=301)

    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app
