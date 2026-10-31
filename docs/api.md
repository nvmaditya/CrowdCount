# HTTP API

Every JSON endpoint lives under `/api`. A running server also serves interactive docs at
**`/docs`** (Swagger UI) and the schema at `/openapi.json`. Those are generated from the code,
so they are always current; this page is the overview.

## Authentication

```bash
TOKEN=$(curl -s -X POST localhost:8080/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username": "admin", "password": "..."}' | python -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')

curl -H "Authorization: Bearer $TOKEN" localhost:8080/api/admin/zones
```

Tokens are HS256 JWTs valid for 24 hours. `POST /api/auth/logout` revokes the token.
Errors use FastAPI's shape: `{"detail": "..."}` (validation errors give a list).

| Status | Meaning |
| --- | --- |
| 401 | Missing, invalid, expired or revoked token |
| 403 | Signed in, but not an admin |
| 404 | No such resource, or no data yet (heatmap, snapshot, export) |
| 409 | Name already taken (user, zone, camera) |
| 422 | Request failed validation |

## Dashboard (no login)

| Method | Path | Returns |
| --- | --- | --- |
| GET | `/api/health` | `{"status": "ok", "version", "detector": "<state>"}`: a liveness probe |
| GET | `/api/summary` | Everything the dashboard shows: counts, peak, zones, active alerts, thresholds, detector status, heatmap start time |
| GET | `/api/count` | `{"total_count", "timestamp", "detection_running"}` |
| GET | `/api/zones` | `{"zones": {name: {"current", "total_visitors", "threshold"}}, "timestamp"}` |
| GET | `/api/zones/layout` | Zone definitions (name, RGB colour, enabled, polygon) |
| GET | `/api/history?limit=300` | One sample per second (the peak in that second), oldest first; `limit` 1-3600 |
| GET | `/api/alerts` | Currently breached thresholds. Read-only, with no side effects. |
| GET | `/api/coordinates` | Box centres of the people in the latest frame |
| GET | `/api/heatmap` | JPEG heatmap over the latest frame (404 until someone is detected) |
| GET | `/api/snapshot` | JPEG of the latest raw frame (404 before the first frame) |
| GET | `/api/stream?fps=15` | Live annotated video, `multipart/x-mixed-replace` (MJPEG); `fps` up to 30. Use as an `<img src>`. |
| GET | `/api/export/csv` | Last hour of per-second history as CSV (404 if empty) |
| GET | `/api/export/pdf` | One-page occupancy report |
| WS | `/api/ws` | Pushes the `/api/summary` payload once a second |

`/api/summary` example (trimmed):

```json
{
  "total_count": 5,
  "peak_count": 8,
  "peak_at": "2026-10-31T16:11:33+05:30",
  "zones": {"East plaza": {"current": 3, "total_visitors": 43, "threshold": 2}},
  "alerts": {"East plaza": {"type": "zone", "zone": "East plaza", "threshold": 2, "current": 3, "exceeded": true}},
  "global_threshold": 50,
  "status": {"state": "running", "source": "vtest.avi", "model": "yolo26n.pt", "tracker": "bytetrack",
             "device": "mps", "fps": 9.6, "frame_width": 768, "frame_height": 576},
  "version": "2.0.0"
}
```

## Signed-in users

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/api/auth/me` | Current user |
| POST | `/api/auth/password` | `{"current_password", "new_password"}` (8-72 bytes) |
| POST | `/api/auth/logout` | Revokes the token |
| POST | `/api/heatmap/reset` | Any role |

## Admin

| Method | Path | Body / query |
| --- | --- | --- |
| GET / POST | `/api/admin/users` | POST `{"username", "password", "role": "user"\|"admin"}` |
| DELETE | `/api/admin/users/{id}` | Not yourself, not the last admin |
| GET / PUT | `/api/admin/config/thresholds` | PUT `{"global_threshold": 30, "zone_thresholds": {"Gate": 5, "Old": null}}`; `null` removes |
| GET / POST | `/api/admin/zones` | POST `{"name", "points": [[x, y], ...], "color": [r, g, b], "enabled": true}` |
| PUT / DELETE | `/api/admin/zones/{name}` | PUT any subset of the fields (renaming carries the threshold) |
| GET / POST | `/api/admin/cameras` | POST `{"name", "source_url", "enabled"}` |
| PUT / DELETE | `/api/admin/cameras/{id}` | |
| GET | `/api/admin/logs` | `category`, `action`, `user_id`, `start_date`, `end_date`, `limit` (≤1000), `offset`; returns `total` for paging |
| GET | `/api/admin/logs/export/csv`, `/pdf` | `category`, `start_date`, `end_date` |
| POST | `/api/admin/logs/cleanup?retention_days=30` | Delete older entries now |
| GET | `/api/admin/alerts/history` | `alert_type` (`global` or a zone name), dates, `limit` |
| POST | `/api/admin/alerts/{id}/acknowledge` | |
| GET | `/api/admin/config` | Thresholds, cameras, retention, plus runtime paths and model |
| GET / PUT | `/api/admin/config/retention` | PUT `?retention_days=N` (applied at each start) |

Dates accept ISO 8601. Values without a timezone are treated as server local time.

## Changes from 1.x

| 1.x | 2.0 |
| --- | --- |
| `/count`, `/zones`, `/history`, `/heatmap`, `/alerts`, `/summary`, `/coordinates`, `/export/*`, `/ws` | Same names under `/api/...` |
| `/` returned JSON | `/` is the dashboard; use `/api/health` |
| `/auth/*`, `/admin/*` | `/api/auth/*`, `/api/admin/*` |
| `POST /alerts/threshold` (public, not persisted) | Removed. Use `PUT /api/admin/config/thresholds` |
| `POST /heatmap/reset` (public) | `POST /api/heatmap/reset` (signed in) |
| `/heatmap` returned PNG | `/api/heatmap` returns JPEG blended over the latest frame |
| `GET /alerts` also wrote alert history | Alerts are recorded by the detector; `GET /api/alerts` is read-only |
| History sampled every frame | One sample per second |
| Zones with nobody in them were omitted | Always present, with `current: 0` |
