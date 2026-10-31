# Configuration

Settings come from four places. Later ones win:

1. built-in defaults
2. `crowdcount.toml`: `--config PATH`, else `$CROWDCOUNT_CONFIG`, else `./crowdcount.toml`
   if present
3. environment variables (and `./.env`, which never overrides variables already set)
4. command-line flags

Unknown keys, wrong types and out-of-range values stop the program with a message that names
the setting (exit code 2).

## Commands

| Command | What it runs |
| --- | --- |
| `crowdcount run` | Detector + dashboard in one process. The normal way to run CrowdCount. |
| `crowdcount demo` | `run` on OpenCV's `vtest.avi` sample (downloaded once, SHA-256 verified) with zones drawn for it. |
| `crowdcount detect` | Detector only, with an OpenCV preview window by default; no web server. For recording, metrics and benchmarking. |
| `crowdcount serve` | Dashboard and admin API only. Shows "No detector running"; useful for managing users and zones. |
| `crowdcount zones list` | Print the zones in a zones file. |
| `crowdcount zones validate [--source S]` | Validate a zones file; with `--source`, also check every zone fits that source's frame. |
| `crowdcount zones edit [--source S]` | Draw zones in an OpenCV window ([zones.md](zones.md)). |
| `crowdcount admin reset-password [--username U] [--password P]` | Set a password (prompts if omitted). Creates `admin` if there are no users at all. |

`-c/--config` and `-v/--verbose` work before or after the command. `crowdcount --version`
prints the version. Run any command with `--help` for its flags.

### Flags for `run`, `demo` and `detect`

| Flag | Default | Meaning |
| --- | --- | --- |
| `--source` | (required) | Video file or stream URL (`rtsp://`, `http://`...). Not on `demo`. |
| `--camera NAME` | | (`run` only) Use a source saved under Settings → Video sources. |
| `--model` | `yolo26m.pt` | YOLO weights: a stock name such as `yolo26s.pt`, `yolo11m.pt` or `yolov8m.pt` (downloaded on first use to the cache dir), or a `.pt` path. |
| `--device` | `auto` | `auto` picks `cuda:0`, then `mps` (Apple silicon), then `cpu`. |
| `--tracker` | `bytetrack` | `bytetrack`, `botsort`, or an Ultralytics tracker YAML ([tracking.md](tracking.md)). |
| `--conf` | `0.5` | Minimum detection confidence. |
| `--iou` | `0.5` | IoU threshold for non-maximum suppression. |
| `--min-area` | `1500` | Ignore boxes smaller than this many pixels. |
| `--zones` | `zones.json` | Zones file (`demo`: `<data dir>/demo-zones.json`). |
| `--loop / --no-loop` | off | Restart video files at the end. |
| `--stride N` | `1` | Process every Nth frame. |
| `--realtime / --no-realtime` | on | Pace files at their native frame rate. Turn off to process as fast as possible. |
| `--display / --no-display` | `detect`: on; others: off | OpenCV preview window. Keys: `q`/Esc quit, `p`/space pause, `+`/`-` stride, `z` print zone stats, `r` reset heatmap. |
| `--output FILE.mp4` | | Save the annotated video. |
| `--max-frames N` | | Stop after N processed frames. |
| `--metrics-out DIR` | | Write per-frame CSVs and `run_summary.json` (see below). |

`demo` changes some defaults (any flag still overrides them): `--model yolo26n.pt --conf 0.35
--min-area 800 --loop`. The people in the sample video are small and distant.

### Flags for `run`, `demo` and `serve`

| Flag | Default | Meaning |
| --- | --- | --- |
| `--host` | `127.0.0.1` | Interface to bind. `0.0.0.0` exposes the dashboard on your network ([dashboard.md](dashboard.md#security-model)). |
| `--port` | `8080` | HTTP port. The command fails fast if the port is taken. |
| `--data-dir` | `./data` | Users, settings, activity log and alert history. |

When `run` or `demo` reaches the end of a video (no `--loop`) or `--max-frames`, detection
stops but the dashboard keeps serving the results until you press Ctrl+C.

### Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success, or stopped by SIGTERM (`docker stop`, `kill`). |
| 1 | Runtime error: source won't open, model can't load, port in use, download failed. |
| 2 | Configuration error: invalid config, zones file or arguments. |
| 130 | Interrupted with Ctrl+C. |

## Config file

Copy [`crowdcount.example.toml`](../crowdcount.example.toml) to `crowdcount.toml` (it is
git-ignored). Every key is optional. Relative paths are resolved against the file's directory.

```toml
[video]
source = "rtsp://user:pass@192.168.1.20/stream1"
loop = false
stride = 1
realtime = true

[detection]
model = "yolo26s.pt"
device = "auto"
tracker = "bytetrack"
confidence = 0.45
iou = 0.5
min_box_area = 1200
min_aspect_ratio = 0.15
max_aspect_ratio = 2.0

[zones]
file = "zones/lobby.json"

[server]
host = "0.0.0.0"
port = 8080

[storage]
data_dir = "/var/lib/crowdcount"
```

Alert rules, saved video sources and log retention are not in this file. They are edited at
runtime (Alerts, Settings and Zones pages) and stored in `<data dir>/config.json`.

### `config.json`

You normally never edit this file by hand, but it is plain JSON:

```json
{
  "cameras": [],
  "thresholds": {"global_threshold": 50, "zone_thresholds": {"East plaza": 2}},
  "rule_options": {
    "global": {},
    "zones": {"East plaza": {"warn_percent": 80, "enabled": true, "banner": true}}
  },
  "log_retention_days": 30
}
```

- `thresholds` holds the limits as whole numbers, exactly as in 1.x, so a 2.0 file still opens in
  1.x. `global_threshold` is the Total occupancy limit (default 50); `zone_thresholds` has one
  entry per zone rule.
- `rule_options` holds the rest of each alert rule, under `global` and `zones` (keyed by zone
  name): `warn_percent` (early warning, 1-100, default 80), `enabled` (default `true`) and
  `banner` (show the dashboard banner, default `true`). Anything missing takes the default, so
  a file without `rule_options` (from 1.x or an earlier 2.0 build) loads with default options.
- A hand-edited value that is invalid (a limit below 1, a percentage outside 1-100, a non-boolean
  switch) is ignored with a warning in the log, and the default is used. An invalid zone limit
  leaves that zone without a rule.

## Environment variables

See [`.env.example`](../.env.example). `crowdcount` reads `./.env` on start.

| Variable | Purpose |
| --- | --- |
| `CROWDCOUNT_ADMIN_PASSWORD` | Password for the first `admin` account, used only when no users exist. If unset, a random password is generated and printed once in the log. |
| `CROWDCOUNT_JWT_SECRET` | Token-signing secret (32+ characters). If unset, one is generated into `<data dir>/.jwt_secret`. The 1.x name `JWT_SECRET_KEY` is still honoured. |
| `CROWDCOUNT_DATA_DIR` | Data directory (overrides the config file; a `--data-dir` flag overrides it). |
| `CROWDCOUNT_CACHE_DIR` | Download cache for the demo video and model weights. Default `$XDG_CACHE_HOME/crowdcount` or `~/.cache/crowdcount`. |
| `CROWDCOUNT_CONFIG` | Path to a config file. |
| `CROWDCOUNT_LOG_LEVEL` | `DEBUG`, `INFO` (default), `WARNING`, `ERROR`. |
| `CROWDCOUNT_TRUST_PROXY` | `1` to take audit-log client IPs from `X-Forwarded-For`. Only behind a proxy you control. |

## Metrics output

`--metrics-out DIR` writes:

| File | One row per | Columns |
| --- | --- | --- |
| `frame_timing.csv` | processed frame | inference, post-processing and total ms; instantaneous FPS; people count |
| `confidence_scores.csv` | accepted detection | track ID, confidence, zones, box, centre, area |
| `zone_timeseries.csv` | processed frame | total and per-zone occupancy |
| `track_summaries.csv` | track ID | first/last frame, frames seen, max trail length, last confidence |
| `history_dump.json` | | the 1 Hz history, as served by `/api/history` |
| `run_summary.json` | | model, tracker, device, source, frame size, frames processed, mean processing FPS (first frame excluded), unique tracks per zone |
