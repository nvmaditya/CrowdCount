"""Assets for ``crowdcount demo``: a public sample video and zones drawn for it.

The video is OpenCV's ``vtest.avi`` sample (768x576, 10 fps, pedestrians crossing a
campus plaza), downloaded on first use from a pinned OpenCV release and verified by
SHA-256. Nothing binary is committed to this repository.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import urllib.request
from pathlib import Path

from crowdcount.zones import Zone, load_zones, save_zones

logger = logging.getLogger(__name__)

DEMO_VIDEO_URL = "https://raw.githubusercontent.com/opencv/opencv/4.10.0/samples/data/vtest.avi"
DEMO_VIDEO_SHA256 = "45cddc9490be69345cbdab64ca583be65987e864ca408038e648db99e10516cf"

#: Settings tuned for the small, distant figures in the sample video.
DEMO_SETTINGS = {
    "model": "yolo26n.pt",
    "confidence": 0.35,
    "min_box_area": 800,
    "loop": True,
}

DEMO_ZONES = [
    Zone(
        "West walkway",
        [(0, 150), (330, 150), (440, 210), (440, 300), (300, 280), (0, 270)],
        (46, 196, 182),
    ),
    Zone(
        "East plaza",
        [(450, 140), (640, 110), (767, 110), (767, 420), (560, 360), (450, 300)],
        (245, 158, 11),
    ),
]


class DemoAssetError(RuntimeError):
    """Raised when the demo video cannot be downloaded or verified."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_demo_video(cache: Path) -> Path:
    """Return the cached demo video, downloading and verifying it if needed."""
    target = cache / "demo" / "vtest.avi"
    if target.is_file() and _sha256(target) == DEMO_VIDEO_SHA256:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading demo video (8 MB) from %s", DEMO_VIDEO_URL)
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, suffix=".part")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as out, urllib.request.urlopen(DEMO_VIDEO_URL, timeout=60) as resp:
            while chunk := resp.read(1 << 16):
                out.write(chunk)
        if _sha256(tmp) != DEMO_VIDEO_SHA256:
            raise DemoAssetError("downloaded demo video failed its checksum; try again")
        os.replace(tmp, target)
    except DemoAssetError:
        raise
    except OSError as exc:
        raise DemoAssetError(
            f"could not download the demo video ({exc}). Check your connection, "
            "or run `crowdcount run --source <your video>` instead."
        ) from exc
    finally:
        tmp.unlink(missing_ok=True)
    return target


def ensure_demo_zones(path: Path) -> Path:
    """Write the demo zones to ``path`` unless a valid file is already there.

    Existing files are left alone so zones you edit during the demo survive restarts.
    """
    if path.exists():
        load_zones(path)  # validate; raises ZoneConfigError with a clear message
        return path
    save_zones(path, DEMO_ZONES)
    logger.info("Wrote demo zones to %s", path)
    return path
