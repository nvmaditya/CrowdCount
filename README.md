<h1 align="center">
  <img src="src/crowdcount/web/favicon.svg" width="56" alt=""><br>
  CrowdCount
</h1>

<p align="center">
  <strong>Real-time people counting for any camera: YOLOv8 detection, ByteTrack tracking, polygon zones and a live web dashboard, in one command.</strong>
</p>

<p align="center">
  <a href="https://github.com/nvmaditya/CrowdCount/actions/workflows/ci.yml"><img src="https://github.com/nvmaditya/CrowdCount/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <img src="https://img.shields.io/badge/python-3.10%20%7C%203.12%20%7C%203.14-3776AB?logo=python&logoColor=white" alt="Python 3.10+">
  <a href="LICENSE"><img src="https://img.shields.io/github/license/nvmaditya/CrowdCount" alt="License"></a>
  <img src="https://img.shields.io/badge/code%20style-ruff-D7FF64" alt="Ruff">
</p>

<!--
TODO(maintainer): replace this screenshot with a hero GIF at docs/images/demo.gif.
What to record (about 15 s, 1280 px wide, under 8 MB):
  1. Run `crowdcount demo` and open http://localhost:8080 in dark mode.
  2. Hold on the live view for a few seconds: boxes, track IDs, trails, zone cards updating.
  3. Set "East plaza" to 2 in Admin > Thresholds beforehand so the red alert banner appears on camera.
  4. Cut to Admin > Zones, draw a new zone on the frame, save, and return to the dashboard to show it counting live.
Tools: Kap, or macOS screen recording (Shift+Cmd+5) converted with gifski.
-->
<p align="center">
  <img src="docs/images/dashboard.jpg" width="920" alt="CrowdCount dashboard: live annotated video with tracked people and zones, zone occupancy against thresholds, people-over-time chart, and an occupancy heatmap">
</p>

## Why CrowdCount

Knowing how many people are in a space, and where, matters for safety limits, queues,
staffing and space planning. Commercial people counters are closed boxes; research code stops
at a notebook. CrowdCount sits in between: point it at a webcam, a video file or an RTSP
camera, draw the areas you care about, and get live occupancy, alerts and exports in a browser.
It is fully local, open source, and built to be read and extended.

## Features

- **Detection and tracking.** YOLOv8 (`n` to `x`, or any COCO-trained Ultralytics weights) finds
  people; ByteTrack, or BoT-SORT for moving cameras, gives each one a persistent ID.
- **Polygon zones.** Draw them on the live frame in the browser or in an OpenCV editor.
  Each zone gets live occupancy and a unique-track count; zones may overlap. Edits apply to the
  running detector within a second, with no restart.
- **Live dashboard.** Annotated MJPEG video, KPIs, per-zone bars against thresholds, a
  10-minute history chart and a cumulative heatmap over the scene. Light and dark themes, and
  it works offline (no CDNs).
- **Alerts.** A total threshold and per-zone thresholds. Each breach is recorded once, shown
  as a banner, and kept in an acknowledgeable alert history.
- **Administration.** Accounts with admin and user roles, saved camera sources, and an audit
  log of every sign-in and change, exportable as CSV or PDF.
- **Exports.** Per-second history as CSV, a PDF occupancy report, and per-frame metrics
  (timing, confidences, zone time series, track lifetimes) for analysis.
- **Runs anywhere.** Webcams, files, and RTSP/HTTP streams with reconnects. CPU, Apple silicon
  (MPS) or NVIDIA (CUDA), chosen automatically. Docker image included.
- **Built to last.** One CLI, layered config (`crowdcount.toml`, env, flags), readable errors
  with exit codes, 175 tests, and CI on Python 3.10 to 3.14.

<p align="center">
  <img src="docs/images/zone-editor.jpg" width="820" alt="Admin panel zone editor: drawing a new polygon zone on the live camera frame, with existing zones listed">
  <br><sub>Drawing a zone on the live frame in the admin panel.</sub>
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
in to draw zones and set thresholds.

> With `make` installed, `make demo` does the last three steps. On Linux without a GPU, run
> `pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu` before
> `pip install -e .` to skip the multi-gigabyte CUDA build of PyTorch.

## Usage

```bash
# Your webcam
crowdcount run --source 0

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
    --model yolov8s.pt --metrics-out runs/yolov8s

# Forgot the admin password
crowdcount admin reset-password
```

Every command takes `--help`. A Dockerfile and compose file are included; see the
[deployment guide](docs/deployment.md).

## How it works

```mermaid
flowchart LR
    cam["Camera · file · RTSP"] --> det["YOLOv8<br/>person detection"]
    det --> trk["ByteTrack<br/>track IDs"]
    trk --> zon["Zones<br/>occupancy · unique tracks"]
    zon --> st[("Live state<br/>counts · 1 Hz history<br/>heatmap · alerts")]
    st --> api["FastAPI"]
    api --> ui["Dashboard<br/>MJPEG · charts · heatmap"]
    api --> adm["Admin panel<br/>zones · thresholds · users"]
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
model = "yolov8s.pt"     # n, s, m (default), l, x
tracker = "bytetrack"    # or "botsort", or a tracker YAML
confidence = 0.45

[server]
host = "0.0.0.0"
```

Secrets and paths go in the environment or `.env` (see [`.env.example`](.env.example)):
`CROWDCOUNT_ADMIN_PASSWORD`, `CROWDCOUNT_JWT_SECRET`, `CROWDCOUNT_DATA_DIR`. Thresholds, cameras
and users are managed in the admin panel. The full reference is in
[docs/configuration.md](docs/configuration.md).

## Performance

Processing throughput (detection, tracking, zone counting, annotation) on the 768×576 demo
clip, 300 frames, ByteTrack, measured with `crowdcount detect --source demo --no-display
--no-realtime --max-frames 300 --conf 0.35 --min-area 800 --model <model> --device <mps|cpu>`:

| Model | Apple M1 Pro, MPS | Apple M1 Pro, CPU | NVIDIA GPU |
| --- | ---: | ---: | ---: |
| yolov8n | 58 FPS | 30 FPS | _TODO: measure_ |
| yolov8s | 51 FPS | 20 FPS | _TODO: measure_ |
| yolov8m | 29 FPS | 11 FPS | _TODO: measure_ |

<!-- TODO(maintainer): fill the NVIDIA column with `--device cuda:0` runs of the same command. -->

Larger frames and denser crowds are slower. Video files are paced to their native frame rate
unless you pass `--no-realtime`. Methodology, the BoT-SORT comparison and caveats are in
[docs/tracking.md](docs/tracking.md#choosing-a-model). There are no accuracy figures: the
repository has no labelled ground truth (see the roadmap).

## Research

The [`paper/`](paper/) folder holds material for an academic write-up of this system:

- a [code-grounded technical description](paper/PAPER_TECHNICAL_EXTRACTION.md) of the
  architecture, detection and tracking pipeline, zone logic, heatmap and access control, with
  pseudocode and a section-by-section outline (written against the 1.x code);
- scripts that regenerate its figures: architecture and frame-flow diagrams, zone overlays,
  activity and alert summaries, and UI screenshots.

It is source material, not a published paper.
<!-- TODO(maintainer): if the paper is published or on arXiv, link the PDF/DOI here. -->

## Documentation

| Guide | Covers |
| --- | --- |
| [Architecture](docs/architecture.md) | Modules, the per-frame pipeline, live and persistent state, design choices |
| [Configuration](docs/configuration.md) | Every command, flag, config key and environment variable; exit codes; metrics files |
| [Zones](docs/zones.md) | Drawing zones, file format, counting rules, thresholds |
| [Detection and tracking](docs/tracking.md) | ByteTrack vs BoT-SORT, tuning, filters, model choice and benchmarks |
| [Dashboard and admin](docs/dashboard.md) | First sign-in, roles, audit log, security model, upgrading from 1.x |
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

See [LICENSE](LICENSE). CrowdCount depends on [Ultralytics YOLO](https://github.com/ultralytics/ultralytics),
which is licensed under AGPL-3.0; review its terms for your use case.
<!-- TODO(maintainer): licensing decision pending (MIT LICENSE file vs. AGPL-3.0 Ultralytics dependency). -->

## Credits

- [Ultralytics YOLOv8](https://github.com/ultralytics/ultralytics): detection models and tracker implementations
- [ByteTrack](https://arxiv.org/abs/2110.06864) (Zhang et al., ECCV 2022) and [BoT-SORT](https://arxiv.org/abs/2206.14651) (Aharon et al., 2022)
- [OpenCV](https://opencv.org/): video I/O and drawing; the demo uses its `vtest.avi` sample clip
- [FastAPI](https://fastapi.tiangolo.com/), [Bootstrap](https://getbootstrap.com/), [Bootstrap Icons](https://icons.getbootstrap.com/), [Chart.js](https://www.chartjs.org/), [ReportLab](https://www.reportlab.com/)

Built by [Aditya Khandelwal](https://github.com/nvmaditya).
