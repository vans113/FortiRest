"""Firmware update availability (read-only: upgrades stay a manual decision)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.update import UpdateEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import FortiGateConfigEntry
from .coordinator import FortiGateCoordinator, version_tuple
from .entity import FortiGateEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FortiGateConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([FirmwareUpdate(entry.runtime_data)])


def _fmt(fw: dict[str, Any]) -> str:
    return f"v{fw.get('major')}.{fw.get('minor')}.{fw.get('patch')} build{fw.get('build')}"


class FirmwareUpdate(FortiGateEntity, UpdateEntity):
    """Shows the newest firmware FortiGuard offers within the same major line."""

    _attr_name = "Firmware"
    _attr_title = "FortiOS"

    def __init__(self, coordinator: FortiGateCoordinator) -> None:
        super().__init__(coordinator, "firmware_update")

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.firmware is not None

    @property
    def _current(self) -> dict[str, Any] | None:
        fw = self.coordinator.data.firmware or {}
        return fw.get("current")

    @property
    def installed_version(self) -> str | None:
        cur = self._current
        return _fmt(cur) if cur else None

    @property
    def latest_version(self) -> str | None:
        fw = self.coordinator.data.firmware or {}
        cur = self._current
        cur_t = version_tuple(cur)
        if cur_t is None:
            return None
        best, best_t = cur, cur_t
        for cand in fw.get("available") or []:
            t = version_tuple(cand)
            # Stay on the installed major.minor train; jumping trains is a
            # deliberate choice, not something to nag about.
            if t and t[:2] == cur_t[:2] and t > best_t:
                best, best_t = cand, t
        return _fmt(best)

    @property
    def release_url(self) -> str | None:
        return "https://docs.fortinet.com/product/fortigate/"
