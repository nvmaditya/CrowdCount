# Changelog

All notable changes to CrowdCount are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [2.0.0] - 2026-10-31

A ground-up refurbish of the 1.x academic project into an installable, tested, documented
tool. The audit that motivated it is in [docs/refurbish-audit.md](docs/refurbish-audit.md).

### Highlights

- One command, `crowdcount`, installable with `pip install -e .`, and a demo that works on a
  fresh clone: `crowdcount demo`.
- Live annotated video in the dashboard, zones drawn in the browser on the live frame and
  applied to the running detector without a restart, and a redesigned UI in light and dark.
- Real authentication: no default password, a generated token secret, maintained crypto
  libraries.
- 250+ tests and CI on Python 3.10, 3.12 and 3.14, plus an end-to-end demo job and a Docker job.

### Breaking changes

- **License is now AGPL-3.0** (was MIT), the license of the Ultralytics YOLO models and trackers
  CrowdCount depends on.
- **Entry points.** `run_app.py`, `detector/integrated_detector.py`,
  `people_detector_bytetrack.py` and `zone_manager.py` are replaced by `crowdcount run | demo |
  detect | serve | zones | admin` ([configuration](docs/configuration.md#commands)).
- **Default tracker is ByteTrack.** 1.x was described as ByteTrack but ran BoT-SORT
  (`tracker="botsort.yaml"`). Use `--tracker botsort` for the old behaviour.
- **API moved under `/api`.** `/` serves the dashboard. `POST /alerts/threshold` is removed; use
  `PUT /api/admin/rules/global` (or the legacy `PUT /api/admin/config/thresholds`). `/api/heatmap` returns JPEG. See [API changes](docs/api.md#changes-from-1x).
- **Default bind address is `127.0.0.1`** (was `0.0.0.0`). Pass `--host 0.0.0.0` to expose it.
- **Default model is YOLO26** (`yolo26m.pt`, was `yolov8m.pt`; the demo uses `yolo26n.pt`), for
  higher COCO accuracy at the same size. Pass `--model yolov8m.pt` for the old one. Requires
  ultralytics 8.4.142 or newer.
- **`--source` has no default.** Pass a video file or stream URL (or use `crowdcount demo`).
- **`--speed` is replaced by `--stride`** (integer frame stride, which is what `--speed` did).
- **Zone `color` is RGB**, as documented; 1.x passed it to OpenCV as BGR.
- **No `admin`/`admin123` account.** The first admin uses `CROWDCOUNT_ADMIN_PASSWORD` or a
  generated password printed once. Existing `data/users.json` files keep working.
- **Passwords must be 8-72 bytes** (was at least 6).
- `zones.json` is no longer tracked; the 1.x zones are in `examples/zones.example.json`.

### Added

- `crowdcount demo`: downloads OpenCV's `vtest.avi` sample (SHA-256 verified) and runs with
  zones drawn for it.
- Configuration via `crowdcount.toml`, environment variables and `.env`, with validation that
  names the bad setting ([configuration](docs/configuration.md)).
- `--device auto` (CUDA, then Apple MPS, then CPU), `--tracker` (`bytetrack`, `botsort` or a
  YAML), `--loop`, `--stride`, `--realtime`, `--display`, `--max-frames`, `--metrics-out`.
- MJPEG live stream (`/api/stream`), raw snapshot (`/api/snapshot`), zone layout
  (`/api/zones/layout`), combined `/api/summary` with detector status, peak count and FPS.
- Zone hot-reload: edits to the zones file (the Zones page, `crowdcount zones edit`, or by hand)
  apply to a running detector within a second; invalid edits are ignored.
- Browser zone editor on the Zones page. Per-zone alert limits; renaming a zone carries its
  rule over.
- `crowdcount run --camera NAME` starts a source saved under Settings → Video sources.
- `crowdcount zones list | validate [--source] | edit`, `crowdcount admin reset-password`.
- Change-password endpoint and page, alert acknowledgement, activity-log paging (`total`).
- A redesigned dashboard in five pages (Live, Alerts, Zones, Settings, Sign in) with a shared
  sidebar, light and dark themes that follow the OS until you choose, and empty, offline and
  error states. Geist fonts, Lucide icon paths and Chart.js are vendored, so it works offline.
- **Near-limit early warnings.** Alert rules gain an early-warning percentage (80% by default).
  A count from `warn_at` up to the limit raises a *near* alert before the limit is crossed;
  above it, *over*. Alerts rise at once and clear after 5 seconds, so a count hovering at a
  limit doesn't flap. Rules can be switched off, or set not to show a banner
  ([dashboard](docs/dashboard.md#alerts)).
- **The Alerts page**: risk per scope, active alerts, the alert rules with an editor, and the
  history (filterable, exportable as CSV).
- **Acknowledging from the dashboard.** The Live banner and the Alerts page acknowledge an alert
  (admins); each alert has the same id as its history record.
- API: public `GET /api/alerts/history`; `PUT /api/admin/rules/global` and `PUT`/`DELETE
  /api/admin/rules/zones/{name}`; `rules` in `/api/summary`; `running` on saved cameras;
  `confidence`, `min_box_area` and `stride` in the config's `runtime`; a `THRESHOLD_WARNING`
  activity-log entry for near-limit alerts ([API](docs/api.md)).
- `config.json` gains `rule_options` (`warn_percent`, `enabled`, `banner`). The 1.x `thresholds`
  keys are unchanged, and a file without `rule_options` loads with default options
  ([configuration](docs/configuration.md#configjson)).
- Run summary (`run_summary.json`) with mean processing FPS in `--metrics-out`.
- Dockerfile, compose file, Makefile, GitHub Actions CI, Dependabot.
- Documentation: README, `docs/` (architecture, configuration, zones, tracking, dashboard, API,
  deployment, troubleshooting), CONTRIBUTING.
- A measured YOLOv8 vs YOLO11 vs YOLO26 comparison (n/s/m, CPU and Apple MPS) ([tracking](docs/tracking.md#choosing-a-model)).

### Changed

- **The `alerts` payload.** `GET /api/alerts` and the summary's `alerts` now include near-limit
  alerts, and each entry has `id`, `level` (`near` or `over`), `since`, `acknowledged` and
  `banner`. `has_alerts` and `exceeded` still mean "over its limit".
- **The admin panel is replaced by Settings, Zones and Alerts.** Video sources, detection
  details, users, data retention and the activity log are in Settings (admins; everyone signed
  in gets *Your account*); zones are on the Zones page and thresholds on the Alerts page.
  `/admin.html` redirects to `/settings.html`.
- Alerts and Zones can be viewed by anyone; the public alert history leaves out who
  acknowledged each alert.

### Fixed

- Zones with nobody in them disappeared from the API and dashboard, taking their visitor
  totals with them.
- The heatmap was a coverage mask: `cv2.circle` overwrote the accumulator with 1.0, so every
  visited pixel became equally "hot". It now accumulates, displayed on a log scale over the
  latest frame.
- History was recorded every frame while claiming one sample per second, so the "last hour"
  buffer held about two minutes and the PDF's peak and average were skewed. It is now one bucket
  per second holding the peak.
- Alerts were persisted only when something polled `GET /alerts`, so with no dashboard open
  no alert history was recorded. The detector now records alerts, once per breach.
- Admin log CSV/PDF export always returned 401 (`window.open` can't send the bearer token).
- Admin zone edits wrote a hard-wired repo-root file that the running detector never re-read.
- Saved cameras and log retention were stored but never used.
- A bad video source exited with status 0; a missing model printed a traceback;
  a malformed `zones.json` crashed with a raw `JSONDecodeError`.
- `--detector-only` ignored `--port`, and the detector burned "Dashboard: http://localhost:8000"
  into every frame (the server ran on 8080).
- An explicit `--conf 0` was silently replaced by the default.
- Recorded video played too fast when frames were skipped.
- The detector's "Enter zone name" prompt never read input.
- Concurrent JSON writes from the detector thread and request handlers could lose entries; a
  truncated `users.json` read as "no users" and locked everyone out.
- Ultralytics pip-installed `lap` into the environment at runtime; it is now a declared
  dependency, and runtime auto-install is disabled.
- `docker stop` / SIGTERM now shuts down cleanly.

### Security

- Removed the hardcoded JWT secret and the `admin`/`admin123` default account (which was also
  printed on the login page).
- Replaced `python-jose` and `passlib` (both unmaintained; passlib forced a `bcrypt==4.0.1` pin)
  with PyJWT and bcrypt.
- Token revocation on logout via `jti`; equal-time failed logins; password length limits.
- Threshold changes and heatmap reset require sign-in (they were public).
- Removed the wildcard CORS policy with credentials.
- Removed stored-XSS paths: user-controlled names and log details were inserted with
  `innerHTML`. The UI now builds DOM nodes, and the server sends a strict Content-Security-Policy.
- `X-Forwarded-For` is trusted only with `CROWDCOUNT_TRUST_PROXY`.
- Secrets and password files are written with owner-only permissions.

### Removed

- Bootstrap and Bootstrap Icons, and the admin panel pages and scripts that used them.
- `dp.py` (an unrelated puzzle solver), and the stray empty files `2.0` and `pip`.
- `people_detector_bytetrack.py` (an older copy of the integrated detector).
- Unused dependencies `pandas` and `python-multipart`.
- The six 1.x markdown guides, replaced by the README and `docs/`.

## [1.0.0] - 2026-04-20

The original academic project: YOLOv8 people detection with tracking, polygon zones drawn in
an OpenCV window, a FastAPI dashboard (counts, charts, heatmap, alerts, CSV/PDF export) and an
admin panel with JWT auth, roles, and activity logging.

[2.0.0]: https://github.com/nvmaditya/CrowdCount/compare/9371787...v2.0.0
[1.0.0]: https://github.com/nvmaditya/CrowdCount/tree/9371787
