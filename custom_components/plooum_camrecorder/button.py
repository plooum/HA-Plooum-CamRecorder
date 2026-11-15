"""Take photo."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CamRecorderConfigEntry
from .entity import RecorderEntity, configured
from .recorder import CameraRecorder


async def async_setup_entry(
    hass: HomeAssistant, entry: CamRecorderConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities(TakePhotoButton(r) for r in configured(entry))


class TakePhotoButton(RecorderEntity, ButtonEntity):
    _attr_translation_key = "take_photo"

    def __init__(self, recorder: CameraRecorder) -> None:
        super().__init__(recorder, "take_photo")

    async def async_press(self) -> None:
        await self.recorder.async_snapshot()
