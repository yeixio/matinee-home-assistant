"""Browsing Matinee's libraries from Home Assistant's media browser."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.media_player import (
    BrowseError,
    BrowseMedia,
    MediaClass,
    MediaType,
)

from .api import MatineeError
from .const import MEDIA_ID_PREFIX
from .hub import MatineeHub

ROOT = f"{MEDIA_ID_PREFIX}libraries"
RECENT = f"{MEDIA_ID_PREFIX}recent"

CLASSES = {
    "movie": (MediaClass.MOVIE, MediaType.MOVIE),
    "show": (MediaClass.TV_SHOW, MediaType.TVSHOW),
    "season": (MediaClass.SEASON, MediaType.SEASON),
    "episode": (MediaClass.EPISODE, MediaType.EPISODE),
    "artist": (MediaClass.ARTIST, MediaType.ARTIST),
    "album": (MediaClass.ALBUM, MediaType.ALBUM),
    "track": (MediaClass.TRACK, MediaType.TRACK),
}
LIBRARY_CLASSES = {"movies": MediaClass.MOVIE, "tv": MediaClass.TV_SHOW, "music": MediaClass.ARTIST}


def media_id(item_id: int) -> str:
    return f"{MEDIA_ID_PREFIX}item/{item_id}"


def parse_media_id(value: str) -> int | None:
    """The item a media id or a bare number names; None for anything else,
    such as a title to search for."""
    value = value.strip()
    if value.startswith(f"{MEDIA_ID_PREFIX}item/"):
        value = value.removeprefix(f"{MEDIA_ID_PREFIX}item/")
    return int(value) if value.isdigit() else None


def _title(item: dict[str, Any]) -> str:
    title = item.get("title", "")
    if item.get("kind") == "episode" and item.get("index") is not None:
        return f"{item['index']}. {title}"
    if item.get("kind") in ("movie", "show") and item.get("year"):
        return f"{title} ({item['year']})"
    return title


# Thumb turns an item into its poster's address, which goes through Home
# Assistant because the server wants the sign-in.
Thumb = Callable[[int], str]


def _item_node(item: dict[str, Any], thumb: Thumb) -> BrowseMedia:
    media_class, content_type = CLASSES.get(item.get("kind", ""), (MediaClass.VIDEO, MediaType.VIDEO))
    playable = bool(item.get("playable")) or item.get("kind") in ("show", "season", "album")
    return BrowseMedia(
        media_class=media_class,
        media_content_id=media_id(item["id"]),
        media_content_type=content_type,
        title=_title(item),
        can_play=playable,
        can_expand=not item.get("playable"),
        thumbnail=thumb(item["id"]) if item.get("hasPoster") else None,
    )


async def async_browse(hub: MatineeHub, content_id: str | None, thumb: Thumb) -> BrowseMedia:
    try:
        if not content_id or content_id == ROOT:
            children = [
                BrowseMedia(
                    media_class=MediaClass.DIRECTORY,
                    media_content_id=RECENT,
                    media_content_type=MediaType.VIDEO,
                    title="Recently added",
                    can_play=False,
                    can_expand=True,
                )
            ]
            for lib in hub.libraries:
                children.append(
                    BrowseMedia(
                        media_class=MediaClass.DIRECTORY,
                        media_content_id=f"{MEDIA_ID_PREFIX}library/{lib['id']}",
                        media_content_type=MediaType.VIDEO,
                        title=lib["name"],
                        can_play=False,
                        can_expand=True,
                        children_media_class=LIBRARY_CLASSES.get(lib.get("kind", "")),
                    )
                )
            return BrowseMedia(
                media_class=MediaClass.DIRECTORY,
                media_content_id=ROOT,
                media_content_type=MediaType.VIDEO,
                title=hub.name,
                can_play=False,
                can_expand=True,
                children=children,
            )
        if content_id == RECENT:
            items = await hub.api.recently_added(30)
            return BrowseMedia(
                media_class=MediaClass.DIRECTORY,
                media_content_id=RECENT,
                media_content_type=MediaType.VIDEO,
                title="Recently added",
                can_play=False,
                can_expand=True,
                children=[_item_node(i, thumb) for i in items],
            )
        if content_id.startswith(f"{MEDIA_ID_PREFIX}library/"):
            lib_id = int(content_id.rsplit("/", 1)[1])
            lib = next((l for l in hub.libraries if l["id"] == lib_id), {"name": "Library"})
            items = await hub.api.library_items(lib_id)
            return BrowseMedia(
                media_class=MediaClass.DIRECTORY,
                media_content_id=content_id,
                media_content_type=MediaType.VIDEO,
                title=lib["name"],
                can_play=False,
                can_expand=True,
                children=[_item_node(i, thumb) for i in items],
                children_media_class=LIBRARY_CLASSES.get(lib.get("kind", "")),
            )
        item_id = parse_media_id(content_id)
        if item_id is None:
            raise BrowseError(f"Unknown media {content_id}")
        item = await hub.api.item(item_id)
        node = _item_node(item, thumb)
        node.children = [_item_node(c, thumb) for c in await hub.api.children(item_id)]
        return node
    except MatineeError as err:
        raise BrowseError(f"Couldn't reach {hub.name}: {err}") from err
