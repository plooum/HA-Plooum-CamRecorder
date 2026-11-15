"""Recording: on while the camera records a video, whoever started it; on starts one, off stops it."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CamRecorderConfigEntry
from .const import DEFAULT_MAX_DURATION
from .entity import RecorderEntity, configured
from .recorder import CameraRecorder


async def async_setup_entry(
    hass: HomeAssistant, entry: CamRecorderConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities(RecordingSwitch(r) for r in configured(entry))


class RecordingSwitch(RecorderEntity, SwitchEntity):
    _attr_translation_key = "recording"

    def __init__(self, recorder: CameraRecorder) -> None:
        super().__init__(recorder, "recording")

    @property
    def is_on(self) -> bool:
        return self.recorder.recording

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {**self.recorder.state_attributes(), "max_duration": DEFAULT_MAX_DURATION}

    async def async_turn_on(self, **kwargs: Any) -> None:
        if not self.recorder.recording:
            await self.recorder.async_start_recording(DEFAULT_MAX_DURATION, 0)

    async def async_turn_off(self, **kwargs: Any) -> None:
        if self.recorder.recording:
            await self.recorder.async_stop_recording()
