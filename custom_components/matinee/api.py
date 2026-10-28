"""A small client for the Matinee server's API (/api/v1).

The server documents every route in internal/api/openapi.yaml.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import aiohttp

DEVICE_NAME = "Home Assistant"
PLATFORM = "homeassistant"


class MatineeError(Exception):
    """The server couldn't be reached or answered with an error."""


class MatineeAuthError(MatineeError):
    """The sign-in is no longer valid (signed out from Matinee, or removed)."""


class MatineeTooOld(MatineeError):
    """The server is from before Home Assistant could connect to it."""


class CodeDeclined(MatineeError):
    """Someone pressed Decline on the server's sign-in page."""


class CodeExpired(MatineeError):
    """The code wasn't approved in time."""


@dataclass
class DeviceCode:
    """A code to approve on the server's /link page."""

    device_code: str
    user_code: str
    verification_url: str
    verification_url_complete: str
    expires_in: int
    interval: int


class MatineeApi:
    """Talks to one Matinee server."""

    def __init__(self, session: aiohttp.ClientSession, url: str, token: str | None = None) -> None:
        self.session = session
        self.url = url.rstrip("/")
        self.token = token

    def _headers(self) -> dict[str, str]:
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        return {}

    async def _request(self, method: str, path: str, body: Any = None, timeout: float = 15) -> Any:
        try:
            async with self.session.request(
                method,
                f"{self.url}/api/v1{path}",
                json=body,
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as res:
                if res.status == 401:
                    raise MatineeAuthError("sign-in no longer valid")
                if res.status >= 400:
                    try:
                        msg = (await res.json()).get("error", res.reason)
                    except (aiohttp.ContentTypeError, json.JSONDecodeError):
                        msg = res.reason
                    raise MatineeError(f"{res.status}: {msg}")
                text = await res.text()
                return json.loads(text) if text.strip() else None
        except (TimeoutError, aiohttp.ClientError) as err:
            raise MatineeError(str(err)) from err

    # ---- signing in ----

    async def server(self) -> dict[str, Any]:
        """What the server is: product, version, name."""
        info = await self._request("GET", "/server")
        if info.get("product") != "matinee":
            raise MatineeError("not a Matinee server")
        return info

    async def check_supported(self) -> None:
        """Raise MatineeTooOld for a server without remote control. Servers
        that have it answer GET /clients with 401 to a stranger."""
        try:
            async with self.session.get(f"{self.url}/api/v1/clients", timeout=aiohttp.ClientTimeout(total=15)) as res:
                if res.status == 404:
                    raise MatineeTooOld("update Matinee to use it with Home Assistant")
        except (TimeoutError, aiohttp.ClientError) as err:
            raise MatineeError(str(err)) from err

    async def start_device_code(self, name: str = DEVICE_NAME) -> DeviceCode:
        """Ask for a code to approve on the server, as the apps do."""
        d = await self._request("POST", "/auth/device", {"deviceName": name, "platform": PLATFORM})
        return DeviceCode(
            device_code=d["deviceCode"],
            user_code=d["userCode"],
            verification_url=d["verificationUrl"],
            verification_url_complete=d["verificationUrlComplete"],
            expires_in=d["expiresIn"],
            interval=d["interval"],
        )

    async def poll_device_code(self, device_code: str) -> str | None:
        """The token once the code is approved; None while it's waiting."""
        try:
            async with self.session.post(
                f"{self.url}/api/v1/auth/device/token",
                json={"deviceCode": device_code},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as res:
                if res.status == 202:
                    return None
                if res.status == 403:
                    raise CodeDeclined
                if res.status == 410:
                    raise CodeExpired
                if res.status != 200:
                    raise MatineeError(f"{res.status}")
                return (await res.json())["token"]
        except (TimeoutError, aiohttp.ClientError) as err:
            raise MatineeError(str(err)) from err

    async def me(self) -> dict[str, Any]:
        return await self._request("GET", "/me")

    # ---- state ----

    async def clients(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/clients")

    async def libraries(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/libraries?counts=1")

    async def library_items(self, library_id: int) -> list[dict[str, Any]]:
        return await self._request("GET", f"/libraries/{library_id}/items", timeout=60)

    async def item(self, item_id: int) -> dict[str, Any]:
        return await self._request("GET", f"/items/{item_id}")

    async def children(self, item_id: int) -> list[dict[str, Any]]:
        return await self._request("GET", f"/items/{item_id}/children")

    async def recently_added(self, limit: int = 20) -> list[dict[str, Any]]:
        return await self._request("GET", f"/items?sort=added&limit={limit}")

    async def search(self, query: str) -> list[dict[str, Any]]:
        return await self._request("GET", f"/search?q={quote(query)}")

    def image_url(self, item_id: int, kind: str = "poster") -> str:
        return f"{self.url}/api/v1/items/{item_id}/image/{kind}"

    # ---- control ----

    async def send(self, client_id: str, command: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", f"/clients/{quote(client_id)}/commands", command)

    async def scan(self, library_id: int) -> None:
        await self._request("POST", f"/libraries/{library_id}/scan")

    async def events(self) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Follow the server's live feed: (type, event) pairs, starting with
        a snapshot of every client. Ends when the connection does."""
        try:
            async with self.session.get(
                f"{self.url}/api/v1/events",
                headers={**self._headers(), "Accept": "text/event-stream"},
                # No total limit: the feed stays open. The server writes at
                # least every 25 seconds, so a minute of silence means it's gone.
                timeout=aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=60),
            ) as res:
                if res.status == 401:
                    raise MatineeAuthError("sign-in no longer valid")
                if res.status != 200:
                    raise MatineeError(f"events: {res.status}")
                kind, data = "", []
                async for raw in res.content:
                    line = raw.decode("utf-8").rstrip("\r\n")
                    if line == "":
                        if data:
                            yield kind or "message", json.loads("\n".join(data))
                        kind, data = "", []
                    elif line.startswith(":"):
                        continue
                    elif line.startswith("event:"):
                        kind = line[6:].strip()
                    elif line.startswith("data:"):
                        data.append(line[5:].lstrip())
        except (TimeoutError, aiohttp.ClientError) as err:
            raise MatineeError(str(err)) from err
