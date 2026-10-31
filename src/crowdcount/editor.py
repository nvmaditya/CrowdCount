"""Interactive OpenCV zone editor (``crowdcount zones edit``).

Draw polygons on frames from a video file or stream. Changes are saved to the zones file,
which a running detector picks up automatically. The admin panel offers the same in a
browser.
"""

from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np

from crowdcount.annotate import draw_hud, draw_zones, label, to_bgr
from crowdcount.video import VideoSource
from crowdcount.zones import ZONE_PALETTE, Zone, ZoneStats, load_zones, save_zones

logger = logging.getLogger(__name__)

WINDOW_TITLE = "CrowdCount zone editor"
HELP = [
    "Left click: add point   Right click: finish zone   Middle click / u: undo point",
    "s: save   c: clear points   d: delete zone   t: toggle zone   l: list   q: quit",
]
_DRAFT_COLOR = (0, 220, 255)


class ZoneEditor:
    """State and event handling for the OpenCV zone editor."""

    def __init__(self, zones_file: Path) -> None:
        self.zones_file = Path(zones_file)
        self.zones: list[Zone] = load_zones(self.zones_file)
        self.draft: list[tuple[int, int]] = []
        self.dirty = False

    # -- editing ---------------------------------------------------------------

    def add_point(self, x: int, y: int) -> None:
        self.draft.append((x, y))

    def undo_point(self) -> None:
        if self.draft:
            self.draft.pop()

    def finish_zone(self, name: str | None = None) -> Zone | None:
        """Turn the draft into a zone. Returns ``None`` if it has fewer than 3 points."""
        if len(self.draft) < 3:
            logger.warning("A zone needs at least 3 points (have %d)", len(self.draft))
            return None
        existing = {z.name for z in self.zones}
        name = (name or "").strip() or self._default_name(existing)
        if name in existing:
            logger.warning("Zone '%s' already exists; pick another name", name)
            return None
        zone = Zone(name, list(self.draft), ZONE_PALETTE[len(self.zones) % len(ZONE_PALETTE)])
        self.zones.append(zone)
        self.draft.clear()
        self.dirty = True
        logger.info("Created zone '%s' with %d points", zone.name, len(zone.points))
        return zone

    def delete_zone(self, index: int) -> None:
        if 0 <= index < len(self.zones):
            logger.info("Deleted zone '%s'", self.zones.pop(index).name)
            self.dirty = True
        else:
            logger.warning("No zone with index %d", index)

    def toggle_zone(self, index: int) -> None:
        if 0 <= index < len(self.zones):
            zone = self.zones[index]
            zone.enabled = not zone.enabled
            self.dirty = True
            logger.info("Zone '%s' %s", zone.name, "enabled" if zone.enabled else "disabled")
        else:
            logger.warning("No zone with index %d", index)

    def save(self) -> None:
        save_zones(self.zones_file, self.zones)
        self.dirty = False
        logger.info("Saved %d zone(s) to %s", len(self.zones), self.zones_file)

    @staticmethod
    def _default_name(existing: set[str]) -> str:
        i = 1
        while f"Zone {i}" in existing:
            i += 1
        return f"Zone {i}"

    # -- UI ----------------------------------------------------------------------

    def render(self, frame: np.ndarray) -> np.ndarray:
        out = frame.copy()
        draw_zones(out, self.zones, {z.name: ZoneStats(0, 0) for z in self.zones}, alpha=0.25)
        for i, zone in enumerate(self.zones):
            if not zone.enabled:
                pts = np.array(zone.points, np.int32)
                cv2.polylines(out, [pts], True, (128, 128, 128), 1, cv2.LINE_AA)
                x, y, _, _ = zone.bounds
                label(out, f"[{i}] {zone.name} (disabled)", (x + 4, y + 24), (90, 90, 90))
        if self.draft:
            pts = np.array(self.draft, np.int32)
            cv2.polylines(out, [pts], len(self.draft) > 2, _DRAFT_COLOR, 2, cv2.LINE_AA)
            for p in self.draft:
                cv2.circle(out, p, 4, _DRAFT_COLOR, -1, cv2.LINE_AA)
        status = f"{len(self.zones)} zone(s)  draft: {len(self.draft)} point(s)" + (
            "  [unsaved]" if self.dirty else ""
        )
        draw_hud(out, [status, *HELP])
        return out

    def on_mouse(self, event: int, x: int, y: int, _flags: int, _param: object) -> None:
        if event == cv2.EVENT_LBUTTONDOWN:
            self.add_point(x, y)
        elif event == cv2.EVENT_RBUTTONDOWN:
            if len(self.draft) >= 3:
                self.finish_zone(_prompt("Zone name (Enter for default): "))
            else:
                logger.warning("A zone needs at least 3 points")
        elif event == cv2.EVENT_MBUTTONDOWN:
            self.undo_point()

    def list_zones(self) -> None:
        if not self.zones:
            logger.info("No zones defined")
        for i, z in enumerate(self.zones):
            logger.info(
                "[%d] %s: %d points, %s, colour %s",
                i,
                z.name,
                len(z.points),
                "enabled" if z.enabled else "disabled",
                to_bgr(z.color)[::-1],
            )


def _prompt(text: str) -> str:
    try:
        return input(text)
    except (EOFError, KeyboardInterrupt):
        return ""


def _prompt_index(text: str) -> int | None:
    raw = _prompt(text).strip()
    return int(raw) if raw.lstrip("-").isdigit() else None


def run_editor(source: VideoSource, zones_file: Path) -> None:
    """Open the editor window on ``source`` (looped) until the user quits."""
    editor = ZoneEditor(zones_file)
    source.loop = True
    source.open()
    logger.info(
        "Editing %s on %s (%dx%d). Close with q.", zones_file, source.label, source.width, source.height
    )
    cv2.namedWindow(WINDOW_TITLE, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WINDOW_TITLE, source.width, source.height)
    cv2.setMouseCallback(WINDOW_TITLE, editor.on_mouse)
    frame = source.read()
    try:
        while frame is not None:
            # Streams stay live; files show their first frame as a still backdrop.
            if source.live and (latest := source.read()) is not None:
                frame = latest
            cv2.imshow(WINDOW_TITLE, editor.render(frame))
            key = cv2.waitKey(30) & 0xFF
            if key in (ord("q"), 27):
                if (
                    editor.dirty
                    and _prompt("Unsaved changes. Save before quitting? [Y/n] ").strip().lower() != "n"
                ):
                    editor.save()
                break
            if key == ord("s"):
                editor.save()
            elif key == ord("c"):
                editor.draft.clear()
            elif key == ord("u"):
                editor.undo_point()
            elif key == ord("l"):
                editor.list_zones()
            elif key == ord("d"):
                editor.list_zones()
                if (idx := _prompt_index("Delete zone index: ")) is not None:
                    editor.delete_zone(idx)
            elif key == ord("t"):
                editor.list_zones()
                if (idx := _prompt_index("Toggle zone index: ")) is not None:
                    editor.toggle_zone(idx)
    finally:
        source.release()
        cv2.destroyAllWindows()
        cv2.waitKey(1)
