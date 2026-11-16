"""Plooum CamRecorder: photos and videos of any camera, saved on the Home Assistant server.

Actions (any camera entity): `snapshot`, `record` (fixed duration, lookback), `start_recording`
and `stop_recording`. The cameras picked in the options also get a "Take photo" button, a
"Recording" switch and "Last photo" / "Last video" sensors. Files land in a media folder, named
so that the gallery card of HA-Plooum-Cards reads the videos, and are purged by the retention.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HassJob, HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.service import async_extract_entity_ids
from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_DURATION,
    ATTR_LOOKBACK,
    ATTR_MAX_DURATION,
    CONF_CAMERAS,
    CONF_FOLDER,
    CONF_KEEP_DAYS,
    CONF_MAX_SIZE_GB,
    DEFAULT_DURATION,
    DEFAULT_KEEP_DAYS,
    DEFAULT_MAX_DURATION,
    DEFAULT_MAX_SIZE_GB,
    DOMAIN,
    MAX_DURATION,
    MAX_LOOKBACK,
    SERVICE_RECORD,
    SERVICE_SNAPSHOT,
    SERVICE_START_RECORDING,
    SERVICE_STOP_RECORDING,
)
from .recorder import CameraRecorder, Hub

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.BUTTON, Platform.SENSOR, Platform.SWITCH]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type CamRecorderConfigEntry = ConfigEntry[Hub]

_DURATION = vol.All(vol.Coerce(int), vol.Range(min=1, max=MAX_DURATION))
_LOOKBACK = vol.All(vol.Coerce(int), vol.Range(min=0, max=MAX_LOOKBACK))

SNAPSHOT_SCHEMA = cv.make_entity_service_schema({})
RECORD_SCHEMA = cv.make_entity_service_schema(
    {
        vol.Optional(ATTR_DURATION, default=DEFAULT_DURATION): _DURATION,
        vol.Optional(ATTR_LOOKBACK, default=0): _LOOKBACK,
    }
)
START_SCHEMA = cv.make_entity_service_schema(
    {
        vol.Optional(ATTR_MAX_DURATION, default=DEFAULT_MAX_DURATION): _DURATION,
        vol.Optional(ATTR_LOOKBACK, default=0): _LOOKBACK,
    }
)
STOP_SCHEMA = cv.make_entity_service_schema({})


def options(entry: ConfigEntry) -> dict[str, Any]:
    return {**entry.data, **entry.options}


def _hub(hass: HomeAssistant) -> Hub:
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            return entry.runtime_data
    raise ServiceValidationError(f"{DOMAIN} is not set up")


async def _cameras(hass: HomeAssistant, call: ServiceCall) -> list[str]:
    """Cameras targeted by a call (entities, devices, areas...)."""
    try:
        ids = await async_extract_entity_ids(call)
    except TypeError:  # Home Assistant before 2026: hass first
        ids = await async_extract_entity_ids(hass, call)  # type: ignore[arg-type]
    cameras = sorted(i for i in ids if i.startswith("camera."))
    if not cameras:
        raise ServiceValidationError("No camera targeted")
    return cameras


async def _for_each(
    hass: HomeAssistant, call: ServiceCall, run: Callable[[CameraRecorder], Awaitable[Any]]
) -> list[Any]:
    """Run on every targeted camera at once; raises the first error once all are done."""
    hub = _hub(hass)
    recorders = [hub.recorder(c) for c in await _cameras(hass, call)]
    results = await asyncio.gather(*(run(r) for r in recorders), return_exceptions=True)
    errors = [r for r in results if isinstance(r, BaseException)]
    for err in errors[1:]:
        _LOGGER.error("%s.%s: %s", DOMAIN, call.service, err)
    if errors:
        raise errors[0]
    return results


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async def snapshot(call: ServiceCall) -> ServiceResponse:
        return {"files": await _for_each(hass, call, lambda r: r.async_snapshot())}

    async def record(call: ServiceCall) -> ServiceResponse:
        d, lb = call.data[ATTR_DURATION], call.data[ATTR_LOOKBACK]
        return {"files": await _for_each(hass, call, lambda r: r.async_record(d, lb))}

    async def start_recording(call: ServiceCall) -> None:
        d, lb = call.data[ATTR_MAX_DURATION], call.data[ATTR_LOOKBACK]
        await _for_each(hass, call, lambda r: r.async_start_recording(d, lb))

    async def stop_recording(call: ServiceCall) -> ServiceResponse:
        return {"files": await _for_each(hass, call, lambda r: r.async_stop_recording())}

    hass.services.async_register(
        DOMAIN, SERVICE_SNAPSHOT, snapshot, schema=SNAPSHOT_SCHEMA, supports_response=SupportsResponse.OPTIONAL
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RECORD, record, schema=RECORD_SCHEMA, supports_response=SupportsResponse.OPTIONAL
    )
    hass.services.async_register(DOMAIN, SERVICE_START_RECORDING, start_recording, schema=START_SCHEMA)
    hass.services.async_register(
        DOMAIN,
        SERVICE_STOP_RECORDING,
        stop_recording,
        schema=STOP_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: CamRecorderConfigEntry) -> bool:
    opts = options(entry)
    hub = Hub(
        hass,
        opts[CONF_FOLDER],
        float(opts.get(CONF_KEEP_DAYS, DEFAULT_KEEP_DAYS)),
        float(opts.get(CONF_MAX_SIZE_GB, DEFAULT_MAX_SIZE_GB)),
    )
    entry.runtime_data = hub
    cameras = list(opts.get(CONF_CAMERAS, []))
    _remove_unconfigured_devices(hass, entry, cameras)
    await hub.async_start(cameras)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_updated))

    # Shutdown: finish the videos being recorded before Home Assistant cancels background tasks
    # and stops the streams (a shutdown job runs first).
    entry.async_on_unload(hass.async_add_shutdown_job(HassJob(hub.async_stop)))
    return True


def _remove_unconfigured_devices(hass: HomeAssistant, entry: ConfigEntry, cameras: list[str]) -> None:
    """Remove the devices, and so the entities, of the cameras taken out of the options."""
    registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if not any(domain == DOMAIN and ident in cameras for domain, ident in device.identifiers):
            registry.async_update_device(device.id, remove_config_entry_id=entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ConfigEntry, device: dr.DeviceEntry
) -> bool:
    """A device can be deleted from the UI once its camera is no longer in the options."""
    cameras = options(entry).get(CONF_CAMERAS, [])
    return not any(domain == DOMAIN and ident in cameras for domain, ident in device.identifiers)


async def _async_updated(hass: HomeAssistant, entry: CamRecorderConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: CamRecorderConfigEntry) -> bool:
    if ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_stop()
    return ok
