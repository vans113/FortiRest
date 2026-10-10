"""Base entity for FortiGate Local."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import FortiGateCoordinator


class FortiGateEntity(CoordinatorEntity[FortiGateCoordinator]):
    """Ties every entity to the one firewall device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: FortiGateCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial}_{key}"
        status = coordinator.data.status if coordinator.data else {}
        results = status.get("results") or {}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.serial)},
            manufacturer="Fortinet",
            model=results.get("model_name") or results.get("model"),
            name=results.get("hostname") or "FortiGate",
            serial_number=coordinator.serial,
            sw_version=status.get("version"),
            configuration_url=coordinator.api.web_url,
        )
