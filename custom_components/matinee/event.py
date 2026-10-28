"""An event entity for what happens on the server, for automations."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import MatineeConfigEntry
from .const import EVENT_TYPES, SIGNAL_EVENT
from .entity import MatineeServerEntity
from .hub import MatineeHub


async def async_setup_entry(
    hass: HomeAssistant, entry: MatineeConfigEntry, add: AddConfigEntryEntitiesCallback
) -> None:
    add([ActivityEvent(entry.runtime_data.hub)])


class ActivityEvent(MatineeServerEntity, EventEntity):
    _attr_translation_key = "activity"
    _attr_event_types = EVENT_TYPES

    def __init__(self, hub: MatineeHub) -> None:
        super().__init__(hub, "activity")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_EVENT.format(entry_id=self.hub.entry.entry_id), self._event)
        )

    @callback
    def _event(self, data: dict[str, Any]) -> None:
        if data.get("type") in EVENT_TYPES:
            self._trigger_event(data["type"], {k: v for k, v in data.items() if k != "type"})
            self.async_write_ha_state()
