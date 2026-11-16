"""Photos and videos of cameras, saved on the server.

`Hub` is the integration's state (one config entry): the settings, and one `CameraRecorder` per
camera that has been used (any camera, configured or not). A video goes through the recorder of
Home Assistant's `stream` integration (no re-encoding, lookback). It is stopped early by
removing that recorder from the stream, which ends it exactly like the end of its duration.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
import logging
import os
import time
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from . import files
from .const import (
    ATTR_CAMERA_ENTITY,
    DOMAIN,
    EVENT_SAVED,
    KIND_PHOTO,
    KIND_VIDEO,
    PURGE_INTERVAL,
    RETRY_DELAY,
    SNAPSHOT_TIMEOUT,
    STOP_TIMEOUT,
)

if TYPE_CHECKING:
    from homeassistant.components.stream import Stream

_LOGGER = logging.getLogger(__name__)

RECORDER_PROVIDER = "recorder"  # homeassistant.components.stream.RECORDER_PROVIDER
HLS_PROVIDER = "hls"


async def _async_wait_stream_stopped(stream: Stream) -> None:
    """Let a stop of the stream in progress (the end of the previous video) finish first. When
    its worker thread ends, the stream removes every output it then has: a video started in
    between would be dropped at once ("captured nothing"). Uses the stream's start / stop lock;
    if it is gone (a future Home Assistant), only the short wait remains."""
    if not stream.outputs():
        lock = getattr(stream, "_start_stop_lock", None)
        if isinstance(lock, asyncio.Lock):
            async with lock:
                pass
        await asyncio.sleep(0.2)  # the worker's last clean-up task


def _video_length(path: str) -> float | None:
    """Duration of a video in seconds, read with PyAV (there with the stream integration)."""
    try:
        import av  # noqa: PLC0415

        with av.open(path) as container:
            if container.duration:
                return container.duration / 1_000_000
            stream = container.streams.video[0]
            if stream.duration and stream.time_base:
                return float(stream.duration * stream.time_base)
    except Exception:  # noqa: BLE001 - only a better name is lost
        _LOGGER.debug("Can't read the length of %s", path, exc_info=True)
    return None


class Hub:
    """Settings, the cameras' recorders and the retention."""

    def __init__(self, hass: HomeAssistant, folder: str, keep_days: float, max_size_gb: float) -> None:
        self.hass = hass
        self.folder = folder
        self.keep_days = keep_days
        self.max_bytes = int(max_size_gb * 1024**3)
        self.recorders: dict[str, CameraRecorder] = {}
        self._unsub_purge: CALLBACK_TYPE | None = None
        self._purging = False

    def recorder(self, entity_id: str) -> CameraRecorder:
        if entity_id not in self.recorders:
            self.recorders[entity_id] = CameraRecorder(self, entity_id)
        return self.recorders[entity_id]

    def media_content_id(self, path: str) -> str | None:
        return files.media_content_id(path, self.hass.config.media_dirs)

    async def async_start(self, cameras: list[str]) -> None:
        """Learn the last photo / video of the configured cameras, start the purge."""
        slugs = {files.camera_slug(c): c for c in cameras}
        found = await self.hass.async_add_executor_job(files.scan, self.folder, list(slugs))
        for slug, camera in slugs.items():
            mine = [c for c in found if os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(c.path)))) == slug]
            rec = self.recorder(camera)
            if photo := files.latest(mine, "photo"):
                rec.last_photo = self._describe(camera, KIND_PHOTO, photo.path, photo.time, None)
            if video := files.latest(mine, "video"):
                m = files.VIDEO_RE.match(os.path.basename(video.path))
                end = float(m.group(2)) if m else video.time
                rec.last_video = self._describe(camera, KIND_VIDEO, video.path, video.time, end)
        self._unsub_purge = async_track_time_interval(
            self.hass, self._async_purge_tick, timedelta(seconds=PURGE_INTERVAL)
        )
        self.async_purge()

    async def async_stop(self) -> None:
        """Unload or shutdown: stop the purge and finish the videos being recorded."""
        if self._unsub_purge:
            self._unsub_purge()
            self._unsub_purge = None
        busy = [r for r in self.recorders.values() if r.recording]
        if busy:
            await asyncio.gather(*(r.async_stop_recording() for r in busy), return_exceptions=True)

    async def _async_purge_tick(self, _now: datetime) -> None:
        self.async_purge()

    @callback
    def async_purge(self) -> None:
        if self._purging:
            return
        self._purging = True
        keep = [r.partial for r in self.recorders.values() if r.partial]
        self.hass.async_create_background_task(self._async_purge(keep), f"{DOMAIN} purge")

    async def _async_purge(self, keep: list[str]) -> None:
        def run() -> int:
            captures = files.scan(self.folder)
            return files.remove(files.to_purge(captures, time.time(), self.keep_days, self.max_bytes, keep))

        try:
            count = await self.hass.async_add_executor_job(run)
            if count:
                _LOGGER.info("Retention removed %s file(s) from %s", count, self.folder)
        except OSError as err:
            _LOGGER.warning("Retention of %s failed: %s", self.folder, err)
        finally:
            self._purging = False

    def _describe(self, camera: str, kind: str, path: str, start: float, end: float | None) -> dict[str, Any]:
        return {
            ATTR_CAMERA_ENTITY: camera,
            "kind": kind,
            "path": path,
            "media_content_id": self.media_content_id(path),
            "start": dt_util.utc_from_timestamp(start).isoformat(),
            "end": dt_util.utc_from_timestamp(end).isoformat() if end is not None else None,
        }

    @callback
    def async_saved(self, info: dict[str, Any]) -> None:
        self.hass.bus.async_fire(EVENT_SAVED, info)
        self.async_purge()


class CameraRecorder:
    """Photos and videos of one camera."""

    def __init__(self, hub: Hub, entity_id: str) -> None:
        self.hub = hub
        self.hass = hub.hass
        self.entity_id = entity_id
        self.last_photo: dict[str, Any] | None = None
        self.last_video: dict[str, Any] | None = None
        self.started: datetime | None = None  # while recording
        self.partial: str | None = None  # file being recorded
        self._stream: Stream | None = None
        self._output: Any = None  # the stream's RecorderOutput of our video
        self._task: asyncio.Task[dict[str, Any]] | None = None
        self._listeners: list[Callable[[], None]] = []

    @property
    def recording(self) -> bool:
        return self._task is not None and not self._task.done()

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    def _camera(self) -> Any:
        from homeassistant.components.camera import get_camera_from_entity_id  # noqa: PLC0415

        try:
            return get_camera_from_entity_id(self.hass, self.entity_id)
        except HomeAssistantError as err:
            raise HomeAssistantError(f"{self.entity_id}: {err}") from err

    # Photos

    async def _async_grab(self, camera: Any) -> tuple[bytes, str]:
        """A picture of the camera and its content type, from its usual source first.

        A camera that was idle often has no picture for the first request (a Tapo answers the
        next one), and one source can fail while the other works: each source is tried, then
        once more after RETRY_DELAY.
        """
        from homeassistant.components.camera import CameraEntityFeature, async_get_stream_image  # noqa: PLC0415

        async def still() -> tuple[bytes | None, str]:
            return await camera.async_camera_image(), camera.content_type or ""

        async def stream() -> tuple[bytes | None, str]:
            # As camera.snapshot does: a cold stream has no keyframe yet, wait for the next one.
            return await async_get_stream_image(camera, wait_for_next_keyframe=True), "image/jpeg"

        sources = [still]
        if CameraEntityFeature.STREAM in camera.supported_features:
            sources = [stream, still] if camera.use_stream_for_stills else [still, stream]
        reason = ""
        for attempt in range(2):
            if attempt:
                await asyncio.sleep(RETRY_DELAY)
            for source in sources:
                try:
                    content, content_type = await source()
                except HomeAssistantError as err:
                    reason = str(err)
                    continue
                except Exception as err:  # noqa: BLE001 - a camera integration's own failure
                    _LOGGER.debug("Picture of %s failed (%s)", self.entity_id, source.__name__, exc_info=True)
                    reason = f"{type(err).__name__}: {err}"
                    continue
                if content:
                    return content, content_type
        raise HomeAssistantError(f"{self.entity_id}: no picture" + (f" ({reason})" if reason else ""))

    async def async_snapshot(self) -> dict[str, Any]:
        camera = self._camera()  # clear error when missing or off
        try:
            async with asyncio.timeout(SNAPSHOT_TIMEOUT):
                content, content_type = await self._async_grab(camera)
        except TimeoutError as err:
            raise HomeAssistantError(f"{self.entity_id}: no picture after {SNAPSHOT_TIMEOUT} s") from err
        ext = "png" if "png" in content_type else "jpg"
        now = dt_util.now()

        def write() -> str:
            path = files.photo_path(self.hub.folder, self.entity_id, now, ext, os.path.exists)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "xb") as out:
                out.write(content)
            return path

        try:
            path = await self.hass.async_add_executor_job(write)
        except OSError as err:
            raise HomeAssistantError(f"{self.entity_id}: can't write the photo: {err}") from err
        ts = now.timestamp()
        self.last_photo = self.hub._describe(self.entity_id, KIND_PHOTO, path, ts, None)
        _LOGGER.debug("Photo of %s: %s", self.entity_id, path)
        self._notify()
        self.hub.async_saved(self.last_photo)
        return self.last_photo

    # Videos

    async def async_start_recording(self, max_duration: int, lookback: int) -> None:
        """Start a video, that stops by itself after `max_duration` seconds at the latest."""
        if self.recording:
            raise ServiceValidationError(f"{self.entity_id} is already recording")
        camera = self._camera()
        stream = await camera.async_create_stream()
        if not stream:
            raise HomeAssistantError(f"{self.entity_id} has no stream: it can only take photos")
        if RECORDER_PROVIDER in stream.outputs():
            raise HomeAssistantError(f"{self.entity_id} is already recording (camera.record?)")
        await _async_wait_stream_stopped(stream)
        # The lookback comes from the segments the HLS output keeps: only there when the stream
        # already runs (preloaded, or someone watching).
        if HLS_PROVIDER not in stream.outputs():
            lookback = 0
        start = dt_util.now() - timedelta(seconds=lookback)
        partial = files.partial_video_path(self.hub.folder, self.entity_id, start)
        await self.hass.async_add_executor_job(lambda: os.makedirs(os.path.dirname(partial), exist_ok=True))
        if not self.hass.config.is_allowed_path(partial):
            raise HomeAssistantError(
                f"Can't write {partial}: the folder must be in allowlist_external_dirs or a media folder"
            )

        self._stream = stream
        self._output = None
        self.partial = partial
        self.started = start
        self._task = self.hass.async_create_background_task(
            self._async_record(stream, partial, start, max_duration, lookback),
            f"{DOMAIN} video of {self.entity_id}",
        )
        self._task.add_done_callback(self._log_failure)
        # stream.async_record() adds its recorder before its first await.
        for _ in range(50):
            if self._task.done() or (output := stream.outputs().get(RECORDER_PROVIDER)):
                break
            await asyncio.sleep(0.02)
        else:
            output = None
        self._output = None if self._task.done() else output
        self._notify()
        if self._task.done():
            await self._task  # raises its error

    async def _async_record(
        self, stream: Stream, partial: str, start: datetime, duration: int, lookback: int
    ) -> dict[str, Any]:
        try:
            await stream.async_record(partial, duration=duration, lookback=lookback)
            end = time.time()

            def finish() -> tuple[str, float] | None:
                if not os.path.exists(partial):
                    return None
                # The real length: the recorder writes whole segments (keyframe to keyframe) and
                # adds one of lookback whenever the stream already runs.
                length = _video_length(partial)
                begin = end - length if length else start.timestamp()
                final = files.final_video_path(partial, begin, end)
                os.replace(partial, final)
                return final, begin

            done = await self.hass.async_add_executor_job(finish)
            if not done:
                raise HomeAssistantError(f"{self.entity_id}: the video captured nothing")
            final, begin = done
            info = self.hub._describe(self.entity_id, KIND_VIDEO, final, begin, end)
            self.last_video = info
            _LOGGER.debug("Video of %s: %s", self.entity_id, final)
            self.hub.async_saved(info)
            return info
        finally:
            self._stream = None
            self._output = None
            self.partial = None
            self.started = None
            self.hass.loop.call_soon(self._notify)  # once the task is done: recording is False

    def _log_failure(self, task: asyncio.Task[dict[str, Any]]) -> None:
        if not task.cancelled() and (err := task.exception()):
            _LOGGER.error("Video of %s failed: %s", self.entity_id, err)

    async def async_stop_recording(self) -> dict[str, Any]:
        """Stop the video now; returns it once written."""
        task = self._task
        if not self.recording or task is None:
            raise ServiceValidationError(f"{self.entity_id} is not recording")
        output, stream = self._output, self._stream
        if output is None and stream is not None:
            output = stream.outputs().get(RECORDER_PROVIDER)
        if output is None:
            raise HomeAssistantError(
                f"{self.entity_id}: can't stop the video (Home Assistant's stream recorder changed?)"
            )
        # Same as the end of its duration: the recorder leaves the stream, writes what it has
        # and closes the file.
        if stream is not None and RECORDER_PROVIDER in stream.outputs():
            await stream.remove_provider(output)
        else:  # the stream already dropped its outputs (shutdown)
            output.cleanup()
        try:
            async with asyncio.timeout(STOP_TIMEOUT):
                return await asyncio.shield(task)
        except TimeoutError as err:
            raise HomeAssistantError(f"{self.entity_id}: the video is still being written") from err

    async def async_record(self, duration: int, lookback: int) -> dict[str, Any]:
        """A video of `duration` seconds (plus the lookback); returns it once written."""
        await self.async_start_recording(duration, lookback)
        assert self._task is not None
        return await asyncio.shield(self._task)

    def state_attributes(self) -> dict[str, Any]:
        return {
            ATTR_CAMERA_ENTITY: self.entity_id,
            "started": self.started.isoformat() if self.started else None,
        }
