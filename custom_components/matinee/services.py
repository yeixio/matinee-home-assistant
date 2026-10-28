"""Actions beyond what every media player has: the sleep timer and subtitles."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.components.media_player import DOMAIN as MEDIA_PLAYER_DOMAIN
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import service

from .const import DOMAIN


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        "sleep_timer",
        entity_domain=MEDIA_PLAYER_DOMAIN,
        schema={
            vol.Exclusive("minutes", "when"): vol.All(vol.Coerce(int), vol.Range(min=0, max=1440)),
            vol.Exclusive("end_of_item", "when"): cv.boolean,
        },
        func="async_sleep_timer",
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        "set_subtitles",
        entity_domain=MEDIA_PLAYER_DOMAIN,
        schema={vol.Required("track"): cv.string},
        func="async_set_subtitles",
    )
