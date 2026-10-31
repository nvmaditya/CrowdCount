# Zones

A zone is a named polygon on the camera image. For each zone, CrowdCount reports:

- **Occupancy (`current`)**: tracked people whose box centre is inside the zone in the latest
  processed frame.
- **Unique tracks (`total_visitors`)**: distinct tracker IDs that have been inside the zone
  since the detector started.

Without zones, CrowdCount still counts everyone in the frame.

## Three ways to define zones

All three edit the same JSON file, and a running detector reloads it within about a second.
There is no restart.

1. **Admin panel, Zones page (recommended).** Click *New zone*, click to place corners on the
   live frame, and click the first corner (or double-click) to close the shape. Name it, pick a
   colour and save. Zones can be enabled, disabled or deleted from the list. This needs a
   running detector, because the editor draws on its latest frame.
2. **OpenCV editor.**
   ```bash
   crowdcount zones edit --source path/to/video.mp4     # or an rtsp:// stream URL
   ```
   | Input | Action |
   | --- | --- |
   | Left click | Add a corner |
   | Right click | Finish the zone (asks for a name in the terminal) |
   | Middle click / `u` | Undo the last corner |
   | `c` | Clear the current corners |
   | `s` | Save |
   | `d` / `t` | Delete / toggle a zone by index (prompted) |
   | `l` | List zones |
   | `q` / Esc | Quit (offers to save unsaved changes) |

   Video files show their first frame as a still; streams stay live.
3. **By hand.** Edit the JSON, then check it:
   ```bash
   crowdcount zones validate --source path/to/video.mp4
   ```

## File format

`zones.json` by default; set it with `--zones` or `[zones] file`. `crowdcount demo` uses
`<data dir>/demo-zones.json`. A sample for a 1703×990 camera is in
[`examples/zones.example.json`](../examples/zones.example.json).

```json
{
  "zones": [
    {
      "name": "Entrance",
      "points": [[120, 80], [480, 80], [480, 360], [120, 360]],
      "color": [46, 196, 182],
      "enabled": true
    }
  ]
}
```

| Field | Required | Rules |
| --- | --- | --- |
| `name` | yes | Non-empty and unique; it is the key for thresholds and the API. |
| `points` | yes | At least 3 `[x, y]` pairs in source-image pixels. Concave shapes are fine. |
| `color` | no | `[r, g, b]`, each 0-255. Defaults to a palette colour. |
| `enabled` | no | `true` by default. Disabled zones are kept but not counted. |

Errors name the zone and the problem (`zone 'Entrance': a polygon needs at least 3 points`);
JSON syntax errors give a line and column. At startup, a zone extending beyond the source's
frame logs a warning: it was probably drawn for a different camera or resolution.

> **Changed in 2.0:** `color` is read as RGB, as the 1.x docs always said. 1.x passed it to
> OpenCV unchanged, which reads BGR, so red and blue were swapped. Swap the first and last
> values of 1.x colours to keep their old appearance.

## Counting rules

- **Box centre, edges included.** A person belongs to a zone when the centre of their bounding
  box is inside the polygon or exactly on its boundary (the same rule as
  `cv2.pointPolygonTest(...) >= 0`, and tested against it).
- **Overlapping zones count the same person in each.** Zone occupancies can therefore add up to
  more than the total.
- **Only tracked people count.** Detections without a tracker ID (typically a person's first
  frame or two) are not counted.
- **Occupancy is rebuilt every frame.** Empty zones report 0; they are never dropped.
- **Unique tracks is an upper bound on unique people.** If the tracker loses someone (for
  example behind a pillar) and re-acquires them under a new ID, they count again. Looping a
  video also produces new IDs on each pass. See [tracking.md](tracking.md) for ways to reduce
  ID switches.
- **Reloading zones keeps history.** Unique-track counts survive a reload for zones whose name
  is unchanged.

## Thresholds and alerts

Each zone can have its own alert threshold, alongside the total-occupancy threshold. Both are
set on the admin panel's Thresholds page (`PUT /api/admin/config/thresholds`). An alert fires
when a count goes **above** its threshold. It is recorded once per breach: it fires again only
after the count has dropped back to the threshold or below. Breaches appear as a banner on the
dashboard, in Alert history (where admins can acknowledge them), and in the activity log.

Renaming a zone in the admin panel carries its threshold over; deleting a zone removes it.
