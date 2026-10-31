"""Small JSON document stores with atomic writes.

Each store guards its file with a lock held across read-modify-write, and writes via a
temporary file plus ``os.replace`` so a crash never leaves a truncated document. A file
that fails to parse is moved aside (``name.corrupt-<timestamp>.json``) and logged rather
than silently treated as empty.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def atomic_write_text(path: Path, text: str, *, mode: int | None = None) -> None:
    """Write ``text`` to ``path`` atomically, optionally setting POSIX permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class JsonStore:
    """A JSON document on disk.

    Args:
        path: file location; parent directories are created on first write.
        default: factory for the document when the file does not exist yet.
        private: restrict the file to its owner (used for password hashes).
    """

    def __init__(self, path: Path, default: Callable[[], Any], *, private: bool = False) -> None:
        self.path = Path(path)
        self._default = default
        self._mode = 0o600 if private else None
        self._lock = threading.RLock()

    def read(self) -> Any:
        with self._lock:
            return self._read()

    def write(self, data: Any) -> None:
        with self._lock:
            atomic_write_text(self.path, json.dumps(data, indent=2, default=str) + "\n", mode=self._mode)

    def update(self, fn: Callable[[Any], T]) -> T:
        """Apply ``fn`` to the document in place and save it; returns ``fn``'s result."""
        with self._lock:
            data = self._read()
            result = fn(data)
            self.write(data)
            return result

    def _read(self) -> Any:
        if not self.path.exists():
            return self._default()
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            backup = self.path.with_name(f"{self.path.stem}.corrupt-{int(time.time())}{self.path.suffix}")
            os.replace(self.path, backup)
            logger.error(
                "%s was unreadable (%s); moved it to %s and started fresh", self.path, exc, backup.name
            )
            return self._default()
