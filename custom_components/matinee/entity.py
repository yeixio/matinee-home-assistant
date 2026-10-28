"""What the server's own entities share."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, SIGNAL_CONNECTION
from .hub import MatineeHub


def server_device(hub: MatineeHub) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, hub.entry.entry_id)},
        name=hub.name,
        manufacturer="Matinee",
        model="Media server",
        sw_version=hub.server.get("version"),
        configuration_url=hub.api.url,
        entry_type=DeviceEntryType.SERVICE,
    )


class MatineeServerEntity(Entity):
    """An entity of the server itself, such as a sensor."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hub: MatineeHub, key: str) -> None:
        self.hub = hub
        self._attr_unique_id = f"{hub.entry.unique_id or hub.entry.entry_id}_{key}"
        self._attr_device_info = server_device(hub)

    @property
    def available(self) -> bool:
        return self.hub.connected

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_CONNECTION.format(entry_id=self.hub.entry.entry_id), self._refresh
            )
        )

    @callback
    def _refresh(self, *_args) -> None:
        self.async_write_ha_state()
