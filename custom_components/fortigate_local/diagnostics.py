"""Diagnostics download: entry settings (token redacted) plus the last raw API payloads."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import FortiGateConfigEntry
from .const import CONF_API_TOKEN

TO_REDACT = {CONF_API_TOKEN, "serial"}

SECTIONS = ("resources", "performance", "interfaces", "ipsec", "policies", "firmware", "arp_macs")


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: FortiGateConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    data = coordinator.data
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "wan_interfaces": coordinator.wan_interfaces,
        "raw": async_redact_data(coordinator.raw, TO_REDACT),
        "sections_available": (
            {name: getattr(data, name) is not None for name in SECTIONS} if data else {}
        ),
    }
