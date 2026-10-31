# Detection and tracking

CrowdCount uses [Ultralytics](https://docs.ultralytics.com/) YOLO models for person detection
and Ultralytics' built-in multi-object trackers to give each person a persistent ID across
frames. IDs are what make "unique visitors per zone" and motion trails possible.

## Pipeline per frame

```text
frame ──► YOLO (person class only, conf ≥ --conf, NMS at --iou)
      ──► tracker (ByteTrack by default) assigns track IDs
      ──► CrowdCount filters: confidence, minimum box area, aspect ratio
      ──► zones, counts, trails
```

The call is `model.track(frame, persist=True, classes=[0], conf=..., iou=..., tracker=...)`.
`persist=True` keeps tracker state between frames; class `0` is "person" in COCO.

## ByteTrack (default)

ByteTrack ([Zhang et al., ECCV 2022](https://arxiv.org/abs/2110.06864)) is a
tracking-by-detection method with no appearance model:

1. A Kalman filter predicts where each existing track should be in the new frame.
2. **First association:** high-confidence detections are matched to predicted tracks by IoU
   (Hungarian assignment).
3. **Second association:** tracks still unmatched are matched against the remaining
   *low*-confidence detections. This is ByteTrack's key idea: a partly occluded person often
   still produces a weak detection, and using it keeps their ID instead of dropping it.
4. Unmatched high-confidence detections start new tracks. Tracks unmatched for `track_buffer`
   frames are removed.

Because it is motion-only, it is fast. That makes it a good default for counting on CPUs and
laptops.

Ultralytics' defaults (`bytetrack.yaml`):

| Parameter | Default | Effect |
| --- | --- | --- |
| `track_high_thresh` | 0.25 | Score for the first association |
| `track_low_thresh` | 0.1 | Lowest score used in the second association |
| `new_track_thresh` | 0.25 | Score needed to start a new track |
| `track_buffer` | 30 | Frames a lost track is kept before its ID is retired |
| `match_thresh` | 0.8 | Association cost threshold |
| `fuse_score` | true | Combine detection score with IoU when matching |

### How CrowdCount's confidence threshold interacts with it

`--conf` (default 0.5; 0.35 in the demo) is passed to YOLO, so the tracker only sees
detections at or above it. That limits ByteTrack's second association to boxes between
`--conf` and `track_high_thresh`.

The obvious alternative is to give the tracker everything down to 0.1 and apply `--conf` only
when counting. We measured that on the full demo video (795 frames, yolov8n and yolov8m). It
did not reduce ID switches: unique track counts were identical for yolov8m and slightly
*higher* for yolov8n (25 vs 21 in one zone), with the same people counts. Low-score boxes
started extra tracks that were later counted under new IDs. So the tracker keeps receiving
detections at `--conf`. If your footage has heavy occlusion, try a custom tracker YAML (below)
and compare `run_summary.json` across runs.

## BoT-SORT (`--tracker botsort`)

BoT-SORT ([Aharon et al., 2022](https://arxiv.org/abs/2206.14651)) builds on ByteTrack's
association and adds **global motion compensation** (Ultralytics default `gmc_method:
sparseOptFlow`), which corrects predictions for a moving or shaking camera, plus optional
appearance re-identification (`with_reid`, off by default).

CrowdCount 1.x used BoT-SORT under the "ByteTrack" name. 2.0 makes ByteTrack the default and
names both honestly. Choose BoT-SORT for handheld, panning or vibrating cameras. On the fixed
demo camera, ByteTrack was about 20% faster with the same counts (see the table below).

## Custom tracker settings

Copy an Ultralytics tracker YAML, edit it, and pass its path:

```bash
cp "$(python -c 'import ultralytics, pathlib; print(pathlib.Path(ultralytics.__file__).parent / "cfg/trackers/bytetrack.yaml")')" my-tracker.yaml
# e.g. raise track_buffer to 60 to keep IDs through longer occlusions
crowdcount run --source 0 --tracker my-tracker.yaml
```

## CrowdCount's box filters

Applied after tracking, before counting:

| Filter | Flag / key | Default | Why |
| --- | --- | --- | --- |
| Confidence | `--conf`, `detection.confidence` | 0.5 | Drops uncertain detections |
| Minimum area | `--min-area`, `detection.min_box_area` | 1500 px | Drops distant figures and small false positives. Lower it for small or far-away people (the demo uses 800). |
| Aspect ratio (w/h) | `detection.min_aspect_ratio`, `max_aspect_ratio` | 0.15-2.0 | Drops boxes that are implausibly wide or thin for a person |

## Choosing a model

Measured with `crowdcount detect --source demo --no-display --no-realtime --max-frames 300
--model <m> --device <d> --tracker <t> --conf 0.35 --min-area 800 --metrics-out ...` on the
768×576 demo video, Apple M1 Pro (macOS 26.6), PyTorch 2.15, Ultralytics 8.4.194. "Processing
FPS" is `mean_processing_fps` from `run_summary.json`: frames divided by the time spent in
detection, tracking, zone counting, annotation and publishing. It excludes video decoding and
the first (warm-up) frame. Your numbers will differ with hardware, resolution and crowd density.

| Model | Device | Tracker | Processing FPS | Mean people per frame |
| --- | --- | --- | ---: | ---: |
| yolov8n | MPS | ByteTrack | 58.0 | 5.79 |
| yolov8n | MPS | BoT-SORT | 48.2 | 5.78 |
| yolov8s | MPS | ByteTrack | 51.4 | 5.87 |
| yolov8m | MPS | ByteTrack | 29.3 | 6.04 |
| yolov8n | CPU | ByteTrack | 29.6 | 5.79 |
| yolov8s | CPU | ByteTrack | 19.5 | 5.87 |
| yolov8m | CPU | ByteTrack | 11.0 | 6.04 |

"Mean people per frame" shows that larger models detect slightly more of the small figures in
this clip. It is **not** an accuracy measurement: this repository has no labelled ground truth,
so there are no precision, recall or counting-error figures. Reproduce or extend the table with
`--metrics-out` (see [configuration.md](configuration.md#metrics-output)).

<!-- TODO(maintainer): add a row for an NVIDIA GPU (e.g. --device cuda:0) if you have one. -->

## Known limitations

- **ID switches inflate unique counts.** Long occlusions, people crossing paths, or leaving
  and re-entering the frame can produce a new ID. "Unique tracks" is therefore an upper bound on
  unique people.
- **The box centre is a body-centre proxy.** For steep overhead cameras, consider drawing
  zones slightly larger than the floor area you mean.
- **Small people need a lower `--min-area`** (and possibly a larger model).
- **One source per process.** Run one `crowdcount run` per camera, on different `--port`s and
  `--data-dir`s.
