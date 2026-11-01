# Troubleshooting

Add `-v` to any command for debug logs (or set `CROWDCOUNT_LOG_LEVEL=DEBUG`). Errors print one
line; `-v` adds the traceback.

## Starting up

**`no video source: pass --source with a video file or stream URL …`**
There is no default source. Pass `--source path/to/video.mp4` or `--source rtsp://…`, set
`[video] source` in `crowdcount.toml`, use a saved source with `--camera NAME`, or try
`crowdcount demo`.

**`video file not found: …` / `OpenCV could not decode …`**
Check the path. For formats OpenCV can't read, convert first:
`ffmpeg -i input.mkv -c:v libx264 output.mp4`.

**`could not connect to stream rtsp://…`**
Test the URL in VLC first. Many cameras need a path such as `/stream1` or `/h264`, and
credentials in the URL (`rtsp://user:pass@host:554/stream1`). Once running, a dropped stream is
retried 5 times with increasing delays before the detector stops.

**`could not load model 'yolo26m.pt': …`**
Stock weights download on first use from GitHub into `~/.cache/crowdcount/models` (or
`CROWDCOUNT_CACHE_DIR`). Offline? Copy the `.pt` file there or pass `--model /path/to/file.pt`.

**`model file not found: …`**
The path doesn't exist and isn't a stock name like `yolo26n.pt`.

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
people; try a larger model (`yolo26m.pt`, `yolo26l.pt`). Check the preview (`--display`) or the
dashboard to see what is detected.

**Zone counts stay at 0.**
- `WARNING … Zone(s) … extend beyond the WxH frame`: the zones were drawn for another camera or
  resolution. Redraw them on the Zones page.
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
The server stopped or the network dropped. The page retries every second and clears the bar
when the server answers again.

**There is no Settings link, or I can't change anything.**
Settings appears in the sidebar only when you are signed in; the sidebar has a *Sign in* button
otherwise. Regular users see only *Your account* there. Alert rules, zones, video sources and
users need an admin ([roles](dashboard.md#roles)). A bookmark of the 1.x admin page redirects to
Settings.

**An alert stays on after the count dropped.**
By design. An alert drops a level or clears only after the lower condition has held for 5
seconds, so a count hovering at a limit doesn't flap. Switching the rule off clears it at once.

**No alert fired although the count was high.**
Check the Alerts page: a zone only has a rule once it has a limit, the rule's switch must be on,
and a count must be *above* the limit to be over it (from the early-warning count up to the limit
it is only *near*). The banner shows only over-limit alerts, and only for rules that have *Show a
banner on the dashboard* on.

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
- Smaller model: `yolo26n.pt` is about 2.5× as fast as the default `yolo26m.pt` on the M1 Pro
  ([tracking.md](tracking.md#choosing-a-model)).
- `--stride 2` halves the work.
- For files, `--realtime` (the default) deliberately caps processing at the video's frame rate.
  Use `--no-realtime` to benchmark.
