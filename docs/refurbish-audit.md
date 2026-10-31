# CrowdCount refurbish: audit and plan

Branch: `refurbish` (from `main` @ `9371787`). This file is the Phase 1 audit. It was
written before any code changed; the commit history on this branch follows the plan
below.

---

## 1. What actually runs today

Verified on macOS (Apple M1 Pro), Python 3.14, using the unmodified `requirements.txt`.

| Command | What it does | Works? |
| --- | --- | --- |
| `python run_app.py` | FastAPI server in a daemon thread + detector in the main thread with an OpenCV window | Only with `--source`; the default `Camera-Video.mp4` is not in the repo (`*.mp4` is gitignored) |
| `python run_app.py --server-only` | API + static frontend on `:8080`; dashboard at `/static/index.html` | Yes |
| `python run_app.py --detector-only` | Detector only | Yes, but nothing can see its output: the shared state is per-process, so a separately started server shows zeros |
| `python detector/integrated_detector.py` | Same detector, own argparse | Yes |
| `python people_detector_bytetrack.py` | Older standalone detector (no dashboard) | Yes |
| `python zone_manager.py` | OpenCV zone editor | Yes (needs a video/camera) |
| `python backend/api.py` | Server only | Yes |
| `python paper/generate_figures.py` | Paper figures/CSVs | Partially (defaults to the missing `Camera-Video.mp4`) |
| `python -m pytest` | 4 API smoke tests | 4 pass, with deprecation warnings |

A 30-frame headless run on OpenCV's `vtest.avi` (768x576) with `yolov8n.pt` worked:
about 25 to 28 instantaneous FPS after a 5.3 s first-frame warmup (CPU). During the
first tracking call, Ultralytics **pip-installed `lap` into the venv at runtime**,
because it is a tracker dependency that `requirements.txt` doesn't list.

There are five entry points for overlapping jobs.

## 2. Dead, duplicated, and experimental files

| File | Verdict |
| --- | --- |
| `2.0`, `pip` | Empty files from shell-redirect accidents (`pip install x>=2.0`). Delete. |
| `dp.py` | Unrelated backtracking puzzle (partitions 1..81 into 9 groups summing to 369). Delete. Not CrowdCount code. |
| `people_detector_bytetrack.py` | Predecessor of `detector/integrated_detector.py`: the same class minus dashboard, metrics and speed control. Despite the name, it uses BoT-SORT. Delete (superseded, not an experiment). |
| `detector/integrated_detector.py` | The real detector. Becomes the core of the package. |
| `zone_manager.py` | Zone editor; duplicates drawing code from the detector. Keep and fold in. |
| `run_app.py` | Orchestrator; replaced by a CLI. |
| `backend/middleware.py` | `get_current_user_optional`, `get_current_active_user`, `require_user_or_admin`, `RoleChecker`, `admin_only`, `user_or_admin` are unused. |
| `backend/api.py` | `ConnectionManager.broadcast` unused; `pandas` imported and never used. |
| `frontend/index.html` | `connectWebSocket()` defined, never called. |
| `IntegratedPeopleDetector` | `recent_track_ids`, `id_memory_frames`, module-level `run_detector()` unused. |

Nothing qualifies for `experiments/`: there is no half-finished idea in the repo, only
superseded copies. The paper tooling stays in `paper/`.

## 3. Where the docs disagree with the code

- `README.md` documents `people_detector.py`, which does not exist; it says the default model
  is `yolov8n.pt` (code: `yolov8m.pt`); claims "Python 3.8+"; says the license is AGPL-3.0
  while `LICENSE` is MIT; shows a stale file tree.
- **Tracking.** `README_BYTETRACK.md`, the filename, and the project description say
  ByteTrack. Both detectors pass `tracker="botsort.yaml"`
  (`detector/integrated_detector.py:318`, `people_detector_bytetrack.py:244`).
- `README_BYTETRACK.md` has an FPS table (60/45/30/20/15) that was never measured, a
  "Coming soon" list (heatmap, analytics dashboard) that already exists, and a link to an old
  repo name.
- Every dashboard/admin doc uses port **8000**; the code uses **8080**. The detector overlay
  burns `Dashboard: http://localhost:8000` into every frame (`integrated_detector.py:609`).
- `README_DASHBOARD.md` says you can run detector and server separately. You can't share
  state across processes, so the dashboard stays empty.
- Admin docs present camera management as configuring the detector. Cameras are stored in
  `data/config.json` and **nothing reads them**. The same goes for `log_retention_days`.
- Admin zone CRUD writes `zones.json`, but the running detector loaded zones once at
  startup and never re-reads them. The "Add Zone" modal is a stub telling you to use the
  detector's drawing mode.
- Zone `color` is documented as RGB; OpenCV draws it as BGR (`zone_manager.py:26-31`
  comments even say `(255, 0, 0) # Blue`).
- `IMPLEMENTATION_SUMMARY.md` / `INSTALLATION_TESTING.md` are status reports for a past task,
  not user docs.

## 4. Bugs

Functional:

1. **Zones with zero people disappear** from `/zones` and the dashboard: occupancy is rebuilt
   from scratch each frame and only non-empty zones are published (`integrated_detector.py:183`,
   `shared_state.py:116`). A zone's visitor total vanishes with it.
2. **History is sampled every frame**, not at 1 Hz as `shared_state.py:49` claims. The
   3600-entry deque holds about 2 minutes at 30 FPS, so "last hour" and the PDF's "peak
   count" are wrong, and `/history?limit=60` covers a couple of seconds.
3. **Alerts are only recorded when someone polls `GET /alerts`** (`api.py:172`), so a
   GET has side effects, and with no dashboard open no alert history is written.
4. Bad video source or camera: prints an error and exits **0** (`integrated_detector.py:479`).
   A missing model path gives a raw traceback. A malformed `zones.json` crashes with a raw
   `JSONDecodeError`.
5. `--detector-only` passes a literal `8080` instead of `--port` (`run_app.py:151`).
6. `--speed` is `int(speed)` as a frame stride, so `1.5` means `1` and `0.5` means `1`.
   The output video keeps the source FPS, so recordings play back too fast when frames are skipped.
7. `conf_threshold or DEFAULT` silently replaces an explicit `0` (`integrated_detector.py:68`).
8. Heatmap default dimensions are stored as `(h, w) = (1920, 1080)`, which is portrait
   (`shared_state.py:46`). The heatmap is a free-floating colour blob with no frame
   underneath, so it's hard to read.
9. In detector drawing mode, `finish_zone` prints "Enter zone name" but never reads input.
10. `zones.json` geometry is for a roughly 1700x990 frame. On any smaller source, zones fall
    partly or fully off-frame without warning (Zone_2 reads 0 on every frame of `vtest.avi`).
11. Admin log CSV/PDF export uses `window.open`, which can't send the bearer token, so it
    **always 401s** (`frontend/admin.js:649`).
12. `showNotification("error", …)` builds `alert-error`, which isn't a Bootstrap class, so
    errors render unstyled (`admin.js:717`).
13. The admin `ZONES_FILE` is hard-wired to the repo root (`backend/admin.py:45`), so with
    `--zones other.json` the admin panel edits a different file than the detector reads.
    All `data/` paths resolve relative to the source tree, which breaks once installed.
14. JSON stores do read-modify-write with a lock around each half but not the whole, and write
    non-atomically. The detector thread (alerts) and API (audit log) can lose each other's
    entries. A truncated `users.json` reads as "no users", and then nobody can log in.
15. "Remember me" on the login page does nothing.

Security:

- JWT secret defaults to a literal string (`backend/auth.py:25`), so anyone can mint an admin
  token for a default install.
- A default `admin` / `admin123` is created automatically and **printed on the login page**.
- `POST /alerts/threshold` and `POST /heatmap/reset` are unauthenticated, and the threshold
  change isn't persisted.
- CORS `allow_origins=["*"]` with `allow_credentials=True`.
- `innerHTML` with unescaped zone names, usernames, camera names and log details
  (`index.html`, `admin.js`), so a stored XSS is reachable through the admin API.
- `X-Forwarded-For` is trusted unconditionally for audit-log IPs.

Dependencies:

- Missing: `lap` (auto-installed at runtime by Ultralytics).
- Unmaintained: `python-jose`, `passlib` (passlib is why `bcrypt==4.0.1` is pinned).
- Unused: `pandas`, `python-multipart`.
- No upper bounds anywhere; nothing records a known-good set.
- Deprecated APIs: `@app.on_event`, Pydantic v1 `.dict()`, `datetime.utcnow()`.

Code health: four `sys.path.insert` hacks, about 60 `print` calls, no type hints outside
`metrics_logger.py`, a module-level singleton the tests reset by poking private attributes.

## 5. Decisions

**Tracker: default to ByteTrack, keep BoT-SORT selectable.** The project is named and
described around ByteTrack, but the code silently ran BoT-SORT. I'll add
`--tracker {bytetrack,botsort,<path.yaml>}` and default to `bytetrack`. It matches the
project's stated design and skips BoT-SORT's global-motion compensation, which costs CPU
time. This is a behaviour change, recorded in the CHANGELOG. (Ultralytics' own default is BoT-SORT.)

**Demo video: download, don't commit.** The quickstart needs a source that exists on a clean
clone. `crowdcount demo` fetches OpenCV's `vtest.avi` (BSD-licensed sample, about 8 MB) into a
cache directory on first use and runs with bundled zones sized for its 768x576 frame. No
binaries go into git.

**Docker: write it, keep it out of the quickstart.** There is no Docker on this machine, so I
can't verify `docker build`. I'll add a Dockerfile plus a CI job that builds it, keep Docker
out of the README quickstart, and flag it as unverified locally. The verified one-command
path is `pip install -e .` plus `crowdcount demo`, with a `Makefile` for dev tasks.

**Storage stays JSON**, made correct: one lock spanning read-modify-write, atomic
`os.replace` writes, and a configurable data directory. SQLite would be cleaner but is a
rewrite with no visible gain; it goes in the roadmap.

**Zone colours are RGB** in `zones.json` (as documented), converted to BGR for drawing.

**License: not touched.** MIT `LICENSE` versus Ultralytics AGPL-3.0 is for the owner to decide.

## 6. Prioritized plan

P0 is required for "easy to run, and true".
P1 is the high-visibility improvements.
P2 is done if time allows.

### Phase 2: clean up and restructure
- [P0] Delete `2.0`, `pip`, `dp.py`, `people_detector_bytetrack.py`; tidy `.gitignore`.
- [P0] `pyproject.toml` (hatchling, `src/` layout), bounded dependency ranges, `lap` added,
  `python-jose`/`passlib` replaced by `PyJWT`/`bcrypt`, `pandas`/`python-multipart` dropped;
  `requirements.lock` frozen from the environment that ran the verification.
- [P0] Package `src/crowdcount/`: `config`, `zones` (pure geometry + occupancy),
  `state`, `detector`, `pipeline`, `annotate`, `video`, `metrics`, `editor`, `demo`, `cli`,
  `server/` (app factory, public API, admin, auth, deps, models, audit log, storage),
  `web/` (frontend as package data, served at `/`). No `sys.path` hacks.
- [P0] One entry point, `crowdcount`, with subcommands `run`, `detect`, `serve`, `demo`,
  `zones {edit,list,validate}`, `admin reset-password`.
- [P0] `.env.example` for the JWT secret, initial admin password, and data dir.

### Phase 3: make it better
- [P0] Fix bugs 1-15 above and every security item.
- [P0] `logging` everywhere, type hints and docstrings on public functions, one config
  source: `crowdcount.toml` overridden by CLI flags.
- [P0] Graceful errors: unopenable camera/file/URL, missing weights, invalid zones, port in
  use; clear message and non-zero exit.
- [P0] Tests: point-in-polygon (inside/outside/edge/concave), disabled zones, overlapping
  zones, per-frame occupancy reset, visitor dedup by track ID, 1 Hz history, edge-triggered
  alerts, auth, admin RBAC, API contract. Tests must not need torch.
- [P0] GitHub Actions: ruff lint + format check, pytest on a Python matrix; a demo smoke job.
- [P1] Live annotated video in the dashboard (MJPEG), heatmap blended over the latest frame.
- [P1] Zones hot-reload: admin-panel and editor changes apply to the running detector.
- [P1] Dashboard/login/admin restyle with one shared theme, real empty and offline states,
  occupancy vs threshold per zone; vendored frontend assets so it works offline.
- [P1] `--device` (cpu/mps/cuda), `--tracker`, `--loop`, `--stride` flags.
- [P1] Dockerfile + compose (unverified locally; CI builds it).
- [P2] Browser zone editor in the admin panel (draw on the live frame).
- [P2] `--camera NAME` to use a source saved in the admin panel.
- [P2] Measured FPS for the README, with hardware and command.

### Phase 4: documentation
- README storefront; `docs/` (architecture, configuration, zones, dashboard & admin, tracking,
  API, troubleshooting, deployment); `CONTRIBUTING.md`; `CHANGELOG.md` v2.0.0; delete the six
  old markdown files. `paper/` featured honestly: it holds a code-grounded technical write-up and
  figure generators, not a finished paper.

### Verification before calling it done
Clone the branch into a temp dir, create a fresh venv, run the README quickstart verbatim,
hit the dashboard and API, and run the test suite and linters.

### Commit order (tests green at each step)
plan → remove stray files → pyproject + lock → src layout (no behaviour change) → CLI →
config + logging + errors → bug fixes → security → tests → server features (stream, reload) →
frontend → CI → Docker → docs → delete old docs.
