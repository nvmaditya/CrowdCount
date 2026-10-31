from pathlib import Path

import pytest

from crowdcount.config import ConfigError, Settings, cache_dir, load_dotenv, load_settings


def write(tmp_path, text, name="crowdcount.toml"):
    path = tmp_path / name
    path.write_text(text)
    return path


def test_defaults_without_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert load_settings(env={}) == Settings()


def test_file_values_and_relative_paths(tmp_path):
    cfg = write(
        tmp_path,
        """
[video]
source = 1
loop = true

[detection]
model = "yolov8n.pt"
confidence = 0.4
min_box_area = 900

[zones]
file = "config/zones.json"

[server]
port = 9000
""",
    )
    s = load_settings(cfg, env={})
    assert s.source == "1"
    assert s.loop is True
    assert s.model == "yolov8n.pt"
    assert s.confidence == pytest.approx(0.4)
    assert s.min_box_area == 900
    assert s.zones_file == tmp_path.resolve() / "config/zones.json"
    assert s.port == 9000


def test_cli_overrides_file_and_ignores_none(tmp_path):
    cfg = write(tmp_path, "[server]\nport = 9000\nhost = '0.0.0.0'\n")
    s = load_settings(cfg, overrides={"port": 7000, "host": None}, env={})
    assert s.port == 7000
    assert s.host == "0.0.0.0"


def test_env_data_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = load_settings(env={"CROWDCOUNT_DATA_DIR": "/srv/crowd"})
    assert s.data_dir == Path("/srv/crowd")


def test_config_env_var(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = write(tmp_path, "[server]\nport = 8181\n", name="other.toml")
    assert load_settings(env={"CROWDCOUNT_CONFIG": str(cfg)}).port == 8181


def test_picks_up_crowdcount_toml_in_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write(tmp_path, "[detection]\ntracker = 'botsort'\n")
    assert load_settings(env={}).tracker == "botsort"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[detection]\nconfidnce = 0.3\n", "unknown setting 'detection.confidnce'"),
        ("[server]\nport = 'eighty'\n", "port must be an integer"),
        ("[video]\nloop = 'yes'\n", "loop must be true or false"),
        ("port = 8080\n", "must be a \\[section\\]"),
        ("[server\n", "crowdcount.toml"),
    ],
)
def test_invalid_files(tmp_path, text, message):
    with pytest.raises(ConfigError, match=message):
        load_settings(write(tmp_path, text), env={})


@pytest.mark.parametrize(
    "overrides",
    [{"confidence": 1.5}, {"stride": 0}, {"port": 70000}, {"min_aspect_ratio": 3.0}],
)
def test_validation(overrides):
    with pytest.raises(ConfigError):
        load_settings(overrides=overrides, env={})


def test_missing_explicit_config(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_settings(tmp_path / "nope.toml", env={})


def test_cache_dir(tmp_path):
    assert cache_dir({"CROWDCOUNT_CACHE_DIR": str(tmp_path)}) == tmp_path
    assert cache_dir({"XDG_CACHE_HOME": str(tmp_path)}) == tmp_path / "crowdcount"


def test_load_dotenv(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "# comment\n"
        "CROWDCOUNT_ADMIN_PASSWORD='s3cret value'\n"
        "export CROWDCOUNT_LOG_LEVEL=DEBUG  # inline comment\n"
        "CROWDCOUNT_JWT_SECRET=\n"
        "CROWDCOUNT_DATA_DIR=/from/file\n"
        "not a variable\n"
    )
    env = {"CROWDCOUNT_DATA_DIR": "/already/set"}
    loaded = load_dotenv(path, env)
    assert env["CROWDCOUNT_ADMIN_PASSWORD"] == "s3cret value"
    assert env["CROWDCOUNT_LOG_LEVEL"] == "DEBUG"
    assert env["CROWDCOUNT_DATA_DIR"] == "/already/set"  # the real environment wins
    assert "CROWDCOUNT_JWT_SECRET" not in env  # empty values are skipped
    assert sorted(loaded) == ["CROWDCOUNT_ADMIN_PASSWORD", "CROWDCOUNT_LOG_LEVEL"]
    assert load_dotenv(tmp_path / "missing", env) == []
