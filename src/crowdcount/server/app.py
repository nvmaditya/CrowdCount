"""
FastAPI Backend Server
Provides REST API endpoints for the people detection dashboard.
Runs alongside the detection script and shares state via shared_state module.
"""

import sys
import os
from pathlib import Path

from fastapi import FastAPI, Response, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
from datetime import datetime
from typing import Optional
import io
import csv

# PDF generation
try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter, A4
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False
    print("Warning: reportlab not installed. PDF export will be disabled.")

try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False
    print("Warning: pandas not installed. Using basic CSV export.")

from crowdcount.state import shared_state

# Import admin router
from crowdcount.server.admin import router as admin_router, _load_config


# Create FastAPI app
app = FastAPI(
    title="People Detection API",
    description="Real-time people detection and zone monitoring API",
    version="1.0.0"
)

# Include admin router
app.include_router(admin_router)


@app.on_event("startup")
async def _startup_load_thresholds():
    """Load persisted thresholds into shared_state on API startup."""
    try:
        config = _load_config()
        thresholds = config.get("thresholds", {}) if isinstance(config, dict) else {}

        global_threshold = thresholds.get("global_threshold")
        if isinstance(global_threshold, int):
            shared_state.set_global_threshold(global_threshold)

        zone_thresholds = thresholds.get("zone_thresholds")
        if isinstance(zone_thresholds, dict):
            for zone_name, threshold in zone_thresholds.items():
                if isinstance(zone_name, str) and isinstance(threshold, int):
                    shared_state.set_zone_threshold(zone_name, threshold)
    except Exception as exc:
        print(f"Warning: failed to load thresholds from config.json: {exc}")

# Enable CORS for frontend access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================== API Endpoints ====================

@app.get("/")
async def root():
    """API root - health check and info"""
    return {
        "status": "running",
        "api": "People Detection API",
        "version": "1.0.0",
        "detection_running": shared_state.is_detection_running()
    }


@app.get("/count")
async def get_count():
    """
    Get current total people count.
    Returns JSON with total count and timestamp.
    """
    return {
        "total_count": shared_state.get_total_count(),
        "timestamp": shared_state.get_last_update().isoformat() if shared_state.get_last_update() else None,
        "detection_running": shared_state.is_detection_running()
    }


@app.get("/zones")
async def get_zones():
    """
    Get zone-wise people counts.
    Returns current count and total unique visitors per zone.
    """
    zone_data = shared_state.get_zone_counts()
    return {
        "zones": zone_data,
        "timestamp": shared_state.get_last_update().isoformat() if shared_state.get_last_update() else None
    }


@app.get("/history")
async def get_history(limit: int = Query(default=300, ge=1, le=3600)):
    """
    Get historical count data for charts.
    
    Args:
        limit: Number of history entries to return (default 300, max 3600)
    
    Returns timestamped count history for line charts.
    """
    history = shared_state.get_history(limit)
    return {
        "history": history,
        "count": len(history)
    }


@app.get("/heatmap")
async def get_heatmap():
    """
    Get heatmap image as PNG.
    Returns a color-coded density heatmap of person positions.
    """
    heatmap_bytes = shared_state.get_heatmap_image()
    
    if heatmap_bytes is None:
        return JSONResponse(
            status_code=404,
            content={"error": "Heatmap not available. Detection may not be running."}
        )
    
    return Response(
        content=heatmap_bytes,
        media_type="image/png",
        headers={"Cache-Control": "no-cache"}
    )


@app.get("/alerts")
async def get_alerts():
    """
    Get current alert status.
    Returns active alerts if any thresholds are exceeded.
    """
    alerts = shared_state.check_alerts_and_record()
    return {
        "alerts": alerts,
        "has_alerts": len(alerts) > 0,
        "global_threshold": shared_state.get_global_threshold(),
        "total_count": shared_state.get_total_count()
    }


@app.post("/alerts/threshold")
async def set_threshold(
    global_threshold: Optional[int] = Query(default=None, ge=1),
    zone_name: Optional[str] = None,
    zone_threshold: Optional[int] = Query(default=None, ge=1)
):
    """
    Set alert thresholds.
    
    Args:
        global_threshold: Set global crowd threshold
        zone_name: Zone name for zone-specific threshold
        zone_threshold: Threshold for the specified zone
    """
    if global_threshold is not None:
        shared_state.set_global_threshold(global_threshold)
    
    if zone_name and zone_threshold is not None:
        shared_state.set_zone_threshold(zone_name, zone_threshold)
    
    return {
        "success": True,
        "global_threshold": shared_state.get_global_threshold(),
        "message": "Thresholds updated"
    }


@app.get("/summary")
async def get_summary():
    """
    Get complete state summary.
    Useful for initial dashboard load.
    """
    return shared_state.get_summary()


@app.get("/coordinates")
async def get_coordinates():
    """
    Get current person coordinates.
    Useful for custom visualizations.
    """
    coords = shared_state.get_coordinates()
    return {
        "coordinates": [{"x": x, "y": y} for x, y in coords],
        "count": len(coords)
    }


@app.post("/heatmap/reset")
async def reset_heatmap():
    """Reset the heatmap accumulator"""
    shared_state.reset_heatmap()
    return {"success": True, "message": "Heatmap reset"}


@app.get("/export/csv")
async def export_csv():
    """
    Export history data as CSV file.
    Includes timestamp, total count, and zone-wise counts.
    """
    history = shared_state.get_history(limit=3600)  # Get all available history
    
    if not history:
        return JSONResponse(
            status_code=404,
            content={"error": "No history data available for export"}
        )
    
    # Create CSV in memory
    output = io.StringIO()
    
    # Get all zone names from history
    all_zones = set()
    for entry in history:
        all_zones.update(entry.get('zone_counts', {}).keys())
    zone_names = sorted(all_zones)
    
    # Create header
    fieldnames = ['timestamp', 'total_count'] + [f'zone_{z}' for z in zone_names]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    
    # Write data rows
    for entry in history:
        row = {
            'timestamp': entry['timestamp'],
            'total_count': entry['total_count']
        }
        zone_counts = entry.get('zone_counts', {})
        for zone in zone_names:
            row[f'zone_{zone}'] = zone_counts.get(zone, 0)
        writer.writerow(row)
    
    # Prepare response
    output.seek(0)
    filename = f"people_count_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        }
    )


@app.get("/export/pdf")
async def export_pdf():
    """
    Export summary report as PDF.
    Includes date, peak crowd, and zone summary.
    """
    if not REPORTLAB_AVAILABLE:
        return JSONResponse(
            status_code=501,
            content={"error": "PDF export not available. Install reportlab: pip install reportlab"}
        )
    
    history = shared_state.get_history(limit=3600)
    zone_data = shared_state.get_zone_counts()
    
    # Calculate statistics
    if history:
        peak_count = max(entry['total_count'] for entry in history)
        avg_count = sum(entry['total_count'] for entry in history) / len(history)
        first_timestamp = history[0]['timestamp'] if history else "N/A"
        last_timestamp = history[-1]['timestamp'] if history else "N/A"
    else:
        peak_count = shared_state.get_total_count()
        avg_count = peak_count
        first_timestamp = "N/A"
        last_timestamp = datetime.now().isoformat()
    
    # Create PDF in memory
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    elements = []
    styles = getSampleStyleSheet()
    
    # Title
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=24,
        spaceAfter=30,
        alignment=1  # Center
    )
    elements.append(Paragraph("People Detection Report", title_style))
    elements.append(Spacer(1, 20))
    
    # Report Info
    info_style = styles['Normal']
    elements.append(Paragraph(f"<b>Report Date:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", info_style))
    elements.append(Paragraph(f"<b>Data Period:</b> {first_timestamp} to {last_timestamp}", info_style))
    elements.append(Spacer(1, 20))
    
    # Summary Statistics
    elements.append(Paragraph("Summary Statistics", styles['Heading2']))
    summary_data = [
        ["Metric", "Value"],
        ["Current Count", str(shared_state.get_total_count())],
        ["Peak Count", str(peak_count)],
        ["Average Count", f"{avg_count:.1f}"],
        ["Data Points", str(len(history))],
        ["Global Threshold", str(shared_state.get_global_threshold())]
    ]
    
    summary_table = Table(summary_data, colWidths=[3*inch, 2*inch])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 1, colors.black)
    ]))
    elements.append(summary_table)
    elements.append(Spacer(1, 30))
    
    # Zone Summary
    if zone_data:
        elements.append(Paragraph("Zone Summary", styles['Heading2']))
        zone_table_data = [["Zone Name", "Current Count", "Total Visitors"]]
        
        for zone_name, data in zone_data.items():
            zone_table_data.append([
                zone_name,
                str(data.get('current', 0)),
                str(data.get('total_visitors', 0))
            ])
        
        zone_table = Table(zone_table_data, colWidths=[2*inch, 1.5*inch, 1.5*inch])
        zone_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.darkgreen),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.lightgreen),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        elements.append(zone_table)
    
    # Build PDF
    doc.build(elements)
    buffer.seek(0)
    
    filename = f"people_detection_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    
    return Response(
        content=buffer.getvalue(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        }
    )


# ==================== WebSocket for Real-Time Updates (Bonus) ====================

from fastapi import WebSocket, WebSocketDisconnect
from typing import List
import asyncio
import json


class ConnectionManager:
    """Manage WebSocket connections"""
    def __init__(self):
        self.active_connections: List[WebSocket] = []
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
    
    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
    
    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except:
                pass


manager = ConnectionManager()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time updates.
    Streams count and zone data every second.
    """
    await manager.connect(websocket)
    try:
        while True:
            # Send current state
            data = {
                "total_count": shared_state.get_total_count(),
                "zones": shared_state.get_zone_counts(),
                "alerts": shared_state.check_alerts(),
                "timestamp": shared_state.get_last_update().isoformat() if shared_state.get_last_update() else None
            }
            await websocket.send_json(data)
            await asyncio.sleep(1)  # Update every second
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# ==================== Static Files for Frontend ====================

# Mount frontend directory
frontend_path = Path(__file__).parent.parent / "web"
if frontend_path.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_path)), name="static")


# ==================== Server Runner ====================

def run_server(host: str = "0.0.0.0", port: int = 8080):
    """Run the FastAPI server"""
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='People Detection API Server')
    parser.add_argument('--host', type=str, default='0.0.0.0', help='Host to bind to')
    parser.add_argument('--port', type=int, default=8080, help='Port to bind to')
    
    args = parser.parse_args()
    run_server(host=args.host, port=args.port)
