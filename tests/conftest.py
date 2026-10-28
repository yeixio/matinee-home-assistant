"""A pretend Matinee server for the tests, speaking the parts of the API the
integration uses (see internal/api/openapi.yaml in yeixio/matinee)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from aiohttp import web

pytest_plugins = "pytest_homeassistant_custom_component"

TOKEN = "secret-token"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


class FakeMatinee:
    def __init__(self) -> None:
        self.url = ""
        self.uuid = "0f8fad5b-d9cb-469f-a165-70867728950e"
        self.name = "Living Room Matinee"
        self.admin = True
        self.approved = False
        self.declined = False
        self.token_valid = True
        self.commands: list[tuple[str, dict[str, Any]]] = []
        self.scanned: list[int] = []
        self.clients: list[dict[str, Any]] = [
            {
                "id": "tv-1234567",
                "name": "Living room TV",
                "platform": "tvos",
                "commands": ["play", "pause", "seek", "playItem", "stop"],
                "user": {"id": 1, "name": "Mike"},
                "online": True,
                "lastSeen": 0,
            }
        ]
        self.libraries = [
            {"id": 1, "name": "Movies", "kind": "movies", "paths": [], "scanning": False, "titles": 812, "files": 815},
            {"id": 2, "name": "TV", "kind": "tv", "paths": [], "scanning": False, "titles": 64, "files": 2210},
        ]
        self.items = {
            7: {"id": 7, "libraryId": 1, "kind": "movie", "title": "Big", "year": 1988, "playable": True, "hasPoster": True},
            8: {"id": 8, "libraryId": 1, "kind": "movie", "title": "Up", "year": 2009, "playable": True, "hasPoster": False},
        }
        self.feeds: list[asyncio.Queue] = []

    def push(self, kind: str, event: dict[str, Any]) -> None:
        for q in self.feeds:
            q.put_nowait((kind, {"type": kind, **event}))

    def drop_feeds(self) -> None:
        for q in self.feeds:
            q.put_nowait(None)

    def app(self) -> web.Application:
        app = web.Application()
        r = app.router
        r.add_get("/api/v1/server", self.server)
        r.add_post("/api/v1/auth/device", self.device)
        r.add_post("/api/v1/auth/device/token", self.device_token)
        r.add_get("/api/v1/me", self.me)
        r.add_get("/api/v1/clients", self.list_clients)
        self.old = False
        r.add_post("/api/v1/clients/{id}/commands", self.command)
        r.add_get("/api/v1/libraries", self.list_libraries)
        r.add_post("/api/v1/libraries/{id}/scan", self.scan)
        r.add_get("/api/v1/libraries/{id}/items", self.library_items)
        r.add_get("/api/v1/items", self.list_items)
        r.add_get("/api/v1/items/{id}", self.item)
        r.add_get("/api/v1/items/{id}/children", self.children)
        r.add_get("/api/v1/search", self.search)
        r.add_get("/api/v1/events", self.events)
        return app

    def _authed(self, request: web.Request) -> None:
        if request.headers.get("Authorization") != f"Bearer {TOKEN}" or not self.token_valid:
            raise web.HTTPUnauthorized(text=json.dumps({"error": "sign in required"}), content_type="application/json")

    async def server(self, request):
        return web.json_response({"product": "matinee", "version": "0.2.0", "apiVersion": 1, "name": self.name, "uuid": self.uuid})

    async def device(self, request):
        body = await request.json()
        assert body["deviceName"] == "Home Assistant"
        return web.json_response(
            {
                "deviceCode": "dc",
                "userCode": "ABCD-EFGH",
                "verificationUrl": f"{self.url}/link",
                "verificationUrlComplete": f"{self.url}/link?code=ABCDEFGH",
                "expiresIn": 600,
                "interval": 0,
            }
        )

    async def device_token(self, request):
        if self.declined:
            return web.json_response({"error": "sign-in was declined"}, status=403)
        if not self.approved:
            return web.json_response({"status": "pending"}, status=202)
        return web.json_response({"status": "approved", "token": TOKEN, "user": {}})

    async def me(self, request):
        self._authed(request)
        return web.json_response({"id": 1, "username": "mike", "isAdmin": self.admin})

    async def list_clients(self, request):
        if self.old:
            return web.json_response({"error": "no such endpoint"}, status=404)
        self._authed(request)
        return web.json_response(self.clients)

    async def command(self, request):
        self._authed(request)
        body = await request.json()
        self.commands.append((request.match_info["id"], body))
        return web.json_response({"id": "1", **body}, status=202)

    async def list_libraries(self, request):
        self._authed(request)
        return web.json_response(self.libraries)

    async def scan(self, request):
        self._authed(request)
        self.scanned.append(int(request.match_info["id"]))
        return web.Response(status=202)

    async def library_items(self, request):
        self._authed(request)
        lib = int(request.match_info["id"])
        return web.json_response([i for i in self.items.values() if i["libraryId"] == lib])

    async def list_items(self, request):
        self._authed(request)
        return web.json_response(list(self.items.values())[: int(request.query.get("limit", 50))])

    async def item(self, request):
        self._authed(request)
        return web.json_response(self.items[int(request.match_info["id"])])

    async def children(self, request):
        self._authed(request)
        return web.json_response([])

    async def search(self, request):
        self._authed(request)
        q = request.query["q"].casefold()
        return web.json_response([i for i in self.items.values() if q in i["title"].casefold()])

    async def events(self, request):
        self._authed(request)
        res = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await res.prepare(request)
        q: asyncio.Queue = asyncio.Queue()
        self.feeds.append(q)
        try:
            await res.write(f"event: snapshot\ndata: {json.dumps({'type': 'snapshot', 'clients': self.clients})}\n\n".encode())
            while True:
                got = await q.get()
                if got is None:
                    break
                kind, event = got
                await res.write(f"event: {kind}\ndata: {json.dumps(event)}\n\n".encode())
        finally:
            self.feeds.remove(q)
        return res


@pytest.fixture
async def matinee(socket_enabled):
    fake = FakeMatinee()
    runner = web.AppRunner(fake.app())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    fake.url = f"http://127.0.0.1:{port}"
    yield fake
    fake.drop_feeds()
    await runner.cleanup()
