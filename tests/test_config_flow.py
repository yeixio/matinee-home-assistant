"""Setting up: found on the network or typed in, then approved with a code."""

from __future__ import annotations

import asyncio
from ipaddress import ip_address

from homeassistant import config_entries
from homeassistant.const import CONF_TOKEN, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.matinee.config_flow import normalize_url
from custom_components.matinee.const import DOMAIN

from .conftest import TOKEN


def test_normalize_url():
    assert normalize_url("matinee.local") == "http://matinee.local:7171"
    assert normalize_url("192.168.1.20:8080/") == "http://192.168.1.20:8080"
    assert normalize_url("https://matinee.example.com/") == "https://matinee.example.com"


async def approve(hass: HomeAssistant, matinee, flow_id: str):
    matinee.approved = True
    for _ in range(50):
        await asyncio.sleep(0.02)
        await hass.async_block_till_done()
        result = await hass.config_entries.flow.async_configure(flow_id)
        if result["type"] != FlowResultType.SHOW_PROGRESS:
            return result
    raise AssertionError("never approved")


async def test_typed_address(hass: HomeAssistant, matinee) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] == FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_URL: "http://127.0.0.1:1"})
    assert result["errors"] == {"base": "cannot_connect"}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_URL: matinee.url + "/"})
    assert result["type"] == FlowResultType.SHOW_PROGRESS
    assert result["description_placeholders"]["code"] == "ABCD-EFGH"
    assert result["description_placeholders"]["url"] == f"{matinee.url}/link"

    result = await approve(hass, matinee, result["flow_id"])
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Living Room Matinee"
    assert result["data"] == {CONF_URL: matinee.url, CONF_TOKEN: TOKEN}
    assert result["result"].unique_id == matinee.uuid


async def test_found_on_network(hass: HomeAssistant, matinee) -> None:
    port = int(matinee.url.rsplit(":", 1)[1])
    info = ZeroconfServiceInfo(
        ip_address=ip_address("127.0.0.1"),
        ip_addresses=[ip_address("127.0.0.1")],
        hostname="matinee.local.",
        name="Living Room Matinee._matinee._tcp.local.",
        port=port,
        type="_matinee._tcp.local.",
        properties={"product": "matinee", "version": "0.2.0", "home": "1", "id": matinee.uuid},
    )
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=info)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "confirm"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] == FlowResultType.SHOW_PROGRESS
    result = await approve(hass, matinee, result["flow_id"])
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_URL] == matinee.url

    # Found again, already set up: nothing to do.
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=info)
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_needs_an_admin(hass: HomeAssistant, matinee) -> None:
    matinee.admin = False
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_URL: matinee.url})
    result = await approve(hass, matinee, result["flow_id"])
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "not_admin"


async def test_declined(hass: HomeAssistant, matinee) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_URL: matinee.url})
    matinee.declined = True
    result = await approve(hass, matinee, result["flow_id"])
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "declined"


async def test_sign_in_again(hass: HomeAssistant, matinee) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=matinee.uuid, data={CONF_URL: matinee.url, CONF_TOKEN: "old"})
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await approve(hass, matinee, result["flow_id"])
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_TOKEN] == TOKEN


async def test_server_too_old(hass: HomeAssistant, matinee) -> None:
    matinee.old = True
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_URL: matinee.url})
    assert result["errors"] == {"base": "too_old"}
