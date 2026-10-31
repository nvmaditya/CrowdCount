# Dashboard and admin panel

`crowdcount run` (or `demo`) serves the UI at **http://localhost:8080/**, with sign-in at
`/login.html`, the admin panel at `/admin.html`, and interactive API docs at `/docs`.
Old `/static/...` URLs from 1.x redirect.

## Dashboard

It refreshes once a second and needs no login to view.

- **Status pill**: detector state (live, starting, finished, error, offline), source and
  throughput. Hover for model, tracker and device.
- **KPIs**: people now, peak this run (with time), zones occupied, active alerts.
- **Live view**: the annotated video as MJPEG (`/api/stream`, up to 15 fps). It disconnects
  while the tab is hidden and shows an explanation instead of a black box when the detector
  isn't running.
- **Zones**: occupancy per zone in the overlay colours, with a bar against the zone's threshold
  (or its share of the total when it has none) and the unique-track count.
- **People over time**: the last 10 minutes at one point per second (the peak within each
  second), total plus one line per zone.
- **Occupancy by zone**: current counts as bars.
- **Heatmap**: where people have been since the run started or the last reset, blended over the
  latest frame. Signed-in users can reset it.
- **Alert threshold**: the total-occupancy threshold. Only admins can change it here.
- **Export**: the last hour of per-second counts as CSV, or a one-page PDF report (current and
  peak counts, a zone table with peaks and thresholds).

The light/dark toggle follows your OS setting until you choose; the choice is remembered per
browser.

## First sign-in

On first start with an empty data directory, CrowdCount creates an `admin` account:

- If `CROWDCOUNT_ADMIN_PASSWORD` is set (in your shell or `.env`), that is the password.
- Otherwise a random password is generated and printed **once** in the server log:

  ```
  ================================================================
    Created the first admin account at http://127.0.0.1:8080/login.html
      username: admin
      password: k-z1ptjuyUyzHXLg
    This password is shown only once. ...
  ================================================================
  ```

Missed it, or locked out? Reset it from the same machine:

```bash
crowdcount admin reset-password                  # prompts; add --username / --password
```

*Keep me signed in* stores the session in `localStorage`; otherwise it lasts until the tab is
closed. Tokens expire after 24 hours.

## Admin panel

| Page | What you can do |
| --- | --- |
| Overview | Counts of zones, cameras, users and unacknowledged alerts; detector status; recent activity. |
| Zones | Draw zones on the live frame; enable, disable or delete them. Changes reach the running detector within a second. See [zones.md](zones.md). |
| Thresholds | Set the total threshold and per-zone thresholds; clear a zone's threshold. Applied immediately and persisted. |
| Cameras | Save named sources (webcam index, file or stream URL). Start one with `crowdcount run --camera "Name"`; the page copies the command for you. One detector process watches one source. |
| Users | Create admins or users; delete accounts. You can't delete yourself or the last admin. |
| Activity log | Filter by category and date, page through, export CSV or PDF. |
| Alert history | Every threshold breach, with acknowledgement. |
| Account | Your profile; change your password. |

### Roles

| | Anonymous | User | Admin |
| --- | :---: | :---: | :---: |
| View dashboard, stream, charts, exports | ✓ | ✓ | ✓ |
| Reset heatmap | | ✓ | ✓ |
| Change thresholds, zones, cameras, users, retention | | | ✓ |
| Read activity log and alert history; acknowledge alerts | | | ✓ |

### What gets logged

| Category | Events |
| --- | --- |
| `AUTH` | Sign-in success and failure (with username and client IP), sign-out, password change |
| `CONFIG` | Zone, threshold, camera, user and retention changes; heatmap resets; log cleanup |
| `ALERT` | Threshold exceeded; alert acknowledged |

Entries older than the retention period (default 30 days; change it with
`PUT /api/admin/config/retention?retention_days=N`) are removed at each server start, along with
old alert records. The log is also capped at 20,000 entries.

## Security model

CrowdCount is built for a trusted local network. Read this before exposing it.

**What is protected**

- Passwords are bcrypt-hashed, and `users.json` and the JWT secret are owner-only (`0600`).
- Every change of configuration requires an admin token. Sign-out revokes the token.
- There is no default password, and the token secret is generated per installation.
- The UI loads no third-party scripts and is served with a strict Content-Security-Policy;
  user data is never inserted as HTML.
- Failed sign-ins are logged and take the same time whether or not the user exists.

**What is not**

- **The dashboard, live stream, snapshots and exports are public** to anyone who can reach the
  port. The default `--host 127.0.0.1` keeps them on your machine. With `--host 0.0.0.0`,
  anyone on your network can watch the camera feed. Optional login for these is on the roadmap.
- There is no HTTPS. Put a reverse proxy in front for TLS ([deployment.md](deployment.md)).
- There is no rate limiting on sign-in.
- Revoked tokens are remembered in memory, so a restart forgets them (they still expire after 24
  hours). Changing `CROWDCOUNT_JWT_SECRET`, or deleting `data/.jwt_secret`, signs everyone out.
- Stream URLs with credentials (`rtsp://user:pass@...`) are stored in plain text in
  `config.json` (saved cameras) or your config file. Credentials are stripped from the source
  label shown on the dashboard.

## Upgrading from 1.x

- Your `data/` directory works as-is: existing accounts and their passwords keep working.
- If the `admin` account still has the 1.x default password, a warning is logged at every
  start until you change it.
- The old token secret is gone, so everyone signs in again.
- The API moved under `/api` ([api.md](api.md)); `POST /alerts/threshold` was removed
  (it was public). Use `PUT /api/admin/config/thresholds`.
