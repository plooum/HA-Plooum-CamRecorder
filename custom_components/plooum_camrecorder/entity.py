"""Base of the entities of a camera's recorder (cameras picked in the options)."""

from __future__ import annotations

from typing import Any

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from . import CamRecorderConfigEntry, options
from .const import ATTR_CAMERA_ENTITY, CONF_CAMERAS, DOMAIN
from .recorder import CameraRecorder


def configured(entry: CamRecorderConfigEntry) -> list[CameraRecorder]:
    return [entry.runtime_data.recorder(c) for c in options(entry).get(CONF_CAMERAS, [])]


class RecorderEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, recorder: CameraRecorder, key: str) -> None:
        self.recorder = recorder
        self._attr_unique_id = f"{recorder.entity_id}_{key}"
        st = recorder.hass.states.get(recorder.entity_id)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, recorder.entity_id)},
            name=st.name if st else recorder.entity_id,
            manufacturer="Plooum",
            model="Camera recorder",
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {ATTR_CAMERA_ENTITY: self.recorder.entity_id}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.recorder.async_add_listener(self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()
