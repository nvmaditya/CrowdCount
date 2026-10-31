"""Command-line interface: ``crowdcount <command>``.

Commands:
    run      detector + web dashboard in one process (the usual way to run CrowdCount)
    demo     ``run`` on a downloaded sample video with zones drawn for it
    detect   detector only, with an OpenCV preview window; no web server
    serve    web dashboard and admin API only (no detector)
    zones    list, validate or interactively edit zone polygons
    admin    account maintenance (reset a password)
"""

from __future__ import annotations

import argparse
import logging
import signal
import socket
import sys
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from crowdcount import __version__
from crowdcount.config import ConfigError, Settings, cache_dir, load_dotenv, load_settings
from crowdcount.log import setup_logging
from crowdcount.zones import ZoneConfigError, load_zones, zones_outside_frame

logger = logging.getLogger("crowdcount")

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_INTERRUPTED = 0, 1, 2, 130
_terminated = False


class PortInUseError(RuntimeError):
    pass


# --------------------------------------------------------------------------- arguments


def _add_video_args(p: argparse.ArgumentParser, *, source: bool = True) -> None:
    g = p.add_argument_group("video")
    if source:
        g.add_argument("--source", help="video file or stream URL (rtsp://, http://)")
    g.add_argument(
        "--loop", action=argparse.BooleanOptionalAction, default=None, help="restart video files at the end"
    )
    g.add_argument("--stride", type=int, help="process every Nth frame (default: 1)")
    g.add_argument(
        "--realtime",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="pace video files at their native frame rate (default: on; off for benchmarks)",
    )
    g.add_argument(
        "--display", action=argparse.BooleanOptionalAction, default=None, help="show an OpenCV preview window"
    )
    g.add_argument("--output", type=Path, help="save the annotated video to this .mp4 file")
    g.add_argument("--max-frames", type=int, help="stop after processing this many frames")
    g.add_argument(
        "--metrics-out", type=Path, help="write per-frame CSV metrics and a run summary to this directory"
    )


def _add_detection_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("detection")
    g.add_argument(
        "--model",
        help="YOLO weights: stock name (yolo26n.pt ... yolo26x.pt, yolo11m.pt, yolov8m.pt) or path (default: yolo26m.pt)",
    )
    g.add_argument("--device", help="auto, cpu, mps, cuda:0 ... (default: auto)")
    g.add_argument("--tracker", help="bytetrack, botsort, or a tracker YAML path (default: bytetrack)")
    g.add_argument(
        "--conf", dest="confidence", type=float, help="minimum detection confidence 0-1 (default: 0.5)"
    )
    g.add_argument("--iou", type=float, help="NMS IoU threshold (default: 0.5)")
    g.add_argument(
        "--min-area", dest="min_box_area", type=int, help="minimum box area in pixels (default: 1500)"
    )
    g.add_argument("--zones", dest="zones_file", type=Path, help="zones JSON file (default: zones.json)")


def _add_server_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("server")
    g.add_argument(
        "--host", help="interface to bind (default: 127.0.0.1; use 0.0.0.0 to expose on your network)"
    )
    g.add_argument("--port", type=int, help="HTTP port (default: 8080)")
    g.add_argument(
        "--data-dir", type=Path, help="where users, settings and logs are stored (default: ./data)"
    )


def _add_common_args(p: argparse.ArgumentParser, *, suppress: bool = False) -> None:
    default = argparse.SUPPRESS if suppress else None
    p.add_argument(
        "-c",
        "--config",
        type=Path,
        default=default,
        help="path to crowdcount.toml (default: ./crowdcount.toml if present)",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=argparse.SUPPRESS if suppress else False,
        help="debug logging",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crowdcount",
        description="Real-time crowd monitoring: YOLO detection, tracking, polygon zones and a live dashboard.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    _add_common_args(parser)
    common = argparse.ArgumentParser(add_help=False)
    _add_common_args(common, suppress=True)  # also accept -v/--config after the command
    sub = parser.add_subparsers(dest="command", metavar="<command>", required=True)
    _real_add_parser = sub.add_parser

    def add_parser(name: str, **kwargs: Any) -> argparse.ArgumentParser:
        return _real_add_parser(name, parents=[common], **kwargs)

    sub.add_parser = add_parser  # type: ignore[method-assign]

    run = sub.add_parser("run", help="run the detector and the web dashboard together")
    _add_video_args(run)
    run.add_argument(
        "--camera", help="use a source saved in Settings > Video sources, by name (instead of --source)"
    )
    _add_detection_args(run)
    _add_server_args(run)
    run.set_defaults(func=cmd_run)

    demo = sub.add_parser(
        "demo",
        help="run on a sample video (downloaded once) with ready-made zones",
        description=(
            "Downloads OpenCV's vtest.avi sample (8 MB, once) and runs the detector and dashboard on it, "
            "with zones drawn for that scene in <data dir>/demo-zones.json. Demo defaults, which any flag "
            "overrides: --model yolo26n.pt --conf 0.35 --min-area 800 --loop."
        ),
    )
    _add_video_args(demo, source=False)
    _add_detection_args(demo)
    _add_server_args(demo)
    demo.set_defaults(func=cmd_demo)

    detect = sub.add_parser("detect", help="run the detector only, with a preview window")
    _add_video_args(detect)
    _add_detection_args(detect)
    detect.set_defaults(func=cmd_detect)

    serve = sub.add_parser("serve", help="run only the web dashboard and admin API")
    _add_server_args(serve)
    serve.add_argument(
        "--zones", dest="zones_file", type=Path, help="zones JSON file edited on the Zones page"
    )
    serve.set_defaults(func=cmd_serve)

    zones = sub.add_parser("zones", help="list, validate or edit zones")
    zsub = zones.add_subparsers(dest="zones_command", metavar="<action>", required=True)
    _real_zadd = zsub.add_parser
    zsub.add_parser = lambda name, **kw: _real_zadd(name, parents=[common], **kw)  # type: ignore[method-assign]
    zl = zsub.add_parser("list", help="print the zones in a zones file")
    zl.set_defaults(func=cmd_zones_list)
    zv = zsub.add_parser("validate", help="check a zones file (and optionally that it fits a source's frame)")
    zv.add_argument("--source", help="also check zone bounds against this source's frame size")
    zv.set_defaults(func=cmd_zones_validate)
    ze = zsub.add_parser("edit", help="draw zones on frames from a camera or video")
    ze.add_argument("--source", help="video file or stream URL to draw on")
    ze.set_defaults(func=cmd_zones_edit)
    for zp in (zl, zv, ze):
        zp.add_argument("--zones", dest="zones_file", type=Path, help="zones JSON file (default: zones.json)")

    admin = sub.add_parser("admin", help="account maintenance")
    asub = admin.add_subparsers(dest="admin_command", metavar="<action>", required=True)
    _real_aadd = asub.add_parser
    asub.add_parser = lambda name, **kw: _real_aadd(name, parents=[common], **kw)  # type: ignore[method-assign]
    rp = asub.add_parser("reset-password", help="set a user's password (creates the admin if no users exist)")
    rp.add_argument("--username", default="admin", help="account to change (default: admin)")
    rp.add_argument("--password", help="new password (prompted for if omitted)")
    rp.add_argument("--data-dir", type=Path, help="data directory (default: ./data)")
    rp.set_defaults(func=cmd_admin_reset_password)
    return parser


_SETTING_KEYS = {
    "source",
    "loop",
    "stride",
    "realtime",
    "model",
    "device",
    "tracker",
    "confidence",
    "iou",
    "min_box_area",
    "zones_file",
    "host",
    "port",
    "data_dir",
}


def _settings(args: argparse.Namespace, extra: dict[str, Any] | None = None) -> Settings:
    overrides = {
        **(extra or {}),
        **{k: v for k, v in vars(args).items() if k in _SETTING_KEYS and v is not None},
    }
    return load_settings(args.config, overrides)


def _dashboard_url(host: str, port: int) -> str:
    shown = "localhost" if host in ("0.0.0.0", "::", "") else host
    return f"http://{shown}:{port}/"


# --------------------------------------------------------------------------- server


def _ensure_port_free(host: str, port: int) -> None:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError as exc:
            raise PortInUseError(
                f"cannot listen on {host}:{port} ({exc.strerror}). Is another server running? Try --port."
            ) from exc


class _ServerThread:
    """Uvicorn in a background thread, so OpenCV can own the main thread."""

    def __init__(self, app: Any, host: str, port: int) -> None:
        import uvicorn

        config = uvicorn.Config(app, host=host, port=port, log_level="warning", timeout_graceful_shutdown=2)
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, name="crowdcount-server", daemon=True)

    def start(self, timeout: float = 15.0) -> None:
        self.thread.start()
        deadline = time.monotonic() + timeout
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("web server failed to start; see the log above")
            time.sleep(0.05)

    def wait(self) -> None:
        while self.thread.is_alive():
            self.thread.join(0.5)

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(5)


def _make_app(settings: Settings) -> Any:
    from crowdcount.server.app import create_app
    from crowdcount.state import shared_state

    return create_app(settings, shared_state, public_url=_dashboard_url(settings.host, settings.port))


# --------------------------------------------------------------------------- detection


def _resolve_source(settings: Settings) -> str:
    if not settings.source:
        raise ConfigError(
            "no video source: pass --source with a video file or stream URL, set [video] source "
            "in crowdcount.toml, or try `crowdcount demo`"
        )
    if settings.source == "demo":
        from crowdcount.demo import ensure_demo_video

        return str(ensure_demo_video(cache_dir()))
    return settings.source


def _run_detection(
    settings: Settings, args: argparse.Namespace, *, with_server: bool, display_default: bool
) -> int:
    from crowdcount.detector import DetectionFilter, PersonDetector
    from crowdcount.pipeline import Pipeline, PipelineOptions
    from crowdcount.state import shared_state
    from crowdcount.video import VideoSource

    load_zones(settings.zones_file)  # fail fast on a malformed zones file
    source = VideoSource(_resolve_source(settings), loop=settings.loop).open()

    server = None
    if with_server:
        _ensure_port_free(settings.host, settings.port)
        server = _ServerThread(_make_app(settings), settings.host, settings.port)
        server.start()
        shared_state.set_status(state="starting", source=source.label)
        logger.info("Dashboard: %s", _dashboard_url(settings.host, settings.port))

    try:
        detector = PersonDetector(
            settings.model,
            device=settings.device,
            tracker=settings.tracker,
            iou=settings.iou,
            detection_filter=DetectionFilter(
                confidence=settings.confidence,
                min_box_area=settings.min_box_area,
                min_aspect_ratio=settings.min_aspect_ratio,
                max_aspect_ratio=settings.max_aspect_ratio,
            ),
            models_dir=cache_dir() / "models",
        )
        options = PipelineOptions(
            stride=settings.stride,
            realtime=settings.realtime,
            display=display_default if args.display is None else args.display,
            output=args.output,
            max_frames=args.max_frames,
            metrics_out=args.metrics_out,
        )
        pipeline = Pipeline(
            detector,
            source,
            settings.zones_file,
            shared_state,
            options,
            info={"model": detector.model_name, "tracker": detector.tracker_name, "device": detector.device},
        )
        summary = pipeline.run()
        logger.info(
            "Detection stopped (%s): %d frames processed, %.1f fps mean processing rate",
            summary.stopped_by,
            summary.frames_processed,
            summary.mean_fps,
        )
        if server is not None and summary.stopped_by in ("eof", "max_frames"):
            logger.info(
                "Dashboard still available at %s; press Ctrl+C to exit",
                _dashboard_url(settings.host, settings.port),
            )
            server.wait()
    except Exception:
        shared_state.set_status(state="error")
        raise
    finally:
        source.release()
        if server is not None:
            server.stop()
    return EXIT_OK


# --------------------------------------------------------------------------- commands


def _camera_source(settings: Settings, name: str) -> str:
    from crowdcount.server.config_store import ConfigStore
    from crowdcount.server.storage import JsonStore

    store = ConfigStore(JsonStore(settings.data_dir / "config.json", dict))
    camera = store.find_camera(name)
    if camera is None:
        known = ", ".join(c.name for c in store.cameras()) or "none saved yet"
        raise ConfigError(
            f"no camera named '{name}' (cameras: {known}). Add one in Settings > Video sources."
        )
    if not camera.enabled:
        raise ConfigError(f"camera '{camera.name}' is turned off in Settings > Video sources")
    logger.info("Using saved camera '%s'", camera.name)
    return camera.source_url


def cmd_run(args: argparse.Namespace) -> int:
    if args.camera and args.source:
        raise ConfigError("use either --camera or --source, not both")
    settings = _settings(args)
    if args.camera:
        settings = _settings(args, {"source": _camera_source(settings, args.camera)})
    return _run_detection(settings, args, with_server=True, display_default=False)


def cmd_demo(args: argparse.Namespace) -> int:
    from crowdcount.demo import DEMO_SETTINGS, ensure_demo_zones

    base = _settings(args)
    zones_file = args.zones_file or base.data_dir / "demo-zones.json"
    ensure_demo_zones(zones_file)
    settings = _settings(args, {**DEMO_SETTINGS, "source": "demo", "zones_file": zones_file})
    return _run_detection(settings, args, with_server=True, display_default=False)


def cmd_detect(args: argparse.Namespace) -> int:
    return _run_detection(_settings(args), args, with_server=False, display_default=True)


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    settings = _settings(args)
    _ensure_port_free(settings.host, settings.port)
    logger.info(
        "Dashboard: %s (no detector running in this process)", _dashboard_url(settings.host, settings.port)
    )
    uvicorn.run(
        _make_app(settings),
        host=settings.host,
        port=settings.port,
        log_level="warning",
        timeout_graceful_shutdown=2,
    )
    return EXIT_OK


def cmd_zones_list(args: argparse.Namespace) -> int:
    settings = _settings(args)
    zones = load_zones(settings.zones_file)
    if not zones:
        print(f"No zones in {settings.zones_file}")
        return EXIT_OK
    print(f"{len(zones)} zone(s) in {settings.zones_file}:")
    width = max(len(z.name) for z in zones)
    for z in zones:
        x1, y1, x2, y2 = z.bounds
        state = "enabled" if z.enabled else "disabled"
        print(
            f"  {z.name:<{width}}  {len(z.points):>2} points  {state:<8}  bounds ({x1},{y1})-({x2},{y2})  rgb{z.color}"
        )
    return EXIT_OK


def cmd_zones_validate(args: argparse.Namespace) -> int:
    settings = _settings(args)
    zones = load_zones(settings.zones_file)
    print(f"OK: {len(zones)} valid zone(s) in {settings.zones_file}")
    if args.source is not None:
        from crowdcount.video import VideoSource

        with VideoSource(_resolve_source(load_settings(args.config, {"source": args.source}))) as src:
            outside = zones_outside_frame(zones, src.width, src.height)
        if outside:
            print(
                f"Warning: {', '.join(outside)} extend beyond the {src.width}x{src.height} frame of {src.label}"
            )
            return EXIT_ERROR
        print(f"All zones fit the {src.width}x{src.height} frame of {src.label}")
    return EXIT_OK


def cmd_zones_edit(args: argparse.Namespace) -> int:
    from crowdcount.editor import run_editor
    from crowdcount.video import VideoSource

    overrides = {"source": args.source} if args.source is not None else {}
    settings = _settings(args, overrides)
    run_editor(VideoSource(_resolve_source(settings)), settings.zones_file)
    return EXIT_OK


def cmd_admin_reset_password(args: argparse.Namespace) -> int:
    import getpass

    from crowdcount.server.auth import UserStore
    from crowdcount.server.models import MAX_PASSWORD_BYTES, UserRole
    from crowdcount.server.storage import JsonStore

    settings = _settings(args)
    users = UserStore(JsonStore(settings.data_dir / "users.json", lambda: {"users": []}, private=True))
    exists = users.get_by_username(args.username) is not None
    if not exists and users.count():
        raise ConfigError(f"no user named '{args.username}' in {settings.data_dir / 'users.json'}")

    password = args.password
    if password is None:
        if not sys.stdin.isatty():
            raise ConfigError("no terminal to prompt on; pass --password")
        password = getpass.getpass(f"New password for {args.username}: ")
        if getpass.getpass("Repeat it: ") != password:
            raise ConfigError("passwords did not match")
    if len(password) < 8 or len(password.encode()) > MAX_PASSWORD_BYTES:
        raise ConfigError(f"password must be 8 characters to {MAX_PASSWORD_BYTES} bytes long")

    if exists:
        users.set_password(args.username, password)
        print(f"Password updated for '{args.username}'.")
    else:
        users.create(args.username, password, UserRole.ADMIN)
        print(f"Created admin '{args.username}'.")
    return EXIT_OK


# --------------------------------------------------------------------------- entry point


def _graceful_signals() -> None:
    """Treat SIGTERM (``docker stop``, ``kill``) like Ctrl+C so cleanup always runs.

    Also re-enables SIGINT if a parent shell started us with it ignored.
    """

    def _interrupt(signum: int, _frame: object) -> None:
        global _terminated
        _terminated = signum == signal.SIGTERM
        raise KeyboardInterrupt

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _interrupt)
        signal.signal(signal.SIGINT, _interrupt)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the ``crowdcount`` script. Returns the process exit code."""
    args = build_parser().parse_args(argv)
    loaded = load_dotenv()  # before logging, so CROWDCOUNT_LOG_LEVEL in .env applies
    setup_logging(args.verbose)
    _graceful_signals()
    if loaded:
        logger.debug("Loaded %s from .env", ", ".join(loaded))

    from crowdcount.demo import DemoAssetError
    from crowdcount.detector import ModelLoadError
    from crowdcount.video import VideoSourceError

    try:
        return int(args.func(args) or EXIT_OK)
    except KeyboardInterrupt:
        if _terminated:  # SIGTERM is a normal shutdown request (docker stop, systemd)
            logger.info("Stopped")
            return EXIT_OK
        logger.info("Interrupted")
        return EXIT_INTERRUPTED
    except (ConfigError, ZoneConfigError) as exc:
        logger.error("%s", exc)
        return EXIT_USAGE
    except (VideoSourceError, ModelLoadError, DemoAssetError, PortInUseError, ValueError, OSError) as exc:
        if args.verbose:
            logger.exception("Fatal error")
        else:
            logger.error("%s", exc)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
