"""Sensors: CPU, memory, sessions, and per-WAN IP and throughput."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfDataRate
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import FortiGateConfigEntry
from .coordinator import FortiGateCoordinator, FortiGateData
from .entity import FortiGateEntity


def _resource(key: str) -> Callable[[FortiGateData], Any]:
    def get(data: FortiGateData) -> Any:
        if data.resources is None:
            return None
        series = data.resources.get(key)
        if isinstance(series, list) and series:
            return series[0].get("current")
        if isinstance(series, dict):
            return series.get("current")
        return None

    return get


@dataclass(frozen=True, kw_only=True)
class SystemSensorDescription(SensorEntityDescription):
    value_fn: Callable[[FortiGateData], Any]
    section: str


SYSTEM_SENSORS: tuple[SystemSensorDescription, ...] = (
    SystemSensorDescription(
        key="cpu",
        name="CPU usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_resource("cpu"),
        section="resources",
    ),
    SystemSensorDescription(
        key="memory",
        name="Memory usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_resource("mem"),
        section="resources",
    ),
    SystemSensorDescription(
        key="sessions",
        name="Sessions",
        native_unit_of_measurement="sessions",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_resource("session"),
        section="resources",
    ),
    SystemSensorDescription(
        key="firmware",
        name="Firmware",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.status.get("version"),
        section="status",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FortiGateConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        FortiGateSystemSensor(coordinator, d) for d in SYSTEM_SENSORS
    ]
    for wan in coordinator.wan_interfaces:
        entities += [
            WanIpSensor(coordinator, wan),
            WanRateSensor(coordinator, wan, download=True),
            WanRateSensor(coordinator, wan, download=False),
        ]
    async_add_entities(entities)


class FortiGateSystemSensor(FortiGateEntity, SensorEntity):
    entity_description: SystemSensorDescription

    def __init__(
        self, coordinator: FortiGateCoordinator, description: SystemSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        return (
            super().available
            and getattr(self.coordinator.data, self.entity_description.section) is not None
        )

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)


class _WanEntity(FortiGateEntity):
    def __init__(self, coordinator: FortiGateCoordinator, wan: str, key: str) -> None:
        super().__init__(coordinator, f"wan_{wan}_{key}")
        self._wan = wan

    @property
    def _iface(self) -> dict[str, Any] | None:
        ifaces = self.coordinator.data.interfaces
        return None if ifaces is None else ifaces.get(self._wan)

    @property
    def available(self) -> bool:
        return super().available and self._iface is not None


class WanIpSensor(_WanEntity, SensorEntity):
    _attr_icon = "mdi:ip-network"

    def __init__(self, coordinator: FortiGateCoordinator, wan: str) -> None:
        super().__init__(coordinator, wan, "ip")
        self._attr_name = f"{wan} IP"

    @property
    def native_value(self) -> str | None:
        ip = (self._iface or {}).get("ip")
        return None if not ip or ip == "0.0.0.0" else ip


class WanRateSensor(_WanEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.DATA_RATE
    _attr_native_unit_of_measurement = UnitOfDataRate.MEGABITS_PER_SECOND
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: FortiGateCoordinator, wan: str, download: bool) -> None:
        super().__init__(coordinator, wan, "download" if download else "upload")
        self._download = download
        self._attr_name = f"{wan} {'download' if download else 'upload'}"
        self._attr_icon = "mdi:download-network" if download else "mdi:upload-network"

    @property
    def native_value(self) -> float | None:
        rate = self.coordinator.data.rates.get(self._wan)
        if rate is None:
            return None
        return rate[0] if self._download else rate[1]
