"""Public dashboard API under ``/api``. Read endpoints need no login; actions do."""

from __future__ import annotations

import asyncio
import csv
import io
from collections.abc import AsyncIterator
from datetime import datetime

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import StreamingResponse

from crowdcount import __version__
from crowdcount.server.deps import Services, client_ip, current_user, get_services
from crowdcount.server.models import LogAction, LogCategory, TokenData
from crowdcount.server.reports import summary_report
from crowdcount.zones import ZoneConfigError, load_zones

router = APIRouter(prefix="/api", tags=["Dashboard"])

_NO_CACHE = {"Cache-Control": "no-store"}
_BOUNDARY = "frame"


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


@router.get("/health")
def health(services: Services = Depends(get_services)) -> dict:
    """Liveness probe; also reports whether a detector is publishing."""
    return {"status": "ok", "version": __version__, "detector": services.state.status().state}


@router.get("/summary")
def summary(services: Services = Depends(get_services)) -> dict:
    """Everything the dashboard renders: counts, zones, alerts, thresholds, detector status."""
    return {**services.state.summary(), "version": __version__}


@router.get("/count")
def count(services: Services = Depends(get_services)) -> dict:
    s = services.state
    return {
        "total_count": s.total_count,
        "timestamp": s.last_update,
        "detection_running": s.detection_running,
    }


@router.get("/zones")
def zones(services: Services = Depends(get_services)) -> dict:
    """Per-zone occupancy, unique track IDs seen, and threshold."""
    return {"zones": services.state.zone_stats(), "timestamp": services.state.last_update}


@router.get("/zones/layout")
def zones_layout(services: Services = Depends(get_services)) -> dict:
    """Zone definitions (name, RGB colour, enabled, polygon) from the watched zones file."""
    try:
        zones = load_zones(services.zones_file)
    except ZoneConfigError as exc:
        raise HTTPException(status_code=500, detail=f"zones file is invalid: {exc}") from exc
    return {"zones": [z.to_dict() for z in zones]}


@router.get("/history")
def history(limit: int = Query(300, ge=1, le=3600), services: Services = Depends(get_services)) -> dict:
    """One sample per second (the peak in that second), oldest first."""
    items = services.state.history(limit)
    return {"history": items, "count": len(items)}


@router.get("/alerts")
def alerts(services: Services = Depends(get_services)) -> dict:
    """Active alerts, near-limit and over-limit. ``has_alerts`` counts only over-limit ones. Reading this has no side effects."""
    s = services.state
    active = s.alerts()
    return {
        "alerts": active,
        "has_alerts": any(a["exceeded"] for a in active.values()),
        "global_threshold": s.get_global_threshold(),
        "total_count": s.total_count,
    }


@router.get("/coordinates")
def coordinates(services: Services = Depends(get_services)) -> dict:
    """Centre points of the people in the latest frame, in source pixels."""
    coords = services.state.coordinates()
    return {"coordinates": [{"x": x, "y": y} for x, y in coords], "count": len(coords)}


def _jpeg(data: bytes | None, missing: str) -> Response:
    if data is None:
        raise HTTPException(status_code=404, detail=missing)
    return Response(content=data, media_type="image/jpeg", headers=_NO_CACHE)


@router.get("/heatmap", responses={200: {"content": {"image/jpeg": {}}}})
def heatmap(services: Services = Depends(get_services)) -> Response:
    """Cumulative occupancy heatmap over the latest frame (JPEG)."""
    return _jpeg(services.state.heatmap_jpeg(), "No heatmap yet: the detector has not seen anyone.")


@router.get("/snapshot", responses={200: {"content": {"image/jpeg": {}}}})
def snapshot(services: Services = Depends(get_services)) -> Response:
    """Latest raw camera frame (JPEG), e.g. as a backdrop for drawing zones."""
    return _jpeg(services.state.snapshot_jpeg(), "No frame yet: start the detector first.")


@router.get("/stream", responses={200: {"content": {"multipart/x-mixed-replace": {}}}})
async def stream(request: Request, fps: float = Query(15, gt=0, le=30)) -> StreamingResponse:
    """Live annotated video as MJPEG, usable directly as an ``<img>`` source."""
    state = get_services(request).state

    async def frames() -> AsyncIterator[bytes]:
        last_seq = -1
        interval = 1.0 / fps
        while not await request.is_disconnected():
            item = await asyncio.to_thread(state.frame_jpeg)
            if item is not None and item[0] != last_seq:
                last_seq, jpeg = item
                yield (
                    f"--{_BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n".encode()
                    + jpeg
                    + b"\r\n"
                )
            await asyncio.sleep(interval)

    return StreamingResponse(
        frames(), media_type=f"multipart/x-mixed-replace; boundary={_BOUNDARY}", headers=_NO_CACHE
    )


@router.post("/heatmap/reset")
def reset_heatmap(
    user: TokenData = Depends(current_user),
    services: Services = Depends(get_services),
    ip: str = Depends(client_ip),
) -> dict:
    """Clear the heatmap. Any signed-in user may do this."""
    services.state.reset_heatmap()
    services.audit.log(
        LogCategory.CONFIG,
        LogAction.HEATMAP_RESET,
        user_id=user.user_id,
        username=user.username,
        ip_address=ip,
    )
    return {"success": True}


@router.get("/export/csv")
def export_csv(services: Services = Depends(get_services)) -> Response:
    """The last hour of per-second history as CSV."""
    rows = services.state.history(3600)
    if not rows:
        raise HTTPException(status_code=404, detail="No history to export yet")
    zones = sorted({z for r in rows for z in r["zone_counts"]})
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["timestamp", "total_count", *[f"zone_{z}" for z in zones]])
    for r in rows:
        writer.writerow([r["timestamp"], r["total_count"], *[r["zone_counts"].get(z, 0) for z in zones]])
    return Response(
        content=out.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="crowdcount_history_{_stamp()}.csv"'},
    )


@router.get("/export/pdf")
def export_pdf(services: Services = Depends(get_services)) -> Response:
    """A one-page occupancy report as PDF."""
    pdf = summary_report(services.state.summary(), services.state.history(3600))
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="crowdcount_report_{_stamp()}.pdf"'},
    )


@router.websocket("/ws")
async def websocket(ws: WebSocket) -> None:
    """Pushes the ``/api/summary`` payload once per second."""
    await ws.accept()
    state = ws.app.state.services.state
    try:
        while True:
            await ws.send_json({**state.summary(), "version": __version__})
            await asyncio.sleep(1)
    except (WebSocketDisconnect, RuntimeError):
        pass
