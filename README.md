# Plooum CamRecorder

*[Version française](README.fr.md)*

A Home Assistant integration that **takes photos and videos of any camera and saves them on the Home Assistant server**, from a dashboard as well as from any automation or script. Videos can have a fixed duration, or be started and stopped by hand. Files are sorted per camera and per day, show in the media browser, and are purged automatically.

It completes Home Assistant's own `camera.snapshot` and `camera.record`: videos that can be stopped, sorted file names, retention, a "recording" state, and actions that tell which file they wrote.

## Installation

HACS → Integrations → ⋮ → Custom repositories → `https://github.com/plooum/HA-Plooum-CamRecorder` (category *Integration*), install **Plooum CamRecorder**, restart Home Assistant. Then **Settings → Devices & services → Add integration → Plooum CamRecorder**.

## Actions

Every action works with **any camera entity** (target entities, devices or areas; several cameras at once).

| Action | What |
| :--- | :--- |
| `plooum_camrecorder.snapshot` | Saves a photo. |
| `plooum_camrecorder.record` | Saves a video of `duration` seconds (30 by default), plus `lookback` seconds before the call (0 by default, up to 30). Ends once the video is saved. |
| `plooum_camrecorder.start_recording` | Starts a video and returns at once. It stops with `stop_recording`, or by itself after `max_duration` seconds (600 by default). Takes `lookback` too. |
| `plooum_camrecorder.stop_recording` | Stops the video and ends once it is saved. |

`snapshot`, `record` and `stop_recording` respond with the saved files, which a script can use:

```yaml
- action: plooum_camrecorder.snapshot
  target:
    entity_id: camera.garden
  response_variable: shot
- action: notify.mobile_app_phone
  data:
    message: Someone at the gate
    data:
      image: "{{ shot.files[0].media_content_id }}"
```

Each file is described by `camera_entity`, `kind` (`photo` or `video`), `path`, `media_content_id`, `start` and `end` (videos). The event `plooum_camrecorder_saved` carries the same data every time a file is saved.

Videos need a camera with a stream (one that plays live in Home Assistant through the `stream` integration, RTSP for instance); other cameras can only take photos. A camera records one video at a time; several cameras can record together.

**Lookback** comes from the last seconds the camera's stream keeps in memory: it only works while that stream already runs, either because someone is watching the camera or because **Preload stream** is on in the camera's settings. Turn it on for cameras that record on motion.

The length of a video follows the camera's keyframes: expect it to be up to a few seconds longer than asked (one keyframe interval at each end).

## Entities

The cameras picked in the options get a device with:

| Entity | What |
| :--- | :--- |
| `button.<camera>_take_photo` | Takes a photo. |
| `switch.<camera>_recording` | On while the camera records a video, whoever started it. Turning it on starts a video (stopped after 10 minutes at the latest), turning it off stops it. Attribute `started`. |
| `sensor.<camera>_last_photo` / `sensor.<camera>_last_video` | When the last photo / video was taken, with its `path` and `media_content_id` (and `end` for videos). Found again on the disk after a restart. |

They all have a `camera_entity` attribute, so a card can find the recorder of its camera.

## Files

```
<folder>/<camera>/photos/<YYYY-MM-DD>/<HH-MM-SS>.jpg
<folder>/<camera>/videos/<YYYY-MM-DD>/<start>-<end>.mp4
```

`<camera>` is the camera's entity id without `camera.`, dates and times are in Home Assistant's time zone, and `<start>` / `<end>` of a video are Unix timestamps. The [gallery card of HA-Plooum-Cards](https://github.com/plooum/HA-Plooum-Cards/blob/main/docs/gallery.md) reads this layout as it is:

```yaml
type: custom:ha-plooum-gallery-card
media_source: media-source://media_source/local/plooum_camrecorder/garden/videos
```

While a video is recorded its file is `<start>-recording.mp4`; it gets its final name when it ends. When Home Assistant stops, the videos being recorded are stopped and saved first.

## Options

**Settings → Devices & services → Plooum CamRecorder → Configure**:

| Option | Default | What |
| :--- | :--- | :--- |
| Folder | `<media folder>/plooum_camrecorder` (`/media/plooum_camrecorder` on Home Assistant OS) | Where files are saved. It must be a media folder (to show in the media browser) or a folder listed in `allowlist_external_dirs`. |
| Keep for | 30 days | Older files are deleted. 0: kept forever. |
| Maximum size | 0 (no limit) | Above it, the oldest files are deleted. |
| Cameras with entities | all cameras when set up | The cameras that get the entities above. Actions work with every camera anyway. |

The retention runs every hour and after each saved file. It only deletes files named as above (and the day folders it empties), never anything else in the folder. Unfinished videos left by a crash are deleted after a day.

## Development

`dev/ha.sh start` runs a local Home Assistant (on 127.0.0.1:8130, using the virtual environment of HA-Plooum-Cards, or `HA_VENV`) with two test cameras: `camera.live` streams a live HLS video made by ffmpeg, `camera.still` has no stream. `dev/scenario.py` checks photos, videos, early stop, lookback, the switch and the events against it; `python -m unittest discover tests` checks the file layout and the retention. See [docs/CONCEPTION.md](docs/CONCEPTION.md) (French) for the design.
