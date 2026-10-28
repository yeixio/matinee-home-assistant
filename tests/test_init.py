"""Players, sensors, controls and events once set up."""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.components.media_player import (
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    ATTR_MEDIA_SEEK_POSITION,
    SERVICE_PLAY_MEDIA,
    MediaPlayerEntityFeature,
)
from homeassistant.components.media_player import (
    DOMAIN as MP,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_TOKEN,
    CONF_URL,
    SERVICE_MEDIA_PAUSE,
    SERVICE_MEDIA_SEEK,
    STATE_IDLE,
    STATE_OFF,
    STATE_PAUSED,
    STATE_PLAYING,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)

from custom_components.matinee.const import DOMAIN, EVENT

from .conftest import TOKEN

TV = "media_player.living_room_tv"


async def setup(hass: HomeAssistant, matinee) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=matinee.uuid, title=matinee.name, data={CONF_URL: matinee.url, CONF_TOKEN: TOKEN})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await settle(hass)
    return entry


async def settle(hass: HomeAssistant) -> None:
    for _ in range(5):
        await asyncio.sleep(0.02)
        await hass.async_block_till_done()


def playing(paused: bool = False, position: int = 60_000) -> dict:
    return {
        "id": "tv-1234567",
        "name": "Living room TV",
        "platform": "tvos",
        "commands": ["play", "pause", "seek", "playItem", "stop"],
        "user": {"id": 1, "name": "Mike"},
        "online": True,
        "lastSeen": 0,
        "playing": {
            "item": {"id": 7, "libraryId": 1, "kind": "movie", "title": "Big", "year": 1988},
            "positionMs": position,
            "durationMs": 6_240_000,
            "paused": paused,
            "updatedAt": 1_800_000_000_000,
        },
    }


async def test_players_and_sensors(hass: HomeAssistant, matinee) -> None:
    await setup(hass, matinee)
    state = hass.states.get(TV)
    assert state.state == STATE_IDLE
    features = state.attributes["supported_features"]
    assert features & MediaPlayerEntityFeature.PLAY_MEDIA
    assert not features & MediaPlayerEntityFeature.VOLUME_SET

    assert hass.states.get("sensor.living_room_matinee_movies").state == "812"
    assert hass.states.get("sensor.living_room_matinee_tv").state == "64"
    assert hass.states.get("sensor.living_room_matinee_watching_now").state == "0"

    events = async_capture_events(hass, EVENT)
    matinee.push("playback.started", {"item": playing()["playing"]["item"], "user": {"name": "Mike"}, "client": playing(), "positionMs": 60_000})
    await settle(hass)
    state = hass.states.get(TV)
    assert state.state == STATE_PLAYING
    assert state.attributes["media_title"] == "Big"
    assert state.attributes["media_duration"] == 6240
    assert state.attributes["media_position"] == 60
    assert hass.states.get("sensor.living_room_matinee_watching_now").state == "1"
    assert events[0].data == {
        "type": "playback_started",
        "item_id": 7,
        "title": "Big",
        "kind": "movie",
        "year": 1988,
        "user": "Mike",
        "client_id": "tv-1234567",
        "client_name": "Living room TV",
        "position": 60,
    }
    activity = hass.states.get("event.living_room_matinee_activity")
    assert activity.attributes["event_type"] == "playback_started"

    matinee.push("client.updated", {"client": playing(paused=True)})
    await settle(hass)
    assert hass.states.get(TV).state == STATE_PAUSED

    # A new player shows up by itself.
    matinee.push("client.updated", {"client": {**playing(), "id": "web-1234567", "name": "Firefox on macOS", "platform": "web", "playing": None}})
    await settle(hass)
    assert hass.states.get("media_player.firefox_on_macos").state == STATE_IDLE

    matinee.push("client.updated", {"client": {**playing(), "online": False, "playing": None}})
    await settle(hass)
    assert hass.states.get(TV).state == STATE_OFF

    matinee.push("item.added", {"item": {"id": 9, "kind": "episode", "title": "Half Loop", "showTitle": "Severance"}, "library": {"id": 2, "name": "TV"}})
    await settle(hass)
    assert hass.states.get("sensor.living_room_matinee_latest_addition").state == "Severance: Half Loop"


async def test_control(hass: HomeAssistant, matinee) -> None:
    await setup(hass, matinee)
    matinee.push("client.updated", {"client": playing()})
    await settle(hass)

    await hass.services.async_call(MP, SERVICE_MEDIA_PAUSE, {ATTR_ENTITY_ID: TV}, blocking=True)
    await hass.services.async_call(MP, SERVICE_MEDIA_SEEK, {ATTR_ENTITY_ID: TV, ATTR_MEDIA_SEEK_POSITION: 90}, blocking=True)
    await hass.services.async_call(
        MP, SERVICE_PLAY_MEDIA, {ATTR_ENTITY_ID: TV, ATTR_MEDIA_CONTENT_ID: "matinee://item/8", ATTR_MEDIA_CONTENT_TYPE: "movie"}, blocking=True
    )
    # A title works too.
    await hass.services.async_call(
        MP, SERVICE_PLAY_MEDIA, {ATTR_ENTITY_ID: TV, ATTR_MEDIA_CONTENT_ID: "big", ATTR_MEDIA_CONTENT_TYPE: "movie"}, blocking=True
    )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            MP, SERVICE_PLAY_MEDIA, {ATTR_ENTITY_ID: TV, ATTR_MEDIA_CONTENT_ID: "Nothing Like It", ATTR_MEDIA_CONTENT_TYPE: "movie"}, blocking=True
        )
    await hass.services.async_call(DOMAIN, "sleep_timer", {ATTR_ENTITY_ID: TV, "minutes": 30}, blocking=True)
    assert matinee.commands == [
        ("tv-1234567", {"type": "pause"}),
        ("tv-1234567", {"type": "seek", "positionMs": 90_000}),
        ("tv-1234567", {"type": "playItem", "itemId": 8}),
        ("tv-1234567", {"type": "playItem", "itemId": 7}),
        ("tv-1234567", {"type": "sleep", "minutes": 30}),
    ]

    await hass.services.async_call("button", "press", {ATTR_ENTITY_ID: "button.living_room_matinee_scan_libraries"}, blocking=True)
    assert matinee.scanned == [1, 2]


async def test_reconnects(hass: HomeAssistant, matinee) -> None:
    await setup(hass, matinee)
    matinee.drop_feeds()
    await settle(hass)
    assert hass.states.get(TV).state == STATE_UNAVAILABLE
    for _ in range(100):
        await asyncio.sleep(0.05)
        await hass.async_block_till_done()
        if hass.states.get(TV).state != STATE_UNAVAILABLE:
            break
    assert hass.states.get(TV).state == STATE_IDLE


async def test_signed_out(hass: HomeAssistant, matinee) -> None:
    matinee.token_valid = False
    entry = MockConfigEntry(domain=DOMAIN, unique_id=matinee.uuid, data={CONF_URL: matinee.url, CONF_TOKEN: TOKEN})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert any(f["context"]["source"] == "reauth" for f in hass.config_entries.flow.async_progress())
