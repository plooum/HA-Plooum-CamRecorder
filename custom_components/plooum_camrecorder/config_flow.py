"""Config flow: one entry; the folder, the retention and the cameras that get entities."""

from __future__ import annotations

import os
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector

from .const import (
    CONF_CAMERAS,
    CONF_FOLDER,
    CONF_KEEP_DAYS,
    CONF_MAX_SIZE_GB,
    DEFAULT_KEEP_DAYS,
    DEFAULT_MAX_SIZE_GB,
    DEFAULT_SUBFOLDER,
    DOMAIN,
    ENTRY_TITLE,
)


def default_folder(hass: HomeAssistant) -> str:
    media = hass.config.media_dirs.get("local") or next(iter(hass.config.media_dirs.values()), None)
    return os.path.join(media or hass.config.path("media"), DEFAULT_SUBFOLDER)


def _schema(hass: HomeAssistant, values: dict[str, Any]) -> vol.Schema:
    def number(hi: float, step: float, unit: str) -> selector.NumberSelector:
        return selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0, max=hi, step=step, unit_of_measurement=unit, mode=selector.NumberSelectorMode.BOX
            )
        )

    return vol.Schema(
        {
            vol.Required(CONF_FOLDER, default=values.get(CONF_FOLDER, default_folder(hass))): selector.TextSelector(),
            vol.Required(CONF_KEEP_DAYS, default=values.get(CONF_KEEP_DAYS, DEFAULT_KEEP_DAYS)): number(3650, 1, "d"),
            vol.Required(
                CONF_MAX_SIZE_GB, default=values.get(CONF_MAX_SIZE_GB, DEFAULT_MAX_SIZE_GB)
            ): number(100000, 0.5, "GB"),
            vol.Optional(CONF_CAMERAS, default=values.get(CONF_CAMERAS, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="camera", multiple=True)
            ),
        }
    )


def _errors(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, str]:
    folder = data[CONF_FOLDER]
    if not os.path.isabs(folder):
        return {CONF_FOLDER: "not_absolute"}
    if not hass.config.is_allowed_path(os.path.join(folder, "x")):
        return {CONF_FOLDER: "not_allowed"}
    return {}


def _clean(data: dict[str, Any]) -> dict[str, Any]:
    return {
        **data,
        CONF_FOLDER: os.path.normpath(data[CONF_FOLDER]),
        CONF_CAMERAS: list(dict.fromkeys(data.get(CONF_CAMERAS, []))),
    }


class CamRecorderConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _errors(self.hass, user_input)
            if not errors:
                return self.async_create_entry(title=ENTRY_TITLE, data={}, options=_clean(user_input))
        values = user_input or {CONF_CAMERAS: sorted(self.hass.states.async_entity_ids("camera"))}
        return self.async_show_form(step_id="user", data_schema=_schema(self.hass, values), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return CamRecorderOptionsFlow()


class CamRecorderOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _errors(self.hass, user_input)
            if not errors:
                return self.async_create_entry(data=_clean(user_input))
        values = user_input or {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(step_id="init", data_schema=_schema(self.hass, values), errors=errors)
