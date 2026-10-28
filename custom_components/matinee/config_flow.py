"""Setting up Matinee: find the server, then approve Home Assistant on it
with a code, the way the apps sign in. No API keys or passwords."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import voluptuous as vol
from homeassistant.config_entries import SOURCE_REAUTH, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_TOKEN, CONF_URL
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .api import (
    CodeDeclined,
    CodeExpired,
    DeviceCode,
    MatineeApi,
    MatineeError,
    MatineeTooOld,
)
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 7171


def normalize_url(text: str) -> str:
    """Turn what someone typed into a server address: matinee.local becomes
    http://matinee.local:7171."""
    text = text.strip().rstrip("/")
    if "://" not in text:
        text = "http://" + text
        parts = urlsplit(text)
        if parts.port is None:
            text = f"http://{parts.hostname}:{DEFAULT_PORT}{parts.path}"
    return text


class MatineeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Find a Matinee server and sign Home Assistant in to it."""

    VERSION = 1

    def __init__(self) -> None:
        self._url = ""
        self._name = "Matinee"
        self._code: DeviceCode | None = None
        self._task: asyncio.Task[str] | None = None
        self._token = ""
        self._failure = ""

    def _api(self, token: str | None = None) -> MatineeApi:
        return MatineeApi(async_get_clientsession(self.hass), self._url, token)

    async def _identify(self) -> None:
        """Ask the server who it is, and make sure it isn't set up already."""
        info = await self._api().server()
        await self._api().check_supported()
        self._name = info.get("name") or "Matinee"
        if self.source != SOURCE_REAUTH:
            await self.async_set_unique_id(info.get("uuid") or self._url)
            self._abort_if_unique_id_configured(updates={CONF_URL: self._url})

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        """The server announced itself on the home network."""
        props = discovery_info.properties
        if props.get("product") != "matinee":
            return self.async_abort(reason="not_matinee")
        if props.get("id"):
            await self.async_set_unique_id(props["id"])
        if props.get("home") == "1" or not props.get("url"):
            host = discovery_info.host
            if ":" in host:
                host = f"[{host}]"
            self._url = f"http://{host}:{discovery_info.port}"
        else:
            self._url = props["url"].rstrip("/")
        if props.get("id"):
            self._abort_if_unique_id_configured(updates={CONF_URL: self._url})
        try:
            await self._identify()
        except MatineeTooOld:
            return self.async_abort(reason="too_old")
        except MatineeError:
            return self.async_abort(reason="cannot_connect")
        self.context["title_placeholders"] = {"name": self._name}
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return await self.async_step_link()
        self._set_confirm_only()
        return self.async_show_form(step_id="confirm", description_placeholders={"name": self._name, "url": self._url})

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Someone typed the server's address."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._url = normalize_url(user_input[CONF_URL])
            try:
                await self._identify()
            except MatineeTooOld:
                errors["base"] = "too_old"
            except MatineeError:
                errors["base"] = "cannot_connect"
            else:
                return await self.async_step_link()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_URL, default=self._url or "http://"): str}),
            errors=errors,
            description_placeholders={
                "example_home": "http://192.168.1.20:7171",
                "example_remote": "https://matinee.example.com",
            },
        )

    async def async_step_link(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show a code to approve on the server, and wait for it."""
        if self._task is None:
            try:
                self._code = await self._api().start_device_code()
            except MatineeError:
                return self.async_abort(reason="cannot_connect")
            self._task = self.hass.async_create_task(self._wait_for_approval(self._code))
        if not self._task.done():
            return self.async_show_progress(
                step_id="link",
                progress_action="approve",
                description_placeholders={
                    "code": self._code.user_code,
                    "url": self._code.verification_url,
                    "link": self._code.verification_url_complete,
                    "name": self._name,
                },
                progress_task=self._task,
            )
        task, self._task = self._task, None
        try:
            self._token = task.result()
        except CodeDeclined:
            return self.async_show_progress_done(next_step_id="declined")
        except CodeExpired:
            return self.async_show_progress_done(next_step_id="expired")
        except MatineeError:
            self._failure = "cannot_connect"
            return self.async_show_progress_done(next_step_id="failed")
        return self.async_show_progress_done(next_step_id="finish")

    async def _wait_for_approval(self, code: DeviceCode) -> str:
        api = self._api()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + code.expires_in
        while loop.time() < deadline:
            await asyncio.sleep(code.interval)
            try:
                token = await api.poll_device_code(code.device_code)
            except (CodeDeclined, CodeExpired):
                raise
            except MatineeError as err:
                # A blip on the network; keep waiting.
                _LOGGER.debug("Waiting for approval: %s", err)
                continue
            if token:
                return token
        raise CodeExpired

    async def async_step_expired(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """The code ran out; offer a new one."""
        if user_input is not None:
            return await self.async_step_link()
        return self.async_show_form(step_id="expired")

    async def async_step_declined(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_abort(reason="declined")

    async def async_step_failed(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_abort(reason=self._failure or "cannot_connect")

    async def async_step_finish(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        try:
            me = await self._api(self._token).me()
        except MatineeError:
            return self.async_abort(reason="cannot_connect")
        if not me.get("isAdmin"):
            return self.async_abort(reason="not_admin")
        data = {CONF_URL: self._url, CONF_TOKEN: self._token}
        if self.source == SOURCE_REAUTH:
            return self.async_update_reload_and_abort(self._get_reauth_entry(), data=data)
        return self.async_create_entry(title=self._name, data=data)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Home Assistant was signed out on the server; sign in again."""
        self._url = entry_data[CONF_URL]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            try:
                await self._identify()
            except MatineeError:
                return self.async_abort(reason="cannot_connect")
            return await self.async_step_link()
        return self.async_show_form(step_id="reauth_confirm", description_placeholders={"url": self._url})
