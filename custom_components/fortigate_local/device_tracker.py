"""Presence from the FortiGate ARP table for the devices picked in options."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components.device_tracker import ScannerEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import FortiGateConfigEntry
from .const import DEFAULT_CONSIDER_HOME, OPT_CONSIDER_HOME, OPT_DEVICE_NAMES
from .coordinator import FortiGateCoordinator
from .entity import FortiGateEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FortiGateConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    consider_home = timedelta(
        minutes=entry.options.get(OPT_CONSIDER_HOME, DEFAULT_CONSIDER_HOME)
    )
    names = entry.options.get(OPT_DEVICE_NAMES, {})
    async_add_entities(
        FortiGateTracker(coordinator, mac, consider_home, names.get(mac))
        for mac in coordinator.tracked_macs
    )


class FortiGateTracker(FortiGateEntity, ScannerEntity):
    """Home while the MAC was in the ARP table within the consider-home window.

    Phones drop off Wi-Fi when they sleep, so a single missed poll must not flip
    them to away; the window rides out those gaps.
    """

    _attr_icon = "mdi:cellphone-wireless"

    def __init__(
        self,
        coordinator: FortiGateCoordinator,
        mac: str,
        consider_home: timedelta,
        hostname: str | None,
    ) -> None:
        super().__init__(coordinator, f"tracker_{mac.replace(':', '')}")
        self._mac = mac
        self._consider_home = consider_home
        self._attr_name = f"{hostname or mac} Wi-Fi"

    @property
    def mac_address(self) -> str:
        # Also becomes the unique_id (HA's ScannerEntity uses the MAC for that).
        return self._mac

    @property
    def entity_registry_enabled_default(self) -> bool:
        # HA would create MAC trackers disabled unless another integration already
        # registered that MAC. These were picked explicitly, so enable them.
        return True

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.arp_macs is not None

    @property
    def is_connected(self) -> bool:
        seen = self.coordinator.last_seen.get(self._mac)
        return seen is not None and dt_util.utcnow() - seen <= self._consider_home
