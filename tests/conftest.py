from pathlib import Path

import cv2
import numpy as np
import pytest


@pytest.fixture
def synthetic_video(tmp_path) -> Path:
    """A 20-frame 160x120 video with a white square moving left to right."""
    path = tmp_path / "clip.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10, (160, 120))
    assert writer.isOpened()
    for i in range(20):
        frame = np.zeros((120, 160, 3), np.uint8)
        x = 5 + i * 6
        frame[40:80, x : x + 20] = 255
        writer.write(frame)
    writer.release()
    return path
