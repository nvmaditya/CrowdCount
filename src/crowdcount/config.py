"""Runtime configuration.

Precedence, lowest to highest: built-in defaults, ``crowdcount.toml``, environment
variables (``CROWDCOUNT_DATA_DIR``), then command-line flags.

The config file is optional. It is read from ``--config``, else ``$CROWDCOUNT_CONFIG``,
else ``./crowdcount.toml`` if present. Relative paths inside it are resolved against the
file's own directory. See ``crowdcount.example.toml`` for every key.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on Python 3.10 only
    import tomli as tomllib

CONFIG_FILENAME = "crowdcount.toml"


class ConfigError(ValueError):
    """Raised for unreadable or invalid configuration."""


@dataclass(frozen=True)
class Settings:
    """Every tunable value in one place. Field names double as CLI ``dest`` names."""

    # [video]
    source: str = "0"
    loop: bool = False
    stride: int = 1
    realtime: bool = True
    # [detection]
    model: str = "yolov8m.pt"
    device: str = "auto"
    tracker: str = "bytetrack"
    confidence: float = 0.5
    iou: float = 0.5
    min_box_area: int = 1500
    min_aspect_ratio: float = 0.15
    max_aspect_ratio: float = 2.0
    # [zones]
    zones_file: Path = Path("zones.json")
    # [server]
    host: str = "127.0.0.1"
    port: int = 8080
    # [storage]
    data_dir: Path = Path("data")

    def validate(self) -> Settings:
        """Raise :class:`ConfigError` if any value is out of range; return ``self``."""
        checks = [
            (self.stride >= 1, "stride must be >= 1"),
            (0.0 <= self.confidence <= 1.0, "confidence must be between 0 and 1"),
            (0.0 < self.iou <= 1.0, "iou must be in (0, 1]"),
            (self.min_box_area >= 0, "min_box_area must be >= 0"),
            (
                0 < self.min_aspect_ratio < self.max_aspect_ratio,
                "aspect ratio bounds must satisfy 0 < min_aspect_ratio < max_aspect_ratio",
            ),
            (1 <= self.port <= 65535, "port must be between 1 and 65535"),
            (bool(self.model), "model must not be empty"),
        ]
        for ok, message in checks:
            if not ok:
                raise ConfigError(message)
        return self


# (section, key) in crowdcount.toml -> Settings field
TOML_KEYS: dict[tuple[str, str], str] = {
    ("video", "source"): "source",
    ("video", "loop"): "loop",
    ("video", "stride"): "stride",
    ("video", "realtime"): "realtime",
    ("detection", "model"): "model",
    ("detection", "device"): "device",
    ("detection", "tracker"): "tracker",
    ("detection", "confidence"): "confidence",
    ("detection", "iou"): "iou",
    ("detection", "min_box_area"): "min_box_area",
    ("detection", "min_aspect_ratio"): "min_aspect_ratio",
    ("detection", "max_aspect_ratio"): "max_aspect_ratio",
    ("zones", "file"): "zones_file",
    ("server", "host"): "host",
    ("server", "port"): "port",
    ("storage", "data_dir"): "data_dir",
}

_PATH_FIELDS = {"zones_file", "data_dir"}
_FIELD_TYPES = {f.name: f.type for f in fields(Settings)}


def _coerce(name: str, value: Any, base_dir: Path | None = None) -> Any:
    """Convert a raw value to the type of ``Settings.<name>``."""
    kind = _FIELD_TYPES[name]
    if name in _PATH_FIELDS:
        if not isinstance(value, (str, os.PathLike)):
            raise ConfigError(f"{name} must be a path string")
        path = Path(value).expanduser()
        if base_dir is not None and not path.is_absolute():
            path = base_dir / path
        return path
    if kind == "bool":
        if not isinstance(value, bool):
            raise ConfigError(f"{name} must be true or false")
        return value
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"{name} must be an integer")
        return value
    if kind == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"{name} must be a number")
        return float(value)
    if name == "source" and isinstance(value, int) and not isinstance(value, bool):
        return str(value)  # camera index written as a bare number
    if not isinstance(value, str):
        raise ConfigError(f"{name} must be a string")
    return value


def find_config_file(explicit: str | os.PathLike[str] | None, env: Mapping[str, str]) -> Path | None:
    """Locate the config file, or return ``None`` if there isn't one."""
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise ConfigError(f"config file not found: {path}")
        return path
    if env.get("CROWDCOUNT_CONFIG"):
        path = Path(env["CROWDCOUNT_CONFIG"]).expanduser()
        if not path.is_file():
            raise ConfigError(f"CROWDCOUNT_CONFIG points to a missing file: {path}")
        return path
    default = Path(CONFIG_FILENAME)
    return default if default.is_file() else None


def read_config_file(path: Path) -> dict[str, Any]:
    """Parse ``path`` into a ``{field: value}`` mapping, rejecting unknown keys."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    values: dict[str, Any] = {}
    base_dir = path.parent.resolve()
    for section, table in data.items():
        if not isinstance(table, dict):
            raise ConfigError(f"{path}: top-level key '{section}' must be a [section]")
        for key, value in table.items():
            name = TOML_KEYS.get((section, key))
            if name is None:
                known = ", ".join(sorted(f"{s}.{k}" for s, k in TOML_KEYS))
                raise ConfigError(f"{path}: unknown setting '{section}.{key}'. Known settings: {known}")
            try:
                values[name] = _coerce(name, value, base_dir)
            except ConfigError as exc:
                raise ConfigError(f"{path}: [{section}] {exc}") from exc
    return values


def load_settings(
    config_path: str | os.PathLike[str] | None = None,
    overrides: Mapping[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
) -> Settings:
    """Build validated :class:`Settings` from file, environment and ``overrides``.

    ``overrides`` holds command-line values; entries that are ``None`` are ignored.
    """
    env = os.environ if env is None else env
    settings = Settings()
    path = find_config_file(config_path, env)
    if path is not None:
        settings = replace(settings, **read_config_file(path))
    if env.get("CROWDCOUNT_DATA_DIR"):
        settings = replace(settings, data_dir=Path(env["CROWDCOUNT_DATA_DIR"]).expanduser())
    if overrides:
        cli = {k: _coerce(k, v) for k, v in overrides.items() if v is not None and k in _FIELD_TYPES}
        settings = replace(settings, **cli)
    return settings.validate()


def load_dotenv(path: str | os.PathLike[str] = ".env", env: dict[str, str] | None = None) -> list[str]:
    """Load ``KEY=VALUE`` lines from ``path`` into the environment.

    Variables that are already set win, so the real environment always overrides the
    file. Returns the names that were set. Missing files are ignored.
    """
    target = os.environ if env is None else env
    path = Path(path)
    if not path.is_file():
        return []
    loaded = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key and key not in target and value:
            target[key] = value
            loaded.append(key)
    return loaded


def cache_dir(env: Mapping[str, str] | None = None) -> Path:
    """Directory for downloaded assets (demo video, model weights)."""
    env = os.environ if env is None else env
    if env.get("CROWDCOUNT_CACHE_DIR"):
        return Path(env["CROWDCOUNT_CACHE_DIR"]).expanduser()
    base = Path(env["XDG_CACHE_HOME"]).expanduser() if env.get("XDG_CACHE_HOME") else Path.home() / ".cache"
    return base / "crowdcount"
