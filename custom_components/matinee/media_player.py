"""A media player for every Matinee player: the web player in a browser, the
phone and tablet apps, and the TV apps."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.media_player import (
    BrowseMedia,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import MatineeConfigEntry
from .api import MatineeError
from .browse import async_browse, media_id, parse_media_id
from .const import DOMAIN, SIGNAL_CLIENT, SIGNAL_CONNECTION, SIGNAL_NEW_CLIENT
from .hub import MatineeHub

# Which Home Assistant features each command the player takes turns on.
FEATURES = {
    "play": MediaPlayerEntityFeature.PLAY,
    "pause": MediaPlayerEntityFeature.PAUSE,
    "stop": MediaPlayerEntityFeature.STOP,
    "seek": MediaPlayerEntityFeature.SEEK,
    "next": MediaPlayerEntityFeature.NEXT_TRACK,
    "previous": MediaPlayerEntityFeature.PREVIOUS_TRACK,
    "playItem": MediaPlayerEntityFeature.PLAY_MEDIA | MediaPlayerEntityFeature.BROWSE_MEDIA,
    "volume": MediaPlayerEntityFeature.VOLUME_SET,
    "mute": MediaPlayerEntityFeature.VOLUME_MUTE,
}

PLATFORM_NAMES = {
    "web": "Web player",
    "ios": "iPhone or iPad",
    "android": "Android",
    "tvos": "Apple TV",
    "androidtv": "Android TV",
}

MEDIA_TYPES = {
    "movie": MediaType.MOVIE,
    "episode": MediaType.EPISODE,
    "track": MediaType.TRACK,
}


async def async_setup_entry(
    hass: HomeAssistant, entry: MatineeConfigEntry, add: AddConfigEntryEntitiesCallback
) -> None:
    hub = entry.runtime_data.hub

    @callback
    def new_client(client_id: str) -> None:
        add([MatineeClientPlayer(hub, client_id)])

    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_NEW_CLIENT.format(entry_id=entry.entry_id), new_client)
    )
    add([MatineeClientPlayer(hub, cid) for cid in hub.clients])


class MatineeClientPlayer(MediaPlayerEntity):
    """One Matinee player, as Home Assistant sees it."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_media_image_remotely_accessible = False

    def __init__(self, hub: MatineeHub, client_id: str) -> None:
        self.hub = hub
        self.client_id = client_id
        self._client = hub.clients.get(client_id, {})
        self._attr_unique_id = f"{hub.entry.unique_id or hub.entry.entry_id}_{client_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"client:{client_id}")},
            name=self._client.get("name") or "Matinee player",
            manufacturer="Matinee",
            model=PLATFORM_NAMES.get(self._client.get("platform", ""), self._client.get("app") or "Player"),
            sw_version=self._client.get("version") or None,
            via_device=(DOMAIN, hub.entry.entry_id),
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_CLIENT.format(entry_id=self.hub.entry.entry_id), self._changed
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_CONNECTION.format(entry_id=self.hub.entry.entry_id), self._connection
            )
        )

    @callback
    def _changed(self, client_id: str) -> None:
        if client_id != self.client_id:
            return
        self._client = self.hub.clients.get(client_id, {})
        self.async_write_ha_state()

    @callback
    def _connection(self) -> None:
        self.async_write_ha_state()

    # ---- state ----

    @property
    def _playing(self) -> dict[str, Any] | None:
        return self._client.get("playing")

    @property
    def _item(self) -> dict[str, Any]:
        return (self._playing or {}).get("item") or {}

    @property
    def available(self) -> bool:
        return self.hub.connected

    @property
    def state(self) -> MediaPlayerState:
        if not self._client or not self._client.get("online"):
            return MediaPlayerState.OFF
        playing = self._playing
        if not playing:
            return MediaPlayerState.IDLE
        if playing.get("buffering") and not playing.get("paused"):
            return MediaPlayerState.BUFFERING
        return MediaPlayerState.PAUSED if playing.get("paused") else MediaPlayerState.PLAYING

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        features = MediaPlayerEntityFeature(0)
        if self._client.get("online"):
            for command in self._client.get("commands") or []:
                features |= FEATURES.get(command, MediaPlayerEntityFeature(0))
        return features

    @property
    def media_content_id(self) -> str | None:
        return media_id(self._item["id"]) if self._item else None

    @property
    def media_content_type(self) -> str | None:
        return MEDIA_TYPES.get(self._item.get("kind", ""), MediaType.VIDEO) if self._item else None

    @property
    def media_title(self) -> str | None:
        return self._item.get("title")

    @property
    def media_series_title(self) -> str | None:
        return self._item.get("showTitle")

    @property
    def media_season(self) -> str | None:
        return str(self._item["season"]) if self._item.get("season") else None

    @property
    def media_episode(self) -> str | None:
        return str(self._item["episode"]) if self._item.get("episode") else None

    @property
    def media_duration(self) -> int | None:
        d = (self._playing or {}).get("durationMs")
        return round(d / 1000) if d else None

    @property
    def media_position(self) -> int | None:
        if not self._playing:
            return None
        return round(self._playing.get("positionMs", 0) / 1000)

    @property
    def media_position_updated_at(self) -> datetime | None:
        if not self._playing:
            return None
        return dt_util.utc_from_timestamp(self._playing.get("updatedAt", 0) / 1000)

    @property
    def volume_level(self) -> float | None:
        return (self._playing or {}).get("volume")

    @property
    def is_volume_muted(self) -> bool | None:
        return (self._playing or {}).get("muted") if self._playing else None

    @property
    def media_image_url(self) -> str | None:
        if not self._item:
            return None
        if self._item.get("kind") == "episode":
            return self.hub.api.image_url(self._item["id"], "backdrop")
        return self.hub.api.image_url(self._item["id"])

    @property
    def media_image_hash(self) -> str | None:
        return str(self._item["id"]) if self._item else None

    async def async_get_media_image(self) -> tuple[bytes | None, str | None]:
        """Posters need the sign-in, so Home Assistant fetches them itself."""
        url = self.media_image_url
        if not url:
            return None, None
        return await self._async_fetch_image(url)

    async def _async_fetch_image(self, url: str) -> tuple[bytes | None, str | None]:
        api = self.hub.api
        try:
            async with api.session.get(url, headers=api._headers()) as res:
                if res.status != 200:
                    return None, None
                return await res.read(), res.headers.get("Content-Type")
        except Exception:  # noqa: BLE001 - a missing poster is no error
            return None, None

    async def async_get_browse_image(
        self, media_content_type: str, media_content_id: str, media_image_id: str | None = None
    ) -> tuple[bytes | None, str | None]:
        item_id = parse_media_id(media_content_id)
        if item_id is None:
            return None, None
        return await self._async_fetch_image(self.hub.api.image_url(item_id))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {
            "client_id": self.client_id,
            "platform": self._client.get("platform"),
            "user": (self._client.get("user") or {}).get("name"),
            "away_from_home": bool(self._client.get("remote")),
        }
        if playing := self._playing:
            attrs["item_id"] = self._item.get("id")
            attrs["transcoding"] = bool(playing.get("transcoding"))
            if playing.get("subtitles"):
                attrs["subtitles"] = playing["subtitles"]
            if playing.get("sleepAtEnd"):
                attrs["sleep_timer"] = "end"
            elif playing.get("sleepAt"):
                attrs["sleep_timer"] = dt_util.utc_from_timestamp(playing["sleepAt"] / 1000).isoformat()
        return attrs

    # ---- control ----

    async def send(self, command: dict[str, Any]) -> None:
        try:
            await self.hub.api.send(self.client_id, command)
        except MatineeError as err:
            raise HomeAssistantError(f"{self._client.get('name', 'The player')} didn't take it: {err}") from err

    async def async_media_play(self) -> None:
        await self.send({"type": "play"})

    async def async_media_pause(self) -> None:
        await self.send({"type": "pause"})

    async def async_media_stop(self) -> None:
        await self.send({"type": "stop"})

    async def async_media_seek(self, position: float) -> None:
        await self.send({"type": "seek", "positionMs": int(position * 1000)})

    async def async_media_next_track(self) -> None:
        await self.send({"type": "next"})

    async def async_media_previous_track(self) -> None:
        await self.send({"type": "previous"})

    async def async_set_volume_level(self, volume: float) -> None:
        await self.send({"type": "volume", "volume": volume})

    async def async_mute_volume(self, mute: bool) -> None:
        await self.send({"type": "mute", "muted": mute})

    async def async_play_media(self, media_type: str, media_id: str, **kwargs: Any) -> None:
        """Play an item: matinee://item/123 from the media browser, an item's
        number, or a title to search for, such as "The Muppet Movie"."""
        item_id = parse_media_id(media_id)
        if item_id is None:
            item_id = await self._find(media_id, media_type)
        command: dict[str, Any] = {"type": "playItem", "itemId": item_id}
        extra = kwargs.get("extra") or {}
        if "position" in extra:
            command["positionMs"] = int(float(extra["position"]) * 1000)
        await self.send(command)

    async def _find(self, title: str, media_type: str) -> int:
        try:
            results = await self.hub.api.search(title)
        except MatineeError as err:
            raise HomeAssistantError(f"Couldn't search Matinee: {err}") from err
        kinds = {
            MediaType.MOVIE: {"movie"},
            MediaType.TVSHOW: {"show"},
            MediaType.EPISODE: {"episode"},
            MediaType.MUSIC: {"artist", "album", "track"},
        }.get(media_type)
        wanted = title.strip().casefold()
        candidates = [r for r in results if not kinds or r.get("kind") in kinds]
        for r in candidates:
            if r.get("title", "").casefold() == wanted:
                return r["id"]
        if candidates:
            return candidates[0]["id"]
        raise ServiceValidationError(f"Nothing called {title!r} on {self.hub.name}")

    async def async_browse_media(
        self, media_content_type: MediaType | str | None = None, media_content_id: str | None = None
    ) -> BrowseMedia:
        return await async_browse(
            self.hub,
            media_content_id,
            lambda item_id: self.get_browse_image_url(MediaType.VIDEO, media_id(item_id)),
        )

    async def async_sleep_timer(self, minutes: int | None = None, end_of_item: bool = False) -> None:
        """The matinee.sleep_timer action."""
        if end_of_item:
            await self.send({"type": "sleep", "endOfItem": True})
        else:
            await self.send({"type": "sleep", "minutes": int(minutes or 0)})

    async def async_set_subtitles(self, track: str) -> None:
        """The matinee.set_subtitles action."""
        await self.send({"type": "subtitles", "track": track})
