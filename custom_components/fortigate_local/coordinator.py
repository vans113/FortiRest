"""Polling coordinator for FortiGate Local."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    FortiGateApi,
    FortiGateAuthError,
    FortiGateError,
    FortiGatePermissionError,
)
from .const import (
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    EVENT_WAN_IP_CHANGED,
    FIRMWARE_REFRESH,
    OPT_SCAN_INTERVAL,
    OPT_TRACKED_DEVICES,
    OPT_WAN_INTERFACES,
)

_LOGGER = logging.getLogger(__name__)


def normalize_mac(mac: str) -> str:
    """Lowercase, colon-separated MAC."""
    return mac.strip().lower().replace("-", ":")


def version_tuple(fw: dict[str, Any] | None) -> tuple[int, int, int, int] | None:
    """(major, minor, patch, build) from a FortiOS firmware dict."""
    if not fw:
        return None
    try:
        return (
            int(fw.get("major", 0)),
            int(fw.get("minor", 0)),
            int(fw.get("patch", 0)),
            int(fw.get("build", 0)),
        )
    except (TypeError, ValueError):
        return None


def tunnel_is_up(tunnel: dict[str, Any]) -> bool:
    """A phase-1 tunnel counts as up when any phase-2 selector is up."""
    return any(p.get("status") == "up" for p in tunnel.get("proxyid") or [])


def _usage_from_idle(entry: Any) -> float | None:
    """100 - idle for one CPU block ({"idle": n, ...})."""
    if not isinstance(entry, dict) or entry.get("idle") is None:
        return None
    try:
        return round(max(0.0, min(100.0, 100.0 - float(entry["idle"]))), 1)
    except (TypeError, ValueError):
        return None


def cpu_from_performance(perf: dict[str, Any] | None) -> tuple[float | None, list[float]]:
    """(all-core average %, per-core %) from monitor/system/performance/status."""
    if not perf:
        return None, []
    cpu = perf.get("cpu")
    if not isinstance(cpu, dict):
        return None, []
    cores = [u for u in (_usage_from_idle(c) for c in cpu.get("cores") or []) if u is not None]
    avg = _usage_from_idle(cpu)
    if avg is None and cores:
        avg = round(sum(cores) / len(cores), 1)
    return avg, cores


@dataclass
class FortiGateData:
    """One poll's worth of data. A section is None when its endpoint failed."""

    status: dict[str, Any]
    resources: dict[str, Any] | None = None
    performance: dict[str, Any] | None = None
    interfaces: dict[str, dict[str, Any]] | None = None
    ipsec: dict[str, dict[str, Any]] | None = None
    policies: dict[int, dict[str, Any]] | None = None
    firmware: dict[str, Any] | None = None
    arp_macs: set[str] | None = None
    # Mbps per interface name: (download/rx, upload/tx); None until two samples.
    rates: dict[str, tuple[float | None, float | None]] = field(default_factory=dict)


class FortiGateCoordinator(DataUpdateCoordinator[FortiGateData]):
    """Fetches everything in one poll so entities share a single API round."""

    config_entry: ConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, api: FortiGateApi
    ) -> None:
        interval = entry.options.get(OPT_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=interval),
        )
        self.api = api
        self.serial: str = entry.unique_id or entry.entry_id
        self.detected_wans: list[str] = []
        self._firmware: dict[str, Any] | None = None
        self._firmware_fetched: datetime | None = None
        self._last_counters: dict[str, tuple[int, int, float]] = {}
        self._last_wan_ip: dict[str, str | None] = {}
        self.last_seen: dict[str, datetime] = {}
        self._warned: set[str] = set()
        # Latest raw payloads for the diagnostics download (no secrets in these).
        self.raw: dict[str, Any] = {}

    # ---- options helpers -------------------------------------------------

    @property
    def wan_interfaces(self) -> list[str]:
        """WANs chosen in options, else the ones with role=wan."""
        return list(
            self.config_entry.options.get(OPT_WAN_INTERFACES) or self.detected_wans
        )

    @property
    def tracked_macs(self) -> list[str]:
        """MACs picked for presence tracking."""
        return [
            normalize_mac(m)
            for m in self.config_entry.options.get(OPT_TRACKED_DEVICES, [])
        ]

    # ---- polling -----------------------------------------------------------

    async def _async_setup(self) -> None:
        """One-time: find role=wan interfaces as the default WAN set."""
        try:
            config = await self.api.get_interface_config()
        except FortiGateAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except FortiGateError as err:
            _LOGGER.debug("Could not read interface roles: %s", err)
            return
        self.detected_wans = [
            i["name"] for i in config if i.get("role") == "wan" and i.get("name")
        ]

    async def _optional(self, name: str, coro) -> Any:
        """Run an endpoint whose failure should only blank its own entities."""
        try:
            result = await coro
        except FortiGateAuthError:
            raise
        except FortiGatePermissionError as err:
            if name not in self._warned:
                _LOGGER.warning(
                    "%s unavailable: the API admin profile can't read it (%s)", name, err
                )
                self._warned.add(name)
            return None
        except FortiGateError as err:
            # Warn once per outage so a broken endpoint isn't silent; debug after.
            if name not in self._warned:
                _LOGGER.warning("%s unavailable: %s", name, err)
                self._warned.add(name)
            else:
                _LOGGER.debug("%s fetch failed: %s", name, err)
            return None
        self._warned.discard(name)
        return result

    async def _async_update_data(self) -> FortiGateData:
        try:
            status = await self.api.get_status()
        except FortiGateAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except FortiGateError as err:
            # Whole device unreachable: every entity goes unavailable, never "down".
            raise UpdateFailed(str(err)) from err

        try:
            resources, performance, interfaces, ipsec, policies, arp = await asyncio.gather(
                self._optional("Resource usage", self.api.get_resources()),
                self._optional("Performance status", self.api.get_performance()),
                self._optional("Interfaces", self.api.get_interfaces()),
                self._optional("IPsec", self.api.get_ipsec()),
                self._optional("Firewall policies", self.api.get_policies()),
                self._optional("ARP table", self.api.get_arp()),
            )
        except FortiGateAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err

        now = dt_util.utcnow()
        if self._firmware_fetched is None or now - self._firmware_fetched > FIRMWARE_REFRESH:
            fw = await self._optional("Firmware", self.api.get_firmware())
            if fw is not None:
                self._firmware = fw
                self._firmware_fetched = now

        data = FortiGateData(
            status=status,
            resources=resources,
            performance=performance,
            interfaces=interfaces,
            ipsec=(
                # Skip per-client dial-up instances; they'd create entity churn.
                {t["name"]: t for t in ipsec if t.get("name") and not t.get("parent")}
                if ipsec is not None
                else None
            ),
            policies=(
                {int(p["policyid"]): p for p in policies if "policyid" in p}
                if policies is not None
                else None
            ),
            firmware=self._firmware,
            arp_macs=(
                {normalize_mac(a["mac"]) for a in arp if a.get("mac")}
                if arp is not None
                else None
            ),
        )

        self.raw = {"status": status, "resources": resources, "performance": performance}

        if interfaces is not None:
            data.rates = self._compute_rates(interfaces)
            self._check_wan_ips(interfaces)

        if data.arp_macs is not None:
            for mac in self.tracked_macs:
                if mac in data.arp_macs:
                    self.last_seen[mac] = now

        return data

    def _compute_rates(
        self, interfaces: dict[str, dict[str, Any]]
    ) -> dict[str, tuple[float | None, float | None]]:
        """Turn byte counters into Mbps using the previous sample."""
        rates: dict[str, tuple[float | None, float | None]] = {}
        mono = asyncio.get_running_loop().time()
        for name in self.wan_interfaces:
            iface = interfaces.get(name)
            if not iface:
                continue
            try:
                rx, tx = int(iface["rx_bytes"]), int(iface["tx_bytes"])
            except (KeyError, TypeError, ValueError):
                continue
            prev = self._last_counters.get(name)
            self._last_counters[name] = (rx, tx, mono)
            if prev is None:
                rates[name] = (None, None)
                continue
            p_rx, p_tx, p_t = prev
            dt = mono - p_t
            if dt <= 0 or rx < p_rx or tx < p_tx:  # counter reset / reboot
                rates[name] = (None, None)
                continue
            rates[name] = (
                round((rx - p_rx) * 8 / dt / 1_000_000, 2),
                round((tx - p_tx) * 8 / dt / 1_000_000, 2),
            )
        return rates

    def _check_wan_ips(self, interfaces: dict[str, dict[str, Any]]) -> None:
        """Fire an event when a WAN's address changes (not on first sight)."""
        for name in self.wan_interfaces:
            iface = interfaces.get(name)
            if not iface:
                continue
            ip = iface.get("ip") or None
            if ip == "0.0.0.0":
                ip = None
            if name in self._last_wan_ip:
                old = self._last_wan_ip[name]
                if ip != old and ip is not None and old is not None:
                    self.hass.bus.async_fire(
                        EVENT_WAN_IP_CHANGED,
                        {"interface": name, "old_ip": old, "new_ip": ip},
                    )
            if ip is not None:
                self._last_wan_ip[name] = ip
            else:
                self._last_wan_ip.setdefault(name, None)
