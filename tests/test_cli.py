import pytest

from crowdcount import __version__
from crowdcount.cli import EXIT_ERROR, EXIT_OK, EXIT_USAGE, main
from crowdcount.zones import Zone, save_zones


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CROWDCOUNT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("CROWDCOUNT_CONFIG", raising=False)


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_zones_list_and_validate(tmp_path, capsys):
    save_zones(tmp_path / "zones.json", [Zone("Gate", [(0, 0), (10, 0), (10, 10)])])
    assert main(["zones", "list"]) == EXIT_OK
    assert "Gate" in capsys.readouterr().out
    assert main(["zones", "validate", "-v"]) == EXIT_OK


def test_zones_validate_checks_frame_bounds(tmp_path, synthetic_video, capsys):
    save_zones(tmp_path / "zones.json", [Zone("Huge", [(0, 0), (1000, 0), (1000, 1000)])])
    assert main(["zones", "validate", "--source", str(synthetic_video)]) == EXIT_ERROR
    assert "extend beyond the 160x120 frame" in capsys.readouterr().out


def test_invalid_zones_file_is_a_usage_error(tmp_path, capsys):
    (tmp_path / "zones.json").write_text('{"zones": [{"name": "A", "points": [[0, 0]]}]}')
    assert main(["zones", "validate"]) == EXIT_USAGE
    assert "at least 3 points" in capsys.readouterr().err


def test_missing_video_fails_before_loading_a_model(tmp_path, capsys):
    assert main(["detect", "--source", "missing.mp4", "--no-display"]) == EXIT_ERROR
    assert "video file not found: missing.mp4" in capsys.readouterr().err
    assert not (tmp_path / "cache" / "models").exists()


def test_bad_config_value(tmp_path, capsys):
    (tmp_path / "crowdcount.toml").write_text("[detection]\nconfidence = 2.0\n")
    assert main(["zones", "list"]) == EXIT_USAGE
    assert "confidence must be between 0 and 1" in capsys.readouterr().err


def test_port_in_use(tmp_path, capsys):
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        assert main(["serve", "--port", str(port), "--data-dir", str(tmp_path / "data")]) == EXIT_ERROR
    assert "cannot listen on 127.0.0.1" in capsys.readouterr().err
