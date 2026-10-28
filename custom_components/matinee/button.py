"""A button to look for new files in every library."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import MatineeConfigEntry
from .api import MatineeError
from .entity import MatineeServerEntity
from .hub import MatineeHub


async def async_setup_entry(
    hass: HomeAssistant, entry: MatineeConfigEntry, add: AddConfigEntryEntitiesCallback
) -> None:
    add([ScanButton(entry.runtime_data.hub)])


class ScanButton(MatineeServerEntity, ButtonEntity):
    _attr_translation_key = "scan"

    def __init__(self, hub: MatineeHub) -> None:
        super().__init__(hub, "scan")

    async def async_press(self) -> None:
        try:
            for lib in self.hub.libraries:
                await self.hub.api.scan(lib["id"])
        except MatineeError as err:
            raise HomeAssistantError(f"Couldn't start a scan: {err}") from err
