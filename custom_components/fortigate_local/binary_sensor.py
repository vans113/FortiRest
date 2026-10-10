"""Binary sensors: WAN link, IPsec tunnels, and watched policy state (read-only)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import FortiGateConfigEntry
from .const import OPT_POLICIES
from .coordinator import FortiGateCoordinator, tunnel_is_up
from .entity import FortiGateEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FortiGateConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(WanLinkSensor(coordinator, wan) for wan in coordinator.wan_interfaces)
    async_add_entities(
        PolicyEnabledSensor(coordinator, int(pid))
        for pid in entry.options.get(OPT_POLICIES, [])
    )

    known: set[str] = set()

    @callback
    def _add_new_tunnels() -> None:
        tunnels = coordinator.data.ipsec if coordinator.data else None
        if not tunnels:
            return
        new = [name for name in tunnels if name not in known]
        if new:
            known.update(new)
            async_add_entities(IpsecTunnelSensor(coordinator, name) for name in new)

    _add_new_tunnels()
    # A tunnel added on the FortiGate later (e.g. the travel router) shows up on its own.
    entry.async_on_unload(coordinator.async_add_listener(_add_new_tunnels))


class WanLinkSensor(FortiGateEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: FortiGateCoordinator, wan: str) -> None:
        super().__init__(coordinator, f"wan_{wan}_link")
        self._wan = wan
        self._attr_name = f"{wan} link"

    @property
    def _iface(self) -> dict[str, Any] | None:
        ifaces = self.coordinator.data.interfaces
        return None if ifaces is None else ifaces.get(self._wan)

    @property
    def available(self) -> bool:
        return super().available and self._iface is not None

    @property
    def is_on(self) -> bool | None:
        link = (self._iface or {}).get("link")
        return None if link is None else bool(link)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        iface = self._iface or {}
        return {k: iface.get(k) for k in ("speed", "duplex", "mac") if iface.get(k) is not None}


class IpsecTunnelSensor(FortiGateEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_icon = "mdi:vpn"

    def __init__(self, coordinator: FortiGateCoordinator, tunnel: str) -> None:
        super().__init__(coordinator, f"ipsec_{tunnel}")
        self._tunnel = tunnel
        self._attr_name = f"VPN {tunnel}"

    @property
    def _data(self) -> dict[str, Any] | None:
        tunnels = self.coordinator.data.ipsec
        return None if tunnels is None else tunnels.get(self._tunnel)

    @property
    def available(self) -> bool:
        # Tunnel deleted on the FortiGate -> unavailable, not "disconnected".
        return super().available and self._data is not None

    @property
    def is_on(self) -> bool:
        return tunnel_is_up(self._data or {})

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        t = self._data or {}
        selectors = t.get("proxyid") or []
        attrs: dict[str, Any] = {
            "remote_gateway": t.get("rgwy"),
            "incoming_bytes": t.get(
                "incoming_bytes", sum(p.get("incoming_bytes", 0) for p in selectors)
            ),
            "outgoing_bytes": t.get(
                "outgoing_bytes", sum(p.get("outgoing_bytes", 0) for p in selectors)
            ),
            "phase2_up": sum(1 for p in selectors if p.get("status") == "up"),
            "phase2_total": len(selectors),
        }
        return {k: v for k, v in attrs.items() if v is not None}


class PolicyEnabledSensor(FortiGateEntity, BinarySensorEntity):
    """On = the policy is enabled on the FortiGate. Read-only by design."""

    _attr_icon = "mdi:wall-fire"

    def __init__(self, coordinator: FortiGateCoordinator, policy_id: int) -> None:
        super().__init__(coordinator, f"policy_{policy_id}")
        self._pid = policy_id
        policy = self._policy or {}
        self._attr_name = f"Policy {policy.get('name') or policy_id}"

    @property
    def _policy(self) -> dict[str, Any] | None:
        policies = self.coordinator.data.policies
        return None if policies is None else policies.get(self._pid)

    @property
    def available(self) -> bool:
        # Policy deleted or unreadable -> unavailable rather than a misleading "off".
        return super().available and self._policy is not None

    @property
    def is_on(self) -> bool:
        return (self._policy or {}).get("status") == "enable"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        p = self._policy or {}
        attrs = {"policy_id": self._pid, "action": p.get("action"), "comments": p.get("comments")}
        return {k: v for k, v in attrs.items() if v}
