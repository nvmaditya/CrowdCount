"""Generate paper-ready architecture/flow diagrams as SVG.

These diagrams are meant to be reproducible and code-anchored, without requiring
external diagram toolchains (Graphviz/Mermaid CLIs).

Outputs:
- paper/figures/system_architecture.svg
- paper/figures/frame_processing_flowchart.svg

Run via:
  python paper/generate_figures.py
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _write_svg(out_svg: Path, lines: Iterable[str]) -> None:
    _ensure_dir(out_svg.parent)
    with open(out_svg, "w", encoding="utf-8", newline="\n") as f:
        for line in lines:
            f.write(line)
            f.write("\n")


def _svg_header(width: int, height: int) -> list[str]:
    return [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        "<defs>",
        '<marker id="arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">',
        '<path d="M 0 0 L 10 5 L 0 10 z" fill="#111111" />',
        "</marker>",
        "<style>",
        "text { font-family: Arial, Helvetica, sans-serif; fill: #111111; }",
        ".title { font-size: 22px; font-weight: 700; }",
        ".boxtitle { font-size: 18px; font-weight: 700; }",
        ".boxtext { font-size: 15px; }",
        ".small { font-size: 13px; }",
        ".box { fill: #ffffff; stroke: #111111; stroke-width: 2; }",
        ".group { fill: #f7f7f7; stroke: #444444; stroke-width: 2; }",
        ".arrow { stroke: #111111; stroke-width: 2; fill: none; }",
        ".dashed { stroke-dasharray: 6 4; }",
        "</style>",
        "</defs>",
    ]


def _svg_footer() -> list[str]:
    return ["</svg>"]


def _svg_rect(cls: str, x: int, y: int, w: int, h: int, rx: int = 12) -> str:
    return f'<rect class="{cls}" x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" ry="{rx}" />'


def _svg_text(x: int, y: int, text: str, cls: str, anchor: str = "middle") -> str:
    return f'<text class="{cls}" text-anchor="{anchor}" x="{x}" y="{y}">{_xml_escape(text)}</text>'


def _svg_box(x: int, y: int, w: int, h: int, title: str, lines: Sequence[str]) -> list[str]:
    out: list[str] = []
    out.append(_svg_rect("box", x, y, w, h, rx=14))
    cx = x + w // 2
    out.append(_svg_text(cx, y + 28, title, "boxtitle"))
    ty = y + 52
    for line in lines:
        out.append(_svg_text(cx, ty, line, "boxtext"))
        ty += 20
    return out


def _svg_group_box(x: int, y: int, w: int, h: int, title: str, lines: Sequence[str]) -> list[str]:
    out: list[str] = []
    out.append(_svg_rect("group", x, y, w, h, rx=18))
    cx = x + w // 2
    out.append(_svg_text(cx, y + 32, title, "boxtitle"))
    ty = y + 60
    for line in lines:
        out.append(_svg_text(cx, ty, line, "boxtext"))
        ty += 20
    return out


def _svg_arrow_line(x1: int, y1: int, x2: int, y2: int, dashed: bool = False) -> str:
    cls = "arrow dashed" if dashed else "arrow"
    return f'<line class="{cls}" x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" marker-end="url(#arrow)" />'


def _svg_arrow_path(points: Sequence[tuple[int, int]], dashed: bool = False) -> str:
    cls = "arrow dashed" if dashed else "arrow"
    d = " ".join([f"{'M' if i == 0 else 'L'} {x} {y}" for i, (x, y) in enumerate(points)])
    return f'<path class="{cls}" d="{d}" marker-end="url(#arrow)" />'


def write_system_architecture_svg(out_svg: Path) -> None:
    width, height = 1800, 760
    svg: list[str] = []
    svg.extend(_svg_header(width, height))
    svg.append(_svg_text(30, 42, "System Architecture (Runtime Data Flow)", "title", anchor="start"))
    svg.append(
        _svg_text(
            30,
            68,
            "CrowdCount 2.x: pipeline → SharedState → FastAPI → web UI (one process)",
            "small",
            anchor="start",
        )
    )

    # Main pipeline blocks
    video = (60, 140, 220, 90)
    opencv = (320, 140, 220, 90)
    detector = (580, 110, 320, 150)
    state = (940, 140, 220, 90)
    api = (1200, 135, 280, 105)
    dashboard = (1520, 90, 240, 90)
    adminui = (1520, 220, 240, 90)

    svg.extend(_svg_box(*video, "Video Source", ["webcam / file / RTSP"]))
    svg.extend(_svg_box(*opencv, "VideoSource", ["cv2.VideoCapture", "loop / reconnect"]))
    svg.extend(
        _svg_box(
            *detector,
            "Pipeline + PersonDetector",
            [
                "YOLOv8 track + ByteTrack",
                "filters + ZoneTracker",
                "trails + overlays + metrics",
            ],
        )
    )
    svg.extend(_svg_box(*state, "SharedState", ["counts + 1 Hz history", "heatmap + frames + alerts"]))
    svg.extend(
        _svg_box(
            *api,
            "FastAPI Backend",
            ["/api/summary /api/history", "/api/stream (MJPEG)", "/api/admin/* + exports"],
        )
    )
    svg.extend(_svg_box(*dashboard, "Dashboard UI", ["1 s polling + Chart.js", "live MJPEG + heatmap"]))
    svg.extend(_svg_box(*adminui, "Admin UI", ["Bearer JWT", "draw zones, thresholds, users"]))

    # Persistence block
    persist = (580, 470, 1180, 200)
    svg.extend(
        _svg_group_box(
            *persist,
            "JSON Persistence (data dir)",
            [
                "zones.json",
                "data/config.json + data/users.json",
                "data/activity_logs.json + data/alerts_history.json",
            ],
        )
    )

    # Helpers
    def right_mid(b: tuple[int, int, int, int]) -> tuple[int, int]:
        x, y, w, h = b
        return (x + w, y + h // 2)

    def left_mid(b: tuple[int, int, int, int]) -> tuple[int, int]:
        x, y, _, h = b
        return (x, y + h // 2)

    def bottom_mid(b: tuple[int, int, int, int]) -> tuple[int, int]:
        x, y, w, h = b
        return (x + w // 2, y + h)

    def top_mid(b: tuple[int, int, int, int]) -> tuple[int, int]:
        x, y, w, _ = b
        return (x + w // 2, y)

    # Video -> OpenCV -> Detector -> SharedState
    x1, y1 = right_mid(video)
    x2, y2 = left_mid(opencv)
    svg.append(_svg_arrow_line(x1, y1, x2, y2))
    svg.append(_svg_text((x1 + x2) // 2, y1 - 10, "frames", "small"))

    x1, y1 = right_mid(opencv)
    x2, y2 = left_mid(detector)
    svg.append(_svg_arrow_line(x1, y1, x2, y2))
    svg.append(_svg_text((x1 + x2) // 2, y1 - 10, "sampled frames", "small"))

    x1, y1 = right_mid(detector)
    x2, y2 = left_mid(state)
    svg.append(_svg_arrow_line(x1, y1, x2, y2))
    svg.append(_svg_text((x1 + x2) // 2, y1 - 10, "publish()", "small"))

    # API reads SharedState
    x1, y1 = left_mid(api)
    x2, y2 = right_mid(state)
    svg.append(_svg_arrow_line(x1, y1, x2, y2))
    svg.append(_svg_text((x1 + x2) // 2, y1 - 10, "read snapshot", "small"))

    # Dashboard/Admin -> API
    x1, y1 = left_mid(dashboard)
    x2, y2 = right_mid(api)
    svg.append(_svg_arrow_line(x1, y1, x2, y2))
    svg.append(_svg_text((x1 + x2) // 2, y1 - 10, "poll REST", "small"))

    x1, y1 = left_mid(adminui)
    x2, y2 = right_mid(api)
    svg.append(_svg_arrow_line(x1, y1, x2, y2))
    svg.append(_svg_text((x1 + x2) // 2, y1 - 10, "admin REST", "small"))

    # API -> persistence (read/write)
    x1, y1 = bottom_mid(api)
    x2, y2 = top_mid(persist)
    svg.append(_svg_arrow_line(x1, y1, x2, y2))
    svg.append(_svg_text(x1, (y1 + y2) // 2 - 10, "read/write", "small"))

    # Detector -> persistence (zones load)
    x1, y1 = bottom_mid(detector)
    x2, y2 = top_mid(persist)
    svg.append(_svg_arrow_line(x1, y1, x2, y2, dashed=True))
    svg.append(_svg_text(x1, (y1 + y2) // 2 - 10, "load zones", "small"))

    svg.extend(_svg_footer())
    _write_svg(out_svg, svg)


def write_frame_processing_flowchart_svg(out_svg: Path) -> None:
    width, height = 1400, 1500
    svg: list[str] = []
    svg.extend(_svg_header(width, height))
    svg.append(_svg_text(30, 42, "Frame Processing Flow (Detector Loop)", "title", anchor="start"))
    svg.append(
        _svg_text(
            30,
            68,
            "CrowdCount 2.x: crowdcount/pipeline.py Pipeline.run()",
            "small",
            anchor="start",
        )
    )

    x, w = 250, 900

    steps = [
        (100, 110, "Initialize", ["validate zones, open source, load YOLO"]),
        (240, 90, "Read frame", ["VideoSource.read(); frames_read += 1"]),
        (
            360,
            120,
            "Handle end-of-video",
            ["file: loop (--loop) or stop", "stream: reconnect with backoff"],
        ),
        (
            520,
            120,
            "Stride, pacing, zone reload",
            ["process every Nth frame (--stride)", "reload zones.json on change"],
        ),
        (
            680,
            120,
            "YOLOv8 tracking",
            ["model.track(..., persist=True,", "classes=[0], tracker='bytetrack.yaml')"],
        ),
        (
            840,
            120,
            "Parse + filter detections",
            ["boxes/id/conf extraction", "filter by conf, min area, aspect ratio"],
        ),
        (
            1000,
            120,
            "Zone membership + trails",
            ["box centre", "point-in-polygon (edges inclusive)", "trails capped at 30"],
        ),
        (
            1160,
            120,
            "Update stats + publish",
            [
                "occupancy + unique track IDs",
                "SharedState.publish()",
                "1 Hz history, heatmap, alerts",
            ],
        ),
        (
            1320,
            120,
            "Render (optional) + quit handling",
            ["draw boxes/zones/trails", "MJPEG frame; window if --display", "stop on q / Ctrl+C"],
        ),
    ]

    for y, h, title, lines in steps:
        svg.extend(_svg_box(x, y, w, h, title, lines))

    def box_bottom(y: int, h: int) -> int:
        return y + h

    for i in range(len(steps) - 1):
        y, h, _, _ = steps[i]
        y2, _, _, _ = steps[i + 1]
        xmid = x + w // 2
        svg.append(_svg_arrow_line(xmid, box_bottom(y, h), xmid, y2))

    # Loop-back arrow (last step back to "Read frame")
    last_y, last_h, _, _ = steps[-1]
    read_y, read_h, _, _ = steps[1]
    xmid = x + w // 2
    loop_points = [
        (xmid, last_y + last_h),
        (xmid, last_y + last_h + 40),
        (120, last_y + last_h + 40),
        (120, read_y + read_h // 2),
        (x, read_y + read_h // 2),
    ]
    svg.append(_svg_arrow_path(loop_points))
    svg.append(_svg_text(120, read_y + read_h // 2 - 10, "repeat", "small", anchor="start"))

    svg.extend(_svg_footer())
    _write_svg(out_svg, svg)
