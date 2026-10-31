# Troubleshooting

Add `-v` to any command for debug logs (or set `CROWDCOUNT_LOG_LEVEL=DEBUG`). Errors print one
line; `-v` adds the traceback.

## Starting up

**`could not open camera 0. Check that it is connected and not in use.`**
- Another app (video call, browser tab) may hold the camera. Close it.
- macOS: allow camera access for your terminal app in *System Settings → Privacy & Security →
  Camera*, then restart the terminal.
- Try another index: `--source 1`.
- Linux: check `ls /dev/video*` and that your user is in the `video` group. In Docker, pass
  `--device /dev/video0`.

**`video file not found: …` / `OpenCV could not decode …`**
Check the path. For formats OpenCV can't read, convert first:
`ffmpeg -i input.mkv -c:v libx264 output.mp4`.

**`could not connect to stream rtsp://…`**
Test the URL in VLC first. Many cameras need a path such as `/stream1` or `/h264`, and
credentials in the URL (`rtsp://user:pass@host:554/stream1`). Once running, a dropped stream is
retried 5 times with increasing delays before the detector stops.

**`could not load model 'yolov8m.pt': …`**
Stock weights download on first use from GitHub into `~/.cache/crowdcount/models` (or
`CROWDCOUNT_CACHE_DIR`). Offline? Copy the `.pt` file there or pass `--model /path/to/file.pt`.

**`model file not found: …`**
The path doesn't exist and isn't a stock name like `yolov8n.pt`.

**`cannot listen on 127.0.0.1:8080 (Address already in use)`**
Another CrowdCount (or something else) holds the port. Use `--port 8081`, or find it with
`lsof -i :8080`.

**`could not download the demo video`**
`crowdcount demo` fetches an 8 MB sample from GitHub once. Behind a proxy, set `HTTPS_PROXY`.
Or skip the demo: `crowdcount run --source path/to/any/video.mp4 --loop`.

**`… zones.json: invalid JSON at line 4, column 7` / `zone 'X': a polygon needs at least 3 points`**
Fix the file, or check it with `crowdcount zones validate`. A running detector ignores bad edits
and keeps the last good zones.

**`config file …: unknown setting 'detection.confidnce'`**
A typo in `crowdcount.toml`. The message lists the valid keys.

## Counting

**People are missed.**
Lower `--conf` (for example 0.35) and `--min-area` (for example 600) for small or distant
people; try a larger model (`yolov8s.pt`, `yolov8m.pt`). Check the preview (`--display`) or the
dashboard to see what is detected.

**Zone counts stay at 0.**
- `WARNING … Zone(s) … extend beyond the WxH frame`: the zones were drawn for another camera or
  resolution. Redraw them in the admin panel.
- The zone may be disabled.
- Counting uses the *centre* of each box. A zone drawn only on the floor can miss people whose
  centre is above it ([zones.md](zones.md#counting-rules)).

**Unique tracks keep growing for the same people.**
The tracker is losing IDs (occlusion, crossings) or the video is looping. See
[tracking.md](tracking.md#known-limitations). A larger `track_buffer` in a custom tracker YAML
can help.

**The total is lower than the sum of the zones.**
That's expected when zones overlap: a person inside two zones counts in both.

## Dashboard

**"No detector running" while the detector runs.**
`crowdcount detect` and `crowdcount serve` are separate processes and don't share data. Use
`crowdcount run`, which runs both in one process.

**The live view stays black or spins.**
The stream only starts once the detector reports "Live". Reverse proxies must not buffer
`/api/stream` ([deployment.md](deployment.md#reverse-proxy)).

**"Can't reach the CrowdCount server."**
The server stopped or the network dropped. The page retries every second.

**I can't sign in / forgot the password.**
Run `crowdcount admin reset-password` (same `--data-dir` as the server). The first password is
printed only once, at first start.

**Everyone was signed out after a restart.**
Expected if `data/.jwt_secret` was deleted or `CROWDCOUNT_JWT_SECRET` changed. Logged-out
tokens are also forgotten on restart.

**A JSON file under `data/` was renamed to `*.corrupt-<timestamp>.json`.**
It couldn't be parsed (for example after a manual edit), so CrowdCount set it aside and started
fresh. Fix and restore it while the server is stopped.

## Performance

- Check the status pill or `run_summary.json` for throughput.
- Use `--device mps` (Apple silicon) or `--device cuda:0` (NVIDIA). `auto` already prefers them
  when PyTorch supports them.
- Smaller model: `yolov8n.pt` is several times faster than `yolov8m.pt` on CPU
  ([tracking.md](tracking.md#choosing-a-model)).
- `--stride 2` halves the work.
- For files, `--realtime` (the default) deliberately caps processing at the video's frame rate.
  Use `--no-realtime` to benchmark.
