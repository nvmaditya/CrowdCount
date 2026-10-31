"""Person detection and multi-object tracking with Ultralytics YOLO.

Tracking uses the trackers bundled with Ultralytics: ByteTrack (default) or BoT-SORT.
Ultralytics and PyTorch are imported lazily so the rest of the package (zones, state,
server) can be used and tested without loading them.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)

PERSON_CLASS_ID = 0

#: Short tracker names accepted on the command line.
TRACKERS = {"bytetrack": "bytetrack.yaml", "botsort": "botsort.yaml"}

#: Ultralytics downloads weights with these name prefixes on first use (yolov8n.pt, yolo11s.pt,
#: yolo26m.pt, rtdetr-l.pt, ...).
_STOCK_MODEL_PREFIXES = ("yolo", "rtdetr")


class ModelLoadError(RuntimeError):
    """Raised when the detection model cannot be loaded."""


@dataclass(frozen=True)
class Detection:
    """A tracked person in one frame. ``bbox`` is ``(x1, y1, x2, y2)`` in pixels."""

    track_id: int
    bbox: tuple[int, int, int, int]
    confidence: float

    @property
    def center(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.bbox
        return (x1 + x2) // 2, (y1 + y2) // 2

    @property
    def area(self) -> int:
        x1, y1, x2, y2 = self.bbox
        return max(0, x2 - x1) * max(0, y2 - y1)


@dataclass(frozen=True)
class DetectionFilter:
    """Post-tracking filters that reject unlikely person boxes.

    Attributes:
        confidence: minimum detector confidence (0-1).
        min_box_area: minimum box area in pixels; drops distant or spurious boxes.
        min_aspect_ratio / max_aspect_ratio: allowed width/height range. People are
            usually taller than they are wide.
    """

    confidence: float = 0.5
    min_box_area: int = 1500
    min_aspect_ratio: float = 0.15
    max_aspect_ratio: float = 2.0

    def accepts(self, bbox: tuple[int, int, int, int], confidence: float) -> bool:
        x1, y1, x2, y2 = bbox
        width, height = x2 - x1, y2 - y1
        if confidence < self.confidence or width <= 0 or height <= 0:
            return False
        if width * height < self.min_box_area:
            return False
        aspect = width / height
        return self.min_aspect_ratio <= aspect <= self.max_aspect_ratio


def resolve_tracker(name: str) -> str:
    """Map ``bytetrack``/``botsort`` to Ultralytics config names; accept a custom YAML path."""
    key = name.strip().lower()
    if key in TRACKERS:
        return TRACKERS[key]
    if key in TRACKERS.values():
        return key
    path = Path(name).expanduser()
    if path.suffix.lower() in {".yaml", ".yml"}:
        if not path.is_file():
            raise ValueError(f"tracker config not found: {path}")
        return str(path)
    raise ValueError(f"unknown tracker '{name}'. Use 'bytetrack', 'botsort' or a path to a tracker YAML.")


def resolve_device(name: str) -> str:
    """Resolve ``auto`` to the fastest available device: CUDA, then Apple MPS, then CPU."""
    if name != "auto":
        return name
    try:
        import torch
    except ImportError:  # pragma: no cover
        return "cpu"
    if torch.cuda.is_available():
        return "cuda:0"
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available():
        return "mps"
    return "cpu"


def resolve_model_path(model: str, models_dir: Path | None) -> str:
    """Return a loadable path for ``model``.

    Existing paths are used as-is. A bare stock name like ``yolov8n.pt`` resolves into
    ``models_dir`` so weights download to the cache instead of the working directory.
    """
    path = Path(model).expanduser()
    if path.exists():
        return str(path)
    is_bare_name = path.parent == Path(".")
    if is_bare_name and path.suffix == ".pt" and path.name.lower().startswith(_STOCK_MODEL_PREFIXES):
        if models_dir is None:
            return model
        models_dir.mkdir(parents=True, exist_ok=True)
        return str(models_dir / path.name)
    raise ModelLoadError(
        f"model file not found: {model}. Pass a path to a .pt file or a stock name such as yolov8n.pt."
    )


class PersonDetector:
    """Detect and track people frame by frame.

    Args:
        model: weights path or stock name (``yolov8n.pt`` ... ``yolov8x.pt``).
        device: ``auto``, ``cpu``, ``mps``, ``cuda:0``...
        tracker: ``bytetrack``, ``botsort`` or a tracker YAML path.
        iou: IoU threshold for non-maximum suppression.
        detection_filter: post-tracking box filters.
        models_dir: where stock weights are downloaded.
    """

    def __init__(
        self,
        model: str = "yolov8m.pt",
        *,
        device: str = "auto",
        tracker: str = "bytetrack",
        iou: float = 0.5,
        detection_filter: DetectionFilter | None = None,
        models_dir: Path | None = None,
    ) -> None:
        self.tracker_config = resolve_tracker(tracker)
        self.tracker_name = Path(self.tracker_config).stem
        self.filter = detection_filter or DetectionFilter()
        self.iou = iou
        self.model_name = Path(model).name

        # Never let Ultralytics pip-install packages at runtime; keep its console quiet.
        os.environ.setdefault("YOLO_AUTOINSTALL", "false")
        os.environ.setdefault("YOLO_VERBOSE", "false")

        weights = resolve_model_path(model, models_dir)
        self.device = resolve_device(device)
        try:
            from ultralytics import YOLO

            self.model: Any = YOLO(weights)
        except Exception as exc:
            raise ModelLoadError(
                f"could not load model '{model}': {exc}. Stock weights are downloaded on first "
                "use, so check your internet connection, or pass a local .pt file with --model."
            ) from exc
        logger.info("Loaded %s on %s with %s tracking", self.model_name, self.device, self.tracker_name)

    def track(self, frame: np.ndarray) -> list[Detection]:
        """Run detection + tracking on one BGR frame and return accepted tracked people."""
        results = self.model.track(
            frame,
            persist=True,
            classes=[PERSON_CLASS_ID],
            conf=self.filter.confidence,
            iou=self.iou,
            tracker=self.tracker_config,
            device=self.device,
            verbose=False,
        )
        boxes = results[0].boxes
        if boxes is None or boxes.id is None:
            return []
        detections = []
        for xyxy, track_id, conf in zip(  # noqa: B905 - equal-length tensors from one result
            boxes.xyxy.cpu().numpy(), boxes.id.int().cpu().tolist(), boxes.conf.cpu().tolist()
        ):
            bbox = (int(xyxy[0]), int(xyxy[1]), int(xyxy[2]), int(xyxy[3]))
            if self.filter.accepts(bbox, float(conf)):
                detections.append(Detection(int(track_id), bbox, float(conf)))
        return detections
