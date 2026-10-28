"""Keeps Home Assistant in step with a Matinee server.

The server's live feed (GET /api/v1/events) says when a player appears,
changes or goes away, and when anything plays, pauses, stops or gets added,
so nothing here polls except the library counts, which refresh every few
minutes and after scans.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_time_interval

from .api import MatineeApi, MatineeAuthError, MatineeError
from .const import (
    EVENT,
    SIGNAL_CLIENT,
    SIGNAL_CONNECTION,
    SIGNAL_EVENT,
    SIGNAL_LIBRARIES,
    SIGNAL_NEW_CLIENT,
)

_LOGGER = logging.getLogger(__name__)

LIBRARY_REFRESH = timedelta(minutes=10)
# The longest wait between attempts to reconnect to the feed.
MAX_BACKOFF = 60


class MatineeHub:
    """One Matinee server's live state."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: MatineeApi, server: dict[str, Any]) -> None:
        self.hass = hass
        self.entry = entry
        self.api = api
        self.server = server
        self.clients: dict[str, dict[str, Any]] = {}
        self.libraries: list[dict[str, Any]] = []
        self.latest_added: dict[str, Any] | None = None
        self.connected = False
        self._unsubs: list[CALLBACK_TYPE] = []
        self._refresh_later: CALLBACK_TYPE | None = None

    @property
    def name(self) -> str:
        return self.server.get("name") or "Matinee"

    def _signal(self, template: str) -> str:
        return template.format(entry_id=self.entry.entry_id)

    async def async_start(self) -> None:
        """Load libraries and start following the feed."""
        await self.async_refresh_libraries()
        try:
            added = await self.api.recently_added(1)
            self.latest_added = added[0] if added else None
        except MatineeError:
            pass
        self._unsubs.append(
            async_track_time_interval(self.hass, lambda _now: self._schedule_refresh(0), LIBRARY_REFRESH)
        )
        self.entry.async_create_background_task(self.hass, self._follow(), "matinee events")

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._refresh_later:
            self._refresh_later()
            self._refresh_later = None

    async def async_refresh_libraries(self) -> None:
        try:
            self.libraries = await self.api.libraries()
        except MatineeAuthError:
            self.entry.async_start_reauth(self.hass)
            return
        except MatineeError as err:
            _LOGGER.debug("Couldn't refresh Matinee libraries: %s", err)
            return
        async_dispatcher_send(self.hass, self._signal(SIGNAL_LIBRARIES))

    @callback
    def _schedule_refresh(self, delay: float = 5) -> None:
        """Refresh the libraries soon; changes close together refresh once."""
        if self._refresh_later:
            return

        @callback
        def run(_now: Any) -> None:
            self._refresh_later = None
            self.hass.async_create_task(self.async_refresh_libraries())

        self._refresh_later = async_call_later(self.hass, delay, run)

    async def _follow(self) -> None:
        """Follow the server's feed for as long as the integration runs,
        reconnecting after a dropped connection or a server restart."""
        backoff = 1
        while True:
            try:
                async for kind, event in self.api.events():
                    backoff = 1
                    self._set_connected(True)
                    self._handle(kind, event)
            except MatineeAuthError:
                self._set_connected(False)
                self.entry.async_start_reauth(self.hass)
                return
            except MatineeError as err:
                _LOGGER.debug("Matinee feed dropped: %s", err)
            self._set_connected(False)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)

    @callback
    def _set_connected(self, connected: bool) -> None:
        if self.connected == connected:
            return
        self.connected = connected
        async_dispatcher_send(self.hass, self._signal(SIGNAL_CONNECTION))

    @callback
    def _handle(self, kind: str, event: dict[str, Any]) -> None:
        if kind == "snapshot":
            fresh = {c["id"]: c for c in event.get("clients") or []}
            gone = set(self.clients) - set(fresh)
            for cid in gone:
                del self.clients[cid]
                async_dispatcher_send(self.hass, self._signal(SIGNAL_CLIENT), cid)
            for cid, client in fresh.items():
                self._update_client(client)
            return
        if kind == "client.updated" and event.get("client"):
            self._update_client(event["client"])
            return
        if kind == "client.removed":
            cid = event.get("clientId")
            if cid in self.clients:
                del self.clients[cid]
                async_dispatcher_send(self.hass, self._signal(SIGNAL_CLIENT), cid)
            return

        if kind == "item.added":
            self.latest_added = event.get("item")
            self._schedule_refresh(10)
        elif kind == "library.scanned":
            self._schedule_refresh(0)
        if event.get("client"):
            self._update_client(event["client"])

        data = _bus_data(kind, event)
        self.hass.bus.async_fire(EVENT, data)
        async_dispatcher_send(self.hass, self._signal(SIGNAL_EVENT), data)

    @callback
    def _update_client(self, client: dict[str, Any]) -> None:
        cid = client["id"]
        new = cid not in self.clients
        self.clients[cid] = client
        if new:
            async_dispatcher_send(self.hass, self._signal(SIGNAL_NEW_CLIENT), cid)
        else:
            async_dispatcher_send(self.hass, self._signal(SIGNAL_CLIENT), cid)


def _bus_data(kind: str, event: dict[str, Any]) -> dict[str, Any]:
    """The server's event, flattened for automations and templates."""
    data: dict[str, Any] = {"type": kind.replace(".", "_")}
    if item := event.get("item"):
        data["item_id"] = item.get("id")
        data["title"] = item.get("title")
        data["kind"] = item.get("kind")
        for key, name in (("showTitle", "show"), ("season", "season"), ("episode", "episode"), ("year", "year")):
            if item.get(key):
                data[name] = item[key]
    if user := event.get("user"):
        data["user"] = user.get("name")
    if client := event.get("client"):
        data["client_id"] = client.get("id")
        data["client_name"] = client.get("name")
    if library := event.get("library"):
        data["library_id"] = library.get("id")
        data["library"] = library.get("name")
    if "positionMs" in event:
        data["position"] = round(event["positionMs"] / 1000)
    if kind == "playback.stopped":
        data["finished"] = bool(event.get("finished"))
    return data
