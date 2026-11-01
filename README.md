<h1 align="center">
  <img src="src/crowdcount/web/favicon.svg" width="56" alt=""><br>
  CrowdCount
</h1>

<p align="center">
  <strong>Real-time people counting for any camera: YOLO26 detection, ByteTrack tracking, polygon zones and a live web dashboard, in one command.</strong>
</p>

<p align="center">
  <a href="https://github.com/nvmaditya/CrowdCount/actions/workflows/ci.yml"><img src="https://github.com/nvmaditya/CrowdCount/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <img src="https://img.shields.io/badge/python-3.10%20%7C%203.12%20%7C%203.14-3776AB?logo=python&logoColor=white" alt="Python 3.10+">
  <a href="LICENSE"><img src="https://img.shields.io/github/license/nvmaditya/CrowdCount" alt="License"></a>
  <img src="https://img.shields.io/badge/code%20style-ruff-D7FF64" alt="Ruff">
</p>

<p align="center">
  <img src="docs/images/demo.gif" width="760" alt="Animated demo: people walking across a campus plaza, each with a tracked bounding box, ID, confidence and motion trail, while two polygon zones show live 'now' and 'total' counts">
</p>

## Why CrowdCount

Knowing how many people are in a space, and where, matters for safety limits, queues,
staffing and space planning. Commercial people counters are closed boxes; research code stops
at a notebook. CrowdCount sits in between: point it at an RTSP camera or a video
file, draw the areas you care about, and get live occupancy, alerts and exports in a browser.
It is fully local, open source, and built to be read and extended.

## Features

- **Detection and tracking.** YOLO26 (`n` to `x`) finds people by default; YOLO11, YOLOv8 or
  other COCO-trained Ultralytics weights also work. ByteTrack, or BoT-SORT for moving cameras,
  gives each person a persistent ID.
- **Polygon zones.** Draw them on the live frame in the Zones page or in an OpenCV editor.
  Each zone gets live occupancy and a unique-track count; zones may overlap. Edits apply to the
  running detector within a second, with no restart.
- **Live dashboard.** Annotated MJPEG video, stat cards, per-zone bars against their limits,
  a people-over-time chart (10 minutes to an hour) and a cumulative heatmap over the scene.
  Five pages (Live, Alerts, Zones, Settings, Sign in), light and dark themes, and it works
  offline (no CDNs).
- **Alerts with early warning.** An alert rule per scope, Total occupancy or a zone: a limit, an
  early-warning percentage (80% by default), an on/off switch and an optional banner. Counts
  near the limit raise a warning before it is crossed. Alerts are recorded once, and admins
  acknowledge them from the banner or the alert history. They clear 5 seconds after the count
  drops, so a count hovering at a limit doesn't flap.
- **Administration.** Accounts with admin and user roles (anyone can view Live, Alerts and
  Zones), saved video sources in Settings, and an activity log of every sign-in and change,
  exportable as CSV or PDF.
- **Exports.** Per-second history as CSV, a PDF occupancy report, and per-frame metrics
  (timing, confidences, zone time series, track lifetimes) for analysis.
- **Runs anywhere.** RTSP/HTTP streams (with reconnects) and video files. CPU, Apple silicon
  (MPS) or NVIDIA (CUDA), chosen automatically. Docker image included.
- **Built to last.** One CLI, layered config (`crowdcount.toml`, env, flags), readable errors
  with exit codes, 250+ tests, and CI on Python 3.10 to 3.14.

<p align="center">
  <img src="docs/images/hero.jpg" width="820" alt="The CrowdCount Live page: an alert banner above four stat cards, the annotated camera view, and zones listed against their limits">
  <br><sub>The Live page while a zone is over its limit.</sub>
</p>

<p align="center">
  <img src="docs/images/zone-editor.jpg" width="820" alt="The Zones page: drawing a new polygon zone on the live camera frame, with the existing zones listed beside it">
  <br><sub>Drawing a zone on the live frame on the Zones page.</sub>
</p>

## Quickstart

Requires Python 3.10+ and an internet connection on first run (it downloads the model and a
sample video).

```bash
git clone https://github.com/nvmaditya/CrowdCount.git && cd CrowdCount
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
crowdcount demo
```

Open **http://localhost:8080**. The demo runs on OpenCV's public `vtest.avi` pedestrian clip,
with zones drawn for it. The terminal prints a one-time password for the `admin` account. Sign
in to draw zones and set alert rules.

> On Windows, activate with `.venv\Scripts\activate` instead of `source .venv/bin/activate`.
> With `make` installed, `make demo` does the last three steps. On Linux without a GPU, run
> `pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu` before
> `pip install -e .` to skip the multi-gigabyte CUDA build of PyTorch.

## Usage

```bash
# A video file, looping, with zones from a file
crowdcount run --source lobby.mp4 --loop --zones lobby-zones.json

# An IP camera, dashboard reachable from your network
crowdcount run --source "rtsp://user:pass@192.168.1.20/stream1" --host 0.0.0.0

# Detector only: preview window off, save the annotated video
crowdcount detect --source lobby.mp4 --no-display --output annotated.mp4

# Draw zones in an OpenCV window instead of the browser
crowdcount zones edit --source lobby.mp4

# Benchmark a model on the demo clip and write metrics CSVs
crowdcount detect --source demo --no-display --no-realtime --max-frames 300 \
    --model yolo26s.pt --metrics-out runs/yolo26s

# Forgot the admin password
crowdcount admin reset-password
```

Every command takes `--help`. A Dockerfile and compose file are included; see the
[deployment guide](docs/deployment.md).

## How it works

```mermaid
flowchart LR
    cam["RTSP camera · video file"] --> det["YOLO26<br/>person detection"]
    det --> trk["ByteTrack<br/>track IDs"]
    trk --> zon["Zones<br/>occupancy · unique tracks"]
    zon --> st[("Live state<br/>counts · 1 Hz history<br/>heatmap · alerts")]
    st --> api["FastAPI"]
    api --> ui["Dashboard<br/>MJPEG · charts · heatmap"]
    api --> adm["Alerts · Zones · Settings<br/>rules · zones · sources · users"]
    adm -. "edits zones.json<br/>(hot reload)" .-> zon
    st -. "alert records" .-> db[("data/ JSON<br/>users · config · audit log")]
```

The detector and the web server run in one process and share in-memory state; persistent
data (accounts, settings, logs, alert history) is plain JSON written atomically. Details are in
[docs/architecture.md](docs/architecture.md).

## Configuration

Flags override environment variables, which override `crowdcount.toml`, which overrides the
defaults:

```toml
# crowdcount.toml  (copy crowdcount.example.toml)
[video]
source = "rtsp://user:pass@192.168.1.20/stream1"

[detection]
model = "yolo26s.pt"     # yolo26 n, s, m (default), l, x
tracker = "bytetrack"    # or "botsort", or a tracker YAML
confidence = 0.45

[server]
host = "0.0.0.0"
```

Secrets and paths go in the environment or `.env` (see [`.env.example`](.env.example)):
`CROWDCOUNT_ADMIN_PASSWORD`, `CROWDCOUNT_JWT_SECRET`, `CROWDCOUNT_DATA_DIR`. Alert rules, zones,
video sources and users are managed in the app (Alerts, Zones and Settings). The full reference is in
[docs/configuration.md](docs/configuration.md).

## Performance

Processing throughput (detection, tracking, zone counting, annotation) on the 768×576 demo
clip, 300 frames, ByteTrack, measured with `crowdcount detect --source demo --no-display
--no-realtime --max-frames 300 --conf 0.35 --min-area 800 --model <model> --device <mps|cpu>`:

| Model | Apple M1 Pro, MPS | Apple M1 Pro, CPU | NVIDIA GPU |
| --- | ---: | ---: | ---: |
| yolo26n | 65 FPS | 28 FPS | _TODO: measure_ |
| yolo26s | 49 FPS | 17 FPS | _TODO: measure_ |
| yolo26m (default) | 26 FPS | 10 FPS | _TODO: measure_ |

<!-- TODO(maintainer): fill the NVIDIA column with `--device cuda:0` runs of the same command. -->

Larger frames and denser crowds are slower. Video files are paced to their native frame rate
unless you pass `--no-realtime`. On this hardware YOLO11 and YOLOv8 are within about 5% of
YOLO26 at the `n` and `s` sizes, and `yolov8m` is about 12% faster than `yolo26m` on the GPU.
YOLO26 is the default for its higher accuracy on Ultralytics' COCO benchmark. The side-by-side comparison, methodology and caveats are in
[docs/tracking.md](docs/tracking.md#choosing-a-model). This repository has no labelled ground
truth, so it reports no accuracy figures of its own (see the roadmap).

## Documentation

| Guide | Covers |
| --- | --- |
| [Architecture](docs/architecture.md) | Modules, the per-frame pipeline, live and persistent state, design choices |
| [Configuration](docs/configuration.md) | Every command, flag, config key and environment variable; exit codes; metrics files |
| [Zones](docs/zones.md) | Drawing zones, file format, counting rules, alert rules |
| [Detection and tracking](docs/tracking.md) | ByteTrack vs BoT-SORT, tuning, filters, model choice and benchmarks |
| [Dashboard](docs/dashboard.md) | The five pages, alert levels, roles, activity log, first sign-in, security model, upgrading from 1.x |
| [HTTP API](docs/api.md) | Endpoints, authentication, changes from 1.x |
| [Deployment](docs/deployment.md) | Docker, compose, systemd, multiple cameras, reverse proxy |
| [Troubleshooting](docs/troubleshooting.md) | Cameras, streams, models, counting and dashboard issues |

## Roadmap

Planned, not yet built:

- [ ] Multiple cameras in one process, with a camera switcher on the dashboard
- [ ] Optional sign-in for the dashboard and live stream
- [ ] Line-crossing (entry/exit) counts and dwell time per zone
- [ ] Alert notifications via webhook and email
- [ ] History that survives restarts (SQLite)
- [ ] Accuracy evaluation against a labelled dataset (counting error, ID switches)
- [ ] ONNX/TensorRT export for edge devices
- [ ] Prometheus metrics endpoint

## Contributing

Issues and pull requests are welcome. [CONTRIBUTING.md](CONTRIBUTING.md) covers setup
(`make install`), tests (`make test`, about 2 seconds, no GPU needed), style and commit
conventions. Release notes are in [CHANGELOG.md](CHANGELOG.md).

## License

CrowdCount is licensed under the [GNU Affero General Public License v3.0](LICENSE), the same
license as [Ultralytics YOLO](https://github.com/ultralytics/ultralytics), which it builds on. You
may use, modify and redistribute it. If you distribute a modified version, or run one as a
network service for others, you must offer its source code under the same license; the
dashboard footer links to the source. See [LICENSE](LICENSE) for the full terms.

## Credits

- [Ultralytics YOLO](https://github.com/ultralytics/ultralytics) (YOLO26, YOLO11, YOLOv8): detection models and tracker implementations
- [ByteTrack](https://arxiv.org/abs/2110.06864) (Zhang et al., ECCV 2022) and [BoT-SORT](https://arxiv.org/abs/2206.14651) (Aharon et al., 2022)
- [OpenCV](https://opencv.org/): video I/O and drawing; the demo uses its `vtest.avi` sample clip
- [FastAPI](https://fastapi.tiangolo.com/), [Chart.js](https://www.chartjs.org/), [Geist](https://vercel.com/font) (fonts), [Lucide](https://lucide.dev/) (icons), [ReportLab](https://www.reportlab.com/)

Built by [Aditya Khandelwal](https://github.com/nvmaditya).
