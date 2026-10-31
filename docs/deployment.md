# Deployment

CrowdCount runs as one process per camera. These are the supported ways to keep it running.

## Docker

> The image is built and smoke-tested by CI (build, run, wait for the detector, clean stop).
> It was not built on the maintainer's machine during the 2.0 refurbish, so report any issue.

```bash
docker build -t crowdcount .

# Demo (sample video), dashboard on http://localhost:8080
docker run --rm -p 8080:8080 crowdcount

# A real camera, with persistent state
docker run -d --name crowdcount -p 8080:8080 \
  -v crowdcount-data:/data -v crowdcount-cache:/cache \
  -e CROWDCOUNT_ADMIN_PASSWORD='choose-a-strong-password' \
  crowdcount run --host 0.0.0.0 --source 'rtsp://user:pass@192.168.1.20/stream1'
```

- The image is CPU-only (PyTorch from the CPU wheel index) and runs as a non-root user.
- `/data` holds users, settings, logs and `zones.json`; `/cache` holds the model weights and
  demo video. Mount both to keep them across container restarts.
- The image has a `HEALTHCHECK` on `/api/health`. `docker stop` shuts down cleanly (exit 0).
- Linux webcams: add `--device /dev/video0` and use `--source 0`.
- Inside the container the server must listen on `0.0.0.0` (the image's default command does).

### Compose

```bash
cp .env.example .env        # set CROWDCOUNT_ADMIN_PASSWORD, optionally CROWDCOUNT_JWT_SECRET
docker compose up --build   # http://localhost:8080
```

Edit `command:` in [`compose.yaml`](../compose.yaml) to point at your camera.

## Bare metal (systemd)

```bash
python3 -m venv /opt/crowdcount/venv
/opt/crowdcount/venv/bin/pip install /path/to/CrowdCount
```

`/etc/systemd/system/crowdcount.service`:

```ini
[Unit]
Description=CrowdCount
After=network-online.target

[Service]
User=crowdcount
WorkingDirectory=/var/lib/crowdcount
Environment=CROWDCOUNT_DATA_DIR=/var/lib/crowdcount/data
EnvironmentFile=-/etc/crowdcount.env
ExecStart=/opt/crowdcount/venv/bin/crowdcount run --source rtsp://camera/stream1
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

CrowdCount handles SIGTERM, so `systemctl stop` shuts it down cleanly.

## Multiple cameras

One process watches one source. Run one instance per camera, each with its own port and data
directory:

```bash
crowdcount run --camera "Entrance" --port 8081 --data-dir data/entrance --zones zones/entrance.json
crowdcount run --camera "Hall"     --port 8082 --data-dir data/hall     --zones zones/hall.json
```

Saved cameras (`--camera`) are read from the given data directory. A single multi-camera
process is on the roadmap.

## Reverse proxy

Use one for HTTPS. Keep the proxy from buffering the MJPEG stream. nginx:

```nginx
server {
    listen 443 ssl;
    server_name crowd.example.com;
    # ssl_certificate ... ;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
    location /api/stream {
        proxy_pass http://127.0.0.1:8080;
        proxy_buffering off;
        proxy_read_timeout 1h;
    }
    location /api/ws {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

Set `CROWDCOUNT_TRUST_PROXY=1` so the activity log records real client IPs from
`X-Forwarded-For`, and keep CrowdCount bound to `127.0.0.1`.

## Before exposing it

- Set `CROWDCOUNT_ADMIN_PASSWORD`, or sign in and change the generated password.
- Remember the dashboard and video stream are **public** to anyone who can reach them
  ([dashboard.md](dashboard.md#security-model)). Restrict access at the proxy (for example
  basic auth or an IP allow-list) if the feed is sensitive.
- Back up the data directory. It is plain JSON.
