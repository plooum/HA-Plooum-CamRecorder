"""camera.live: a live HLS stream made by ffmpeg (dev/ha.sh serves it), with stream support.
camera.still: no stream, a generated picture with the time (photos only).
camera.cold: like camera.still, but every first picture after 5 s without one is missing, as an
idle Tapo camera answers (photos must try again)."""

from __future__ import annotations

import asyncio
import io
import time
import os

from PIL import Image, ImageDraw

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.util import dt as dt_util

HLS_URL = os.environ.get("CAMRECORDER_HLS", "http://127.0.0.1:8131/live.m3u8")


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    async_add_entities([LiveCamera(), StillCamera(), ColdCamera()])


class LiveCamera(Camera):
    _attr_name = "Live"
    _attr_unique_id = "camrecorder_dev_live"
    _attr_supported_features = CameraEntityFeature.STREAM

    async def async_camera_image(self, width=None, height=None) -> bytes | None:
        # Not from HA's stream (use_stream_for_stills): that needs libturbojpeg, missing here.
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-loglevel", "error", "-live_start_index", "-1", "-i", HLS_URL,
            "-frames:v", "1", "-f", "image2", "-c:v", "mjpeg", "-",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        return out or None

    async def stream_source(self) -> str | None:
        return HLS_URL


class StillCamera(Camera):
    _attr_name = "Still"
    _attr_unique_id = "camrecorder_dev_still"

    async def async_camera_image(self, width=None, height=None) -> bytes | None:
        def draw() -> bytes:
            img = Image.new("RGB", (640, 360), (40, 90, 140))
            ImageDraw.Draw(img).text((20, 20), dt_util.now().strftime("still %H:%M:%S"), fill=(255, 255, 255))
            out = io.BytesIO()
            img.save(out, "JPEG")
            return out.getvalue()

        return await self.hass.async_add_executor_job(draw)


class ColdCamera(StillCamera):
    _attr_name = "Cold"
    _attr_unique_id = "camrecorder_dev_cold"

    def __init__(self) -> None:
        super().__init__()
        self._last = 0.0

    async def async_camera_image(self, width=None, height=None) -> bytes | None:
        now = time.monotonic()
        cold = now - self._last > 5
        self._last = now
        return None if cold else await super().async_camera_image(width, height)
