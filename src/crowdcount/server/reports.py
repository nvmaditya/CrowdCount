"""PDF reports (ReportLab): the occupancy summary and the activity log export."""

from __future__ import annotations

import io
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from crowdcount import __version__
from crowdcount.server.models import ActivityLog

INK = colors.HexColor("#0f172a")
MUTED = colors.HexColor("#64748b")
ACCENT = colors.HexColor("#14b8a6")
STRIPE = colors.HexColor("#f1f5f9")
GRID = colors.HexColor("#e2e8f0")


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "t", parent=base["Title"], textColor=INK, alignment=0, fontSize=20, spaceAfter=2
        ),
        "sub": ParagraphStyle("s", parent=base["Normal"], textColor=MUTED, fontSize=9, spaceAfter=10),
        "h2": ParagraphStyle("h", parent=base["Heading2"], textColor=INK, fontSize=12, spaceBefore=10),
        "cell": ParagraphStyle("c", parent=base["Normal"], fontSize=8, leading=10, textColor=INK),
    }


def _table(rows: Sequence[Sequence[Any]], widths: Sequence[float]) -> Table:
    table = Table(rows, colWidths=widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), INK),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, STRIPE]),
                ("LINEBELOW", (0, 0), (-1, -1), 0.4, GRID),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def _header(story: list[Any], title: str, subtitle: str) -> None:
    s = _styles()
    story.append(Paragraph(title, s["title"]))
    story.append(Paragraph(subtitle, s["sub"]))


def summary_report(summary: dict[str, Any], history: list[dict[str, Any]]) -> bytes:
    """Occupancy report: current and peak counts, zone table, thresholds."""
    s = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm)
    story: list[Any] = []
    now = datetime.now().astimezone()
    period = f"{history[0]['timestamp']} to {history[-1]['timestamp']}" if history else "no samples recorded"
    _header(
        story, "CrowdCount occupancy report", f"Generated {now:%Y-%m-%d %H:%M %Z} · CrowdCount {__version__}"
    )

    status = summary.get("status", {})
    avg = sum(h["total_count"] for h in history) / len(history) if history else 0.0
    story.append(Paragraph("Overview", s["h2"]))
    overview = [
        ["Metric", "Value"],
        ["Source", status.get("source") or "-"],
        ["Detector state", status.get("state", "-")],
        ["Sampling period", period],
        ["People now", str(summary.get("total_count", 0))],
        ["Peak (this run)", f"{summary.get('peak_count', 0)} at {summary.get('peak_at') or '-'}"],
        ["Average (per-second peaks)", f"{avg:.1f}"],
        ["Samples (1 per second)", str(len(history))],
        ["Global alert threshold", str(summary.get("global_threshold", "-"))],
    ]
    story.append(_table(overview, [60 * mm, 110 * mm]))

    zones = summary.get("zones") or {}
    story.append(Paragraph("Zones", s["h2"]))
    if zones:
        peaks: dict[str, int] = {}
        for h in history:
            for name, count in h.get("zone_counts", {}).items():
                peaks[name] = max(peaks.get(name, 0), count)
        rows = [["Zone", "Now", "Peak", "Unique tracks", "Threshold"]]
        for name, z in zones.items():
            rows.append(
                [
                    Paragraph(name, s["cell"]),
                    z.get("current", 0),
                    peaks.get(name, 0),
                    z.get("total_visitors", 0),
                    z.get("threshold") or "-",
                ]
            )
        story.append(_table(rows, [62 * mm, 22 * mm, 22 * mm, 34 * mm, 30 * mm]))
    else:
        story.append(Paragraph("No zones are defined.", s["sub"]))

    story.append(Spacer(1, 8))
    story.append(
        Paragraph(
            "Unique tracks counts distinct tracker IDs; a person who is lost and re-acquired "
            "is counted again, so it is an upper bound on unique people.",
            s["sub"],
        )
    )
    doc.build(story)
    return buf.getvalue()


def activity_report(logs: Sequence[ActivityLog], total: int) -> bytes:
    """Activity log table (newest first, at most the first 500 rows)."""
    s = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4), leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm
    )
    story: list[Any] = []
    shown = list(logs)[:500]
    _header(
        story,
        "CrowdCount activity log",
        f"Generated {datetime.now().astimezone():%Y-%m-%d %H:%M %Z} · showing {len(shown)} of {total} entries",
    )
    if not shown:
        story.append(Paragraph("No log entries match the selected filters.", s["sub"]))
    else:
        rows: list[list[Any]] = [["Time", "Category", "Action", "User", "IP", "Details"]]
        for e in shown:
            rows.append(
                [
                    e.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    e.category.value,
                    e.action.value.replace("_", " ").title(),
                    e.username or "-",
                    e.ip_address or "-",
                    Paragraph(e.details or "-", s["cell"]),
                ]
            )
        story.append(_table(rows, [36 * mm, 22 * mm, 40 * mm, 28 * mm, 30 * mm, 112 * mm]))
    doc.build(story)
    return buf.getvalue()
