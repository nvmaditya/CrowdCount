import functools
from pathlib import Path

import bcrypt
import cv2
import numpy as np
import pytest


@pytest.fixture(autouse=True)
def fast_bcrypt(monkeypatch):
    """bcrypt's production cost (12 rounds) makes every hash ~0.25 s; tests don't need that."""
    monkeypatch.setattr(bcrypt, "gensalt", functools.partial(bcrypt.gensalt, rounds=4))


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


ADMIN_PASSWORD = "correct-horse-battery"


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    """Factory for a TestClient on an isolated app (own data dir, zones file and state)."""
    from fastapi.testclient import TestClient

    from crowdcount.config import Settings
    from crowdcount.server.app import create_app
    from crowdcount.state import SharedState

    monkeypatch.setenv("CROWDCOUNT_ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.delenv("CROWDCOUNT_JWT_SECRET", raising=False)
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    clients = []

    def make(state=None, **settings):
        settings.setdefault("data_dir", tmp_path / "data")
        settings.setdefault("zones_file", tmp_path / "zones.json")
        state = state or SharedState()
        client = TestClient(create_app(Settings(**settings), state))
        client.__enter__()
        clients.append(client)
        client.state = state
        return client

    yield make
    for c in clients:
        c.__exit__(None, None, None)


@pytest.fixture
def client(make_client):
    return make_client()


def login(client, username="admin", password=ADMIN_PASSWORD) -> dict:
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
def admin_headers(client):
    return login(client)
