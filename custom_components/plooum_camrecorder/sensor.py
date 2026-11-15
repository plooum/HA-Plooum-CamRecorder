"""Last photo / last video: when it was taken, with its file."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import CamRecorderConfigEntry
from .const import KIND_PHOTO, KIND_VIDEO
from .entity import RecorderEntity, configured
from .recorder import CameraRecorder


async def async_setup_entry(
    hass: HomeAssistant, entry: CamRecorderConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities(LastCaptureSensor(r, kind) for r in configured(entry) for kind in (KIND_PHOTO, KIND_VIDEO))


class LastCaptureSensor(RecorderEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, recorder: CameraRecorder, kind: str) -> None:
        super().__init__(recorder, f"last_{kind}")
        self.kind = kind
        self._attr_translation_key = f"last_{kind}"

    def _info(self) -> dict[str, Any] | None:
        return self.recorder.last_photo if self.kind == KIND_PHOTO else self.recorder.last_video

    @property
    def native_value(self) -> datetime | None:
        info = self._info()
        return dt_util.parse_datetime(info["start"]) if info else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        info = self._info() or {}
        attrs = {**super().extra_state_attributes, "path": info.get("path"), "media_content_id": info.get("media_content_id")}
        if self.kind == KIND_VIDEO:
            attrs["end"] = info.get("end")
        return attrs
