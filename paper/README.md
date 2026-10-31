# Research material

This folder supports an academic write-up of CrowdCount. It contains no finished paper.

| File | What it is |
| --- | --- |
| [`PAPER_TECHNICAL_EXTRACTION.md`](PAPER_TECHNICAL_EXTRACTION.md) | A code-grounded technical description of the system (pipeline, tracking, zone logic, heatmap, API, admin/RBAC) with pseudocode and a section-by-section mapping, written as source material for a paper. It describes the **1.x** code layout; see its header note. |
| [`generate_figures.py`](generate_figures.py) | Regenerates figures and CSVs: zone overlay, zone areas, architecture and frame-flow diagrams (SVG), activity-log and alert-history summaries. |
| [`diagrams_svg.py`](diagrams_svg.py) | Dependency-free SVG diagrams used by `generate_figures.py`. |
| [`capture_ui_screenshots.py`](capture_ui_screenshots.py) | Captures the dashboard, sign-in and admin pages as PNGs (needs `pip install playwright`). |

## Regenerate figures

```bash
python paper/generate_figures.py --video path/to/video.mp4      # backdrop for the zone overlay (optional)
```

Defaults: zones from `./zones.json` (or `examples/zones.example.json`), logs and alerts from
`./data/`. Outputs go to `paper/figures/` and `paper/data/` (git-ignored).

## Measure throughput for an evaluation section

`--metrics-out` writes per-frame timing, per-detection confidence, zone time series, track
lifetimes and a run summary:

```bash
crowdcount detect --source path/to/video.mp4 --no-display --no-realtime \
    --model yolov8n.pt --max-frames 500 --metrics-out runs/yolov8n
```

Repeat per model or tracker and compare `mean_processing_fps` in each `run_summary.json`.
Accuracy figures (precision/recall, counting error) need labelled ground truth, which this
repository does not include.
