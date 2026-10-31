# Dashboard

![The Live page during an alert: a banner, four stat cards, the camera, zones against their limits, the people-over-time chart, the heatmap and recent events](images/dashboard.jpg)

`crowdcount run` (or `demo`) serves the UI at **http://localhost:8080/** and interactive API docs
at `/docs`. There are five pages, all behind one sidebar (a top bar on phones):

| Page | Path | Who can open it |
| --- | --- | --- |
| [Live](#live) | `/` | Anyone |
| [Alerts](#alerts) | `/alerts.html` | Anyone (only admins can change anything) |
| [Zones](#zones) | `/zones.html` | Anyone (only admins can change anything) |
| [Settings](#settings) | `/settings.html` | Signed-in users; regular users see only *Your account* |
| Sign in | `/login.html` | Anyone |

Old `/static/...` URLs from 1.x redirect, and the 1.x admin page's address now opens Settings
(see [api.md](api.md#pages)). The pages need no internet access. Fonts, icons and Chart.js ship
with the package ([`web/vendor/README.md`](../src/crowdcount/web/vendor/README.md)).

The light/dark toggle in the header follows your OS setting until you choose; the choice is
remembered per browser. If the server stops answering, every page shows an offline bar and
recovers by itself when it comes back.

## Live

Refreshes once a second and needs no sign-in.

- **Status pill**: detector state (live, starting, finished, error, offline), source and
  throughput. Hover for model, tracker and device.
- **Banner**: appears while a scope is over its limit and its rule has *Show a banner on the
  dashboard* on. It names the scope, the count and the limit, and mentions scopes that are close
  to theirs. Admins get an *Acknowledge* button; everyone gets *Review alerts*. If the detector
  stops with an error, the banner says so.
- **Stat cards**: people now, peak this run (with time), total occupancy against its limit, and
  active alerts.
- **Camera**: the annotated video as MJPEG (`/api/stream`, up to 15 fps), with a full-screen
  button. It disconnects while the tab is hidden, and shows an explanation instead of a black
  box when the detector isn't running.
- **Zones**: occupancy per zone in the overlay colours, against its limit (or its share of the
  total when it has none), with the unique-track count. Zones at the greatest risk come first.
  Admins get an *Edit zones* link.
- **People over time**: total plus one line per zone, for the last 10 minutes, 30 minutes or hour
  (your choice is remembered). Each point is the peak within its window (10 seconds, 30
  seconds or a minute).
- **Heatmap**: where people have been since the run started or the last reset, blended over the
  latest frame. Signed-in users can reset it.
- **Recent events**: the five latest alert records, with a link to the Alerts page. Only alert
  records appear here, never the activity log.
- **Export CSV** and **PDF report** in the header: the last hour of per-second counts, or a
  one-page report (current and peak counts, a zone table with peaks and limits).

## Alerts

What is at risk, what fired, and the rules behind it.

- **Risk right now**: one card per scope that has a rule (Total occupancy and any zones): the count
  against its limit, and a marker where the early warning starts.
- **Active now**: alerts that are open this moment. They clear on their own when counts drop
  back.
- **Alert rules**: one per scope, either **Total occupancy** (everyone in the frame) or a zone.
  Each rule has:
  - a **limit**: the alert is *over* when the count is **above** it;
  - an **early warning** percentage (default **80%**): the alert is *near* from `warn_at` up;
  - an **on/off** switch;
  - **Show a banner on the dashboard**, on by default.

  Admins add, edit, switch off and remove rules here (Total occupancy can be edited and switched
  off, but not removed). Changes apply to the running detector at once and are saved. Everyone
  else sees the rules read-only. A zone's limit can also be set when drawing it on the
  [Zones](#zones) page.
- **History**: every recorded alert, filterable (all, needs review, acknowledged) and exportable
  as CSV, with an *Acknowledge* button for admins. History survives restarts. The name of the admin who
  acknowledged an alert is shown to admins only.

### Alert levels

| Level | When | Badge |
| --- | --- | --- |
| **Over limit** | count > limit | red |
| **Near limit** | count ≥ `warn_at` and ≤ limit | amber |
| **Normal** | anything lower, or the rule is off | none |

`warn_at = min(limit, max(1, ceil(limit × early warning % / 100)))`. With a limit of 10 and the
default 80%, `warn_at` is 8: 8, 9 and 10 people are *near*, and 11 is *over*. A limit of 1 gives
a `warn_at` of 1, so being at the limit is already *near*.

The server works the level out once; the browser only displays it.

**Rising is immediate, falling is not.** An alert appears as soon as a count reaches the next
level. It drops a level, or clears, only after the lower condition has held for **5 seconds**.
Detection counts jitter by one or two from frame to frame, and this stops a count hovering at a
limit from writing a new record every frame. Switching a rule off, or removing it, clears its
alert at once.

**Records and acknowledgement.** Each time an alert rises, one record is written to the history:
*near* when it reached the early warning, and a second *over* record if it then crossed the
limit. Dropping from *over* back to *near* writes nothing. Every active alert has the same id
as its history record, so acknowledging an alert (admins only, from the banner, Active now or
History) marks both. Acknowledging hides the banner for that alert; it does not clear the
alert, and does not silence the next one.

## Zones

Draw areas on the live frame; see [zones.md](zones.md) for the counting rules and file
format. Admins click *New zone*, click corners on the frame, close the shape, then name it,
pick a colour and optionally set an *Alert above* limit. Zones can be edited, switched off and
deleted from the list. Anyone else sees the zones read-only. Drawing needs a running detector,
because the editor draws on its latest frame, and the detector picks up changes within a second.

## Settings

Sign in to open it. Admins see all sections; everyone else sees *Your account*.

| Section | What it does | Who |
| --- | --- | --- |
| Video sources | Save named streams and files. A detector watches one source at a time; start a saved one with `crowdcount run --camera "Name"` (each row has the command to copy). The row of the source being watched shows *Running*. | Admin |
| Detection | Read-only: model, tracker, device, minimum confidence, minimum box size and frame stride of the running detector. Change them in `crowdcount.toml` or with flags; they apply on the next start. | Admin |
| Users | Add admins or users; delete accounts (not yourself, not the last admin). | Admin |
| Data | The CSV and PDF exports of recent counts, and how long the activity log and alert records are kept (1 to 3650 days; default 30). | Admin |
| Activity log | Filter by category, load more, export as CSV or PDF. | Admin |
| Your account | Your profile and password change. | Everyone signed in |

### Roles

| | Anonymous | User | Admin |
| --- | :---: | :---: | :---: |
| View Live, Alerts (rules and history) and Zones; stream, charts, exports | ✓ | ✓ | ✓ |
| Reset the heatmap, change your own password | | ✓ | ✓ |
| Change alert rules, zones, video sources and users; change retention | | | ✓ |
| Acknowledge alerts; read the activity log and who acknowledged what | | | ✓ |

The alert history is public, but leaves out who acknowledged each alert (`GET /api/alerts/history`).
The activity log is admin-only because it holds usernames and client IP addresses.

### What gets logged

| Category | Events |
| --- | --- |
| `AUTH` | Sign-in success and failure (with username and client IP), sign-out, password change |
| `CONFIG` | Zone, alert rule, camera, user and retention changes; heatmap resets; log cleanup |
| `ALERT` | `THRESHOLD_WARNING` when a count reaches an early warning; `THRESHOLD_EXCEEDED` when it passes a limit; alert acknowledged |

Entries older than the retention period (default 30 days; change it in Settings → Data or with
`PUT /api/admin/config/retention?retention_days=N`) are removed at each server start, along with
old alert records. The log is also capped at 20,000 entries.

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
