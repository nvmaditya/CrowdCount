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

1. **The Zones page (recommended; admins only).** Click *New zone*, click to place corners on
   the live frame, and click the first corner (or double-click) to close the shape. Name it, pick
   a colour, optionally set an *Alert above* limit, and save. Zones can be edited, switched off or
   deleted from the list. This needs a running detector, because the editor draws on its latest
   frame. Anyone can open the page and see the zones, but only admins can change them.
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
| `name` | yes | Non-empty and unique; it is the key for alert rules and the API. |
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

## Alert rules

Each zone can have an alert rule, alongside the **Total occupancy** rule for the whole frame. A
rule has a limit, an early-warning percentage (default 80%), an on/off switch and a choice of
whether to show a banner on the dashboard. A zone has no rule until you give it a limit: set one
in the *Alert above* field of the zone form on the Zones page, or on the Alerts page
(`PUT /api/admin/rules/zones/{name}`, see [api.md](api.md)).

- **Over limit** when the count is **above** the limit.
- **Near limit** from `warn_at` up to the limit, where
  `warn_at = min(limit, max(1, ceil(limit × early warning % / 100)))`. With a limit of 10 and the
  default 80%, 8 and 9 people are near and 11 is over.
- An alert appears at once. It drops a level or clears only after the lower condition has held
  for 5 seconds, so a count hovering at a limit doesn't write a new record every frame.

Each alert that appears is recorded once, shown on the Live page (a banner for over-limit alerts,
if the rule shows one) and in the Alerts page's history, where admins can acknowledge it, and
the activity log. [dashboard.md](dashboard.md#alerts) has the details.

Renaming a zone in the Zones page carries its rule over; deleting a zone removes it. Rules are
stored by zone name in `config.json`.
