"""Matinee for Home Assistant: see what's playing on every Matinee player,
control it, and automate around it."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_TOKEN, CONF_URL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import MatineeApi, MatineeAuthError, MatineeError
from .entity import server_device
from .hub import MatineeHub
from .services import async_setup_services

PLATFORMS = [Platform.BUTTON, Platform.EVENT, Platform.MEDIA_PLAYER, Platform.SENSOR]


@dataclass
class MatineeData:
    hub: MatineeHub


type MatineeConfigEntry = ConfigEntry[MatineeData]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: MatineeConfigEntry) -> bool:
    api = MatineeApi(async_get_clientsession(hass), entry.data[CONF_URL], entry.data[CONF_TOKEN])
    try:
        server = await api.server()
        me = await api.me()
    except MatineeAuthError as err:
        raise ConfigEntryAuthFailed from err
    except MatineeError as err:
        raise ConfigEntryNotReady(f"Can't reach Matinee at {api.url}: {err}") from err
    if not me.get("isAdmin"):
        raise ConfigEntryAuthFailed("Home Assistant needs to be signed in with an admin account")

    hub = MatineeHub(hass, entry, api, server)
    # The server is a device of its own, which every player is reached through.
    dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, **server_device(hub))
    await hub.async_start()
    entry.runtime_data = MatineeData(hub)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MatineeConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.hub.async_stop()
    return ok
