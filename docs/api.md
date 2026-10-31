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
| GET | `/api/summary` | Everything the dashboard shows: counts, peak, zones, active alerts, alert rules, thresholds, detector status, heatmap start time |
| GET | `/api/count` | `{"total_count", "timestamp", "detection_running"}` |
| GET | `/api/zones` | `{"zones": {name: {"current", "total_visitors", "threshold"}}, "timestamp"}` |
| GET | `/api/zones/layout` | Zone definitions (name, RGB colour, enabled, polygon) |
| GET | `/api/history?limit=300` | One sample per second (the peak in that second), oldest first; `limit` 1-3600 |
| GET | `/api/alerts` | Active alerts, near-limit and over-limit. `has_alerts` is true only while one is over its limit. Read-only, with no side effects. |
| GET | `/api/alerts/history?limit=100` | Recorded alerts, newest first; `limit` 1-1000. Like the admin version below, but without `acknowledged_by`. |
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
  "peak_count": 6,
  "peak_at": "2026-11-01T00:48:55+05:30",
  "zones": {"East plaza": {"current": 3, "total_visitors": 6, "threshold": 2}},
  "alerts": {
    "East plaza": {"id": "824576e4-5549-47f2-ba60-1bf7e7559a74", "type": "zone", "zone": "East plaza",
                   "level": "over", "threshold": 2, "current": 3, "exceeded": true,
                   "since": "2026-11-01T00:48:55+05:30", "acknowledged": false, "banner": true}
  },
  "rules": {
    "global": {"limit": 50, "warn_percent": 80, "warn_at": 40, "enabled": true, "banner": true},
    "zones": {"East plaza": {"limit": 2, "warn_percent": 80, "warn_at": 2, "enabled": true, "banner": true}}
  },
  "global_threshold": 50,
  "status": {"state": "running", "source": "vtest.avi", "model": "yolo26n.pt", "tracker": "bytetrack",
             "device": "mps", "fps": 9.5, "frame_width": 768, "frame_height": 576},
  "version": "2.0.0"
}
```

### Alerts and rules

- `alerts` holds every active alert, keyed by `"global"` or the zone name, at both levels. `level`
  is `"over"` (count above the limit) or `"near"` (count at or above `warn_at`, up to the limit);
  `exceeded` is true only for `over`. `since` is when the current level began.
- `rules` has the Total occupancy rule (`global`) and one rule per zone that has a limit.
  `warn_at = min(limit, max(1, ceil(limit * warn_percent / 100)))`; `warn_percent` defaults to 80.
  Clients should read `level` and `warn_at` rather than recompute them.
- An alert appears as soon as a count reaches a level. It drops a level or clears only after the
  lower condition has held for 5 seconds, and switching off or removing a rule clears it at once.
- An alert's `id` is also the `id` of its history record, so `POST /api/admin/alerts/{id}/acknowledge`
  marks both (`acknowledged` is true in each).
- The legacy `global_threshold` and `zone_thresholds` keys are still in the summary.

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
| PUT | `/api/admin/rules/global` | The Total occupancy rule: `{"limit": 50, "warn_percent": 80, "enabled": true, "banner": true}`. Only `limit` (1-100000) is required; the rest default as shown (`warn_percent` 1-100). |
| PUT / DELETE | `/api/admin/rules/zones/{name}` | PUT sets a zone's rule, with the same body (404 if the zone doesn't exist); DELETE removes it (404 if it has none). Rules take effect on the running detector at once and are saved in `config.json`. |
| GET / PUT | `/api/admin/config/thresholds` | The 1.x interface, still supported. PUT `{"global_threshold": 30, "zone_thresholds": {"Gate": 5, "Old": null}}`; `null` removes. It sets limits only: an existing rule keeps its other options, and a new zone rule gets the defaults. |
| GET / POST | `/api/admin/zones` | POST `{"name", "points": [[x, y], ...], "color": [r, g, b], "enabled": true}` |
| PUT / DELETE | `/api/admin/zones/{name}` | PUT any subset of the fields (renaming carries the threshold) |
| GET / POST | `/api/admin/cameras` | POST `{"name", "source_url", "enabled"}`. GET returns `{"cameras": [...]}`; each item has `running`, true while the detector is running that source. |
| PUT / DELETE | `/api/admin/cameras/{id}` | |
| GET | `/api/admin/logs` | `category`, `action`, `user_id`, `start_date`, `end_date`, `limit` (≤1000), `offset`; returns `total` for paging |
| GET | `/api/admin/logs/export/csv`, `/pdf` | `category`, `start_date`, `end_date` |
| POST | `/api/admin/logs/cleanup?retention_days=30` | Delete older entries now |
| GET | `/api/admin/alerts/history` | `alert_type` (`global` or a zone name), dates, `limit`. Records carry `level` (`near` or `over`), `acknowledged`, `acknowledged_by` and `acknowledged_at`. |
| POST | `/api/admin/alerts/{id}/acknowledge` | |
| GET | `/api/admin/config` | Thresholds, `rule_options`, cameras, retention, plus a `runtime` object: `zones_file`, `data_dir`, `model`, `tracker`, `device`, `confidence`, `min_box_area`, `stride` |
| GET / PUT | `/api/admin/config/retention` | PUT `?retention_days=N` (applied at each start) |

Dates accept ISO 8601. Values without a timezone are treated as server local time.

Rule URLs take the zone name as it is written, URL-encoded (`/api/admin/rules/zones/East%20plaza`);
names containing `/` work too.

The activity log uses `ALERT` entries `THRESHOLD_WARNING` (a count reached an early warning) and
`THRESHOLD_EXCEEDED` (it passed a limit). Setting or removing a rule logs `THRESHOLD_UPDATED`.

## Pages

The UI is static files served from `/`: `/` (Live), `/alerts.html`, `/zones.html`,
`/settings.html` and `/login.html`. `/admin.html`, the 1.x admin panel, redirects (301) to
`/settings.html`, and `/static/...` redirects to the same path without the prefix.

## Changes from 1.x

| 1.x | 2.0 |
| --- | --- |
| `/count`, `/zones`, `/history`, `/heatmap`, `/alerts`, `/summary`, `/coordinates`, `/export/*`, `/ws` | Same names under `/api/...` |
| `/` returned JSON | `/` is the dashboard; use `/api/health` |
| `/auth/*`, `/admin/*` | `/api/auth/*`, `/api/admin/*` |
| `POST /alerts/threshold` (public, not persisted) | Removed. Use `PUT /api/admin/rules/global` (or the legacy `PUT /api/admin/config/thresholds`) |
| `POST /heatmap/reset` (public) | `POST /api/heatmap/reset` (signed in) |
| `/heatmap` returned PNG | `/api/heatmap` returns JPEG blended over the latest frame |
| `GET /alerts` also wrote alert history | Alerts are recorded by the detector; `GET /api/alerts` is read-only |
| `alerts` held only exceeded thresholds | `alerts` also holds near-limit entries; each has `level` and an `id`. Use `has_alerts` or `exceeded` for the old meaning |
| `/admin.html` was the admin panel | Redirects to `/settings.html`; its sections are Settings, Zones and Alerts |
| History sampled every frame | One sample per second |
| Zones with nobody in them were omitted | Always present, with `current: 0` |
