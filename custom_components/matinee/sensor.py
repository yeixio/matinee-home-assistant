"""Sensors: who's watching, how big each library is, and what's newest."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import MatineeConfigEntry
from .const import SIGNAL_CLIENT, SIGNAL_EVENT, SIGNAL_LIBRARIES, SIGNAL_NEW_CLIENT
from .entity import MatineeServerEntity
from .hub import MatineeHub


async def async_setup_entry(
    hass: HomeAssistant, entry: MatineeConfigEntry, add: AddConfigEntryEntitiesCallback
) -> None:
    hub = entry.runtime_data.hub
    add([WatchingSensor(hub), LatestSensor(hub)])
    known: set[int] = set()

    @callback
    def libraries() -> None:
        new = [lib for lib in hub.libraries if lib["id"] not in known]
        known.update(lib["id"] for lib in new)
        if new:
            add([LibrarySensor(hub, lib["id"]) for lib in new])

    libraries()
    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_LIBRARIES.format(entry_id=entry.entry_id), libraries)
    )


class WatchingSensor(MatineeServerEntity, SensorEntity):
    """How many players are playing something right now."""

    _attr_translation_key = "watching"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, hub: MatineeHub) -> None:
        super().__init__(hub, "watching")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        for signal in (SIGNAL_CLIENT, SIGNAL_NEW_CLIENT):
            self.async_on_remove(
                async_dispatcher_connect(self.hass, signal.format(entry_id=self.hub.entry.entry_id), self._refresh)
            )

    def _playing(self) -> list[dict[str, Any]]:
        return [c for c in self.hub.clients.values() if c.get("online") and c.get("playing")]

    @property
    def native_value(self) -> int:
        return len(self._playing())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        out = []
        for c in self._playing():
            item = c["playing"].get("item") or {}
            entry = {
                "player": c.get("name"),
                "user": (c.get("user") or {}).get("name"),
                "title": item.get("title"),
                "paused": bool(c["playing"].get("paused")),
            }
            if item.get("showTitle"):
                entry["show"] = item["showTitle"]
            out.append(entry)
        return {"playing": out}


class LibrarySensor(MatineeServerEntity, SensorEntity):
    """How many titles a library has."""

    _attr_translation_key = "library"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, hub: MatineeHub, library_id: int) -> None:
        super().__init__(hub, f"library_{library_id}")
        self.library_id = library_id
        self._attr_translation_placeholders = {"name": self._library.get("name", "")}

    @property
    def _library(self) -> dict[str, Any]:
        return next((l for l in self.hub.libraries if l["id"] == self.library_id), {})

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_LIBRARIES.format(entry_id=self.hub.entry.entry_id), self._refresh
            )
        )

    @property
    def available(self) -> bool:
        return super().available and bool(self._library)

    @property
    def native_value(self) -> int | None:
        return self._library.get("titles")

    @property
    def native_unit_of_measurement(self) -> str:
        return {"movies": "movies", "tv": "shows", "music": "artists"}.get(self._library.get("kind", ""), "titles")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        lib = self._library
        return {"library_id": self.library_id, "kind": lib.get("kind"), "files": lib.get("files"), "scanning": bool(lib.get("scanning"))}


class LatestSensor(MatineeServerEntity, SensorEntity):
    """The newest addition to any library."""

    _attr_translation_key = "latest"

    def __init__(self, hub: MatineeHub) -> None:
        super().__init__(hub, "latest")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_EVENT.format(entry_id=self.hub.entry.entry_id), self._event)
        )

    @callback
    def _event(self, data: dict[str, Any]) -> None:
        if data.get("type") == "item_added":
            self.async_write_ha_state()

    @property
    def native_value(self) -> str | None:
        item = self.hub.latest_added
        if not item:
            return None
        if item.get("showTitle"):
            return f"{item['showTitle']}: {item.get('title', '')}"[:255]
        return (item.get("title") or "")[:255]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        item = self.hub.latest_added or {}
        return {k: item.get(v) for k, v in (("item_id", "id"), ("kind", "kind"), ("show", "showTitle"), ("year", "year"))}
