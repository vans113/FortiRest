"""Config and options flow for FortiGate Local."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    FortiGateApi,
    FortiGateAuthError,
    FortiGateConnectionError,
    FortiGateError,
    FortiGatePermissionError,
)
from .const import (
    CONF_API_TOKEN,
    CONF_VDOM,
    CONF_VERIFY_SSL,
    DEFAULT_CONSIDER_HOME,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
    OPT_CONSIDER_HOME,
    OPT_DEVICE_NAMES,
    OPT_POLICIES,
    OPT_SCAN_INTERVAL,
    OPT_TRACKED_DEVICES,
    OPT_WAN_INTERFACES,
)
from .coordinator import normalize_mac

_LOGGER = logging.getLogger(__name__)

PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


async def _validate(hass, data: dict[str, Any]) -> dict[str, Any]:
    """Connect once and return the status payload (raises on failure)."""
    from . import build_api  # avoid import cycle at module load

    api: FortiGateApi = build_api(hass, data)
    return await api.get_status()


def _error_key(err: Exception) -> str:
    if isinstance(err, (FortiGateAuthError, FortiGatePermissionError)):
        return "invalid_auth"
    if isinstance(err, FortiGateConnectionError):
        return "cannot_connect"
    return "unknown"


class FortiGateConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle setup of a FortiGate."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                status = await _validate(self.hass, user_input)
            except FortiGateError as err:
                errors["base"] = _error_key(err)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error validating FortiGate")
                errors["base"] = "unknown"
            else:
                serial = status.get("serial")
                if not serial:
                    errors["base"] = "cannot_connect"
                else:
                    await self.async_set_unique_id(serial)
                    self._abort_if_unique_id_configured()
                    results = status.get("results") or {}
                    return self.async_create_entry(
                        title=results.get("hostname") or "FortiGate", data=user_input
                    )

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=65535)
                ),
                vol.Required(CONF_API_TOKEN): PASSWORD,
                vol.Required(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): bool,
                vol.Optional(CONF_VDOM): str,
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or {}),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            data = {**entry.data, CONF_API_TOKEN: user_input[CONF_API_TOKEN]}
            try:
                status = await _validate(self.hass, data)
            except FortiGateError as err:
                errors["base"] = _error_key(err)
            else:
                await self.async_set_unique_id(status.get("serial"))
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(entry, data=data)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_TOKEN): PASSWORD}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return FortiGateOptionsFlow()


class FortiGateOptionsFlow(OptionsFlow):
    """Pick WANs, policies to watch, and phones to track."""

    def __init__(self) -> None:
        self._hostnames: dict[str, str] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            user_input[OPT_TRACKED_DEVICES] = [
                normalize_mac(m) for m in user_input.get(OPT_TRACKED_DEVICES, [])
            ]
            user_input[OPT_POLICIES] = [str(p) for p in user_input.get(OPT_POLICIES, [])]
            user_input[OPT_SCAN_INTERVAL] = int(user_input[OPT_SCAN_INTERVAL])
            user_input[OPT_CONSIDER_HOME] = int(user_input[OPT_CONSIDER_HOME])
            known = {**self.config_entry.options.get(OPT_DEVICE_NAMES, {}), **self._hostnames}
            user_input[OPT_DEVICE_NAMES] = {
                m: known[m] for m in user_input[OPT_TRACKED_DEVICES] if known.get(m)
            }
            return self.async_create_entry(data=user_input)

        coordinator = self.config_entry.runtime_data
        api = coordinator.api
        opts = self.config_entry.options

        # Interfaces
        iface_opts: list[SelectOptionDict] = []
        try:
            for i in await api.get_interface_config():
                name = i.get("name")
                if not name:
                    continue
                label = name
                if i.get("alias"):
                    label += f" ({i['alias']})"
                if i.get("role"):
                    label += f" [{i['role']}]"
                iface_opts.append(SelectOptionDict(value=name, label=label))
        except FortiGateError as err:
            _LOGGER.warning("Could not list interfaces: %s", err)

        # Policies
        policy_opts: list[SelectOptionDict] = []
        try:
            for p in await api.get_policies():
                pid = p.get("policyid")
                if pid is None:
                    continue
                name = p.get("name") or "(unnamed)"
                state = "" if p.get("status") == "enable" else " — currently disabled"
                policy_opts.append(
                    SelectOptionDict(
                        value=str(pid), label=f"#{pid} {name} [{p.get('action', '?')}]{state}"
                    )
                )
        except FortiGateError as err:
            _LOGGER.warning("Could not list policies: %s", err)

        # Devices: DHCP leases first (they carry hostnames), then ARP-only entries.
        device_opts: dict[str, str] = {}
        try:
            for lease in await api.get_dhcp_leases():
                mac = lease.get("mac")
                if not mac:
                    continue
                mac = normalize_mac(mac)
                host = lease.get("hostname") or "unknown"
                if lease.get("hostname"):
                    self._hostnames[mac] = lease["hostname"]
                device_opts[mac] = (
                    f"{host} — {lease.get('ip', '?')} ({mac}) on {lease.get('interface', '?')}"
                )
        except FortiGateError as err:
            _LOGGER.warning("Could not list DHCP leases: %s", err)
        try:
            for a in await api.get_arp():
                mac = a.get("mac")
                if mac and normalize_mac(mac) not in device_opts:
                    mac = normalize_mac(mac)
                    device_opts[mac] = f"{a.get('ip', '?')} ({mac}) on {a.get('interface', '?')}"
        except FortiGateError as err:
            _LOGGER.warning("Could not read ARP table: %s", err)
        # Keep already-tracked devices selectable even if they're away right now.
        for mac in opts.get(OPT_TRACKED_DEVICES, []):
            device_opts.setdefault(mac, f"{mac} (not currently on the network)")
        for pid in opts.get(OPT_POLICIES, []):
            if pid not in {o["value"] for o in policy_opts}:
                policy_opts.append(SelectOptionDict(value=pid, label=f"#{pid} (missing)"))

        device_list = sorted(
            (SelectOptionDict(value=k, label=v) for k, v in device_opts.items()),
            key=lambda o: o["label"].lower(),
        )

        schema = vol.Schema(
            {
                vol.Required(
                    OPT_SCAN_INTERVAL,
                    default=opts.get(OPT_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL,
                        max=300,
                        step=5,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(
                    OPT_WAN_INTERFACES,
                    default=opts.get(OPT_WAN_INTERFACES) or coordinator.detected_wans,
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=iface_opts, multiple=True, mode=SelectSelectorMode.DROPDOWN
                    )
                ),
                vol.Optional(
                    OPT_POLICIES, default=opts.get(OPT_POLICIES, [])
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=policy_opts, multiple=True, mode=SelectSelectorMode.DROPDOWN
                    )
                ),
                vol.Optional(
                    OPT_TRACKED_DEVICES, default=opts.get(OPT_TRACKED_DEVICES, [])
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=device_list, multiple=True, mode=SelectSelectorMode.DROPDOWN
                    )
                ),
                vol.Required(
                    OPT_CONSIDER_HOME,
                    default=opts.get(OPT_CONSIDER_HOME, DEFAULT_CONSIDER_HOME),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=60,
                        step=1,
                        unit_of_measurement="min",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
