"""FortiGate Local: monitor and control a FortiGate over its LAN REST API."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import FortiGateApi
from .const import CONF_API_TOKEN, CONF_VDOM, CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL
from .coordinator import FortiGateCoordinator

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.DEVICE_TRACKER,
    Platform.SENSOR,
    Platform.UPDATE,
]

type FortiGateConfigEntry = ConfigEntry[FortiGateCoordinator]


def build_api(hass: HomeAssistant, data: dict) -> FortiGateApi:
    """Create an API client from config entry data."""
    verify = data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
    return FortiGateApi(
        async_get_clientsession(hass, verify_ssl=verify),
        data[CONF_HOST],
        data[CONF_PORT],
        data[CONF_API_TOKEN],
        verify,
        data.get(CONF_VDOM),
    )


async def async_setup_entry(hass: HomeAssistant, entry: FortiGateConfigEntry) -> bool:
    """Set up from a config entry."""
    coordinator = FortiGateCoordinator(hass, entry, build_api(hass, dict(entry.data)))
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_on_options))
    return True


async def _async_reload_on_options(hass: HomeAssistant, entry: FortiGateConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: FortiGateConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
