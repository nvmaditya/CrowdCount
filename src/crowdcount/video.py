"""Video input: video files and network streams, with readable errors."""

from __future__ import annotations

import logging
import math
import time
from pathlib import Path
from urllib.parse import urlparse

import cv2
import numpy as np

logger = logging.getLogger(__name__)

_STREAM_SCHEMES = {"rtsp", "rtsps", "rtmp", "http", "https", "udp", "tcp"}


class VideoSourceError(RuntimeError):
    """Raised when a video source cannot be opened."""


def is_stream(source: str) -> bool:
    """True for network streams (sources that never reach a real end)."""
    return urlparse(source).scheme.lower() in _STREAM_SCHEMES


def describe_source(source: str) -> str:
    """Short human-readable label for logs and the dashboard."""
    parsed = urlparse(source)
    if parsed.scheme.lower() in _STREAM_SCHEMES:
        host = parsed.hostname or ""
        return f"{parsed.scheme}://{host}{parsed.path}"  # drop any credentials in the URL
    return Path(source).name


class VideoSource:
    """A frame reader that can loop files and reconnect dropped streams.

    Args:
        source: video file path or stream URL (``rtsp://``, ``http://``, ...).
        loop: restart files from the beginning at the end.
        reconnect_attempts: retries before giving up on a dropped stream.
    """

    def __init__(self, source: str, *, loop: bool = False, reconnect_attempts: int = 5) -> None:
        self.source = str(source).strip()
        if not self.source:
            raise VideoSourceError("no video source given: pass a video file or stream URL")
        self.loop = loop
        self.reconnect_attempts = reconnect_attempts
        self.label = describe_source(self.source)
        self._cap: cv2.VideoCapture | None = None
        self._first_frame: np.ndarray | None = None
        self.width = self.height = 0
        self.fps = 30.0

    @property
    def live(self) -> bool:
        return is_stream(self.source)

    @property
    def opened(self) -> bool:
        return self._cap is not None

    def open(self) -> VideoSource:
        """Open the source, raising :class:`VideoSourceError` with a hint on failure."""
        if not self.live and not Path(self.source).is_file():
            raise VideoSourceError(f"video file not found: {self.source}")
        self._cap = cv2.VideoCapture(self.source)
        if not self._cap.isOpened():
            self._cap.release()
            raise VideoSourceError(self._open_error())
        ok, frame = self._cap.read()
        if not ok or frame is None:
            self._cap.release()
            raise VideoSourceError(f"{self.label} opened but returned no frames")
        self.height, self.width = frame.shape[:2]
        fps = self._cap.get(cv2.CAP_PROP_FPS)
        self.fps = fps if fps and math.isfinite(fps) and 1 <= fps <= 240 else 30.0
        self._first_frame = frame
        logger.info("Opened %s (%dx%d @ %.1f fps)", self.label, self.width, self.height, self.fps)
        return self

    def _open_error(self) -> str:
        if self.live:
            return f"could not connect to stream {self.label}. Check the URL, credentials and network."
        return f"OpenCV could not decode {self.source}. Is it a supported video format?"

    def read(self) -> np.ndarray | None:
        """Return the next frame, or ``None`` when the source is exhausted."""
        if self._cap is None:
            raise RuntimeError("VideoSource.read() called before open()")
        if self._first_frame is not None:
            frame, self._first_frame = self._first_frame, None
            return frame
        ok, frame = self._cap.read()
        if ok and frame is not None:
            return frame
        if not self.live:
            if not self.loop:
                return None
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = self._cap.read()
            return frame if ok else None
        return self._reconnect()

    def _reconnect(self) -> np.ndarray | None:
        for attempt in range(1, self.reconnect_attempts + 1):
            delay = min(2**attempt, 30)
            logger.warning(
                "Lost %s; reconnecting in %ds (attempt %d/%d)",
                self.label,
                delay,
                attempt,
                self.reconnect_attempts,
            )
            time.sleep(delay)
            assert self._cap is not None
            self._cap.release()
            self._cap = cv2.VideoCapture(self.source)
            if self._cap.isOpened():
                ok, frame = self._cap.read()
                if ok and frame is not None:
                    logger.info("Reconnected to %s", self.label)
                    return frame
        logger.error("Giving up on %s after %d attempts", self.label, self.reconnect_attempts)
        return None

    def release(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def __enter__(self) -> VideoSource:
        return self.open() if self._cap is None else self

    def __exit__(self, *exc: object) -> None:
        self.release()
