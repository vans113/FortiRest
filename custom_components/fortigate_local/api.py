"""Thin async client for the FortiOS REST API (7.x)."""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

from .const import REQUEST_TIMEOUT


class FortiGateError(Exception):
    """Base error."""


class FortiGateConnectionError(FortiGateError):
    """The FortiGate could not be reached or answered garbage."""


class FortiGateAuthError(FortiGateError):
    """The API token was rejected (401)."""


class FortiGatePermissionError(FortiGateError):
    """The token works but its profile can't read/write this endpoint (403)."""


class FortiGateApi:
    """FortiOS REST API client using a bearer API token."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int,
        token: str,
        verify_ssl: bool,
        vdom: str | None = None,
    ) -> None:
        self._session = session
        self._base = f"https://{host}:{port}/api/v2"
        self._token = token
        self._ssl: bool | None = None if verify_ssl else False
        self._vdom = vdom or None
        self.web_url = f"https://{host}:{port}"

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        query = dict(params or {})
        if self._vdom:
            query["vdom"] = self._vdom
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with self._session.request(
                    method,
                    f"{self._base}/{path}",
                    params=query,
                    json=json,
                    headers=headers,
                    ssl=self._ssl,
                ) as resp:
                    if resp.status == 401:
                        raise FortiGateAuthError("API token rejected")
                    if resp.status == 403:
                        raise FortiGatePermissionError(
                            f"Token profile lacks permission for {path}"
                        )
                    if resp.status >= 400:
                        raise FortiGateConnectionError(
                            f"{method} {path} returned HTTP {resp.status}"
                        )
                    data = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise FortiGateConnectionError(f"{method} {path} failed: {err}") from err

        if not isinstance(data, dict):
            raise FortiGateConnectionError(f"{method} {path}: unexpected payload")
        # FortiOS wraps errors in a 200 sometimes.
        if data.get("status") == "error":
            if data.get("http_status") == 401:
                raise FortiGateAuthError("API token rejected")
            if data.get("http_status") == 403:
                raise FortiGatePermissionError(f"Token profile lacks permission for {path}")
            raise FortiGateConnectionError(f"{method} {path}: {data.get('error')}")
        return data

    # ---- monitor endpoints -------------------------------------------------

    async def get_status(self) -> dict[str, Any]:
        """System status. Top level carries serial/version; results has hostname/model."""
        return await self._request("GET", "monitor/system/status")

    async def get_resources(self) -> dict[str, Any]:
        """CPU / memory / session usage."""
        # No filters: FortiOS 7.6 rejects a comma-separated `resource` list (HTTP 400),
        # and the unfiltered call returns cpu/mem/session together.
        data = await self._request("GET", "monitor/system/resource/usage")
        return data.get("results") or {}

    async def get_interfaces(self) -> dict[str, dict[str, Any]]:
        """Live interface table keyed by interface name."""
        data = await self._request(
            "GET", "monitor/system/interface", params={"include_vlan": "true"}
        )
        results = data.get("results") or {}
        if isinstance(results, list):  # defensive: some builds return a list
            results = {i.get("name"): i for i in results if i.get("name")}
        return results

    async def get_ipsec(self) -> list[dict[str, Any]]:
        """IPsec phase-1 tunnels with their phase-2 selectors."""
        data = await self._request("GET", "monitor/vpn/ipsec")
        return data.get("results") or []

    async def get_firmware(self) -> dict[str, Any]:
        """Current and available firmware (from FortiGuard)."""
        data = await self._request("GET", "monitor/system/firmware")
        return data.get("results") or {}

    async def get_arp(self) -> list[dict[str, Any]]:
        """IPv4 ARP table."""
        data = await self._request("GET", "monitor/network/arp")
        return data.get("results") or []

    async def get_dhcp_leases(self) -> list[dict[str, Any]]:
        """DHCP leases handed out by the FortiGate."""
        data = await self._request("GET", "monitor/system/dhcp")
        results = data.get("results") or []
        # Some builds nest leases per server/interface.
        if isinstance(results, dict):
            flat: list[dict[str, Any]] = []
            for value in results.values():
                if isinstance(value, list):
                    flat.extend(value)
            return flat
        return results

    # ---- cmdb (config) endpoints -----------------------------------------

    async def get_interface_config(self) -> list[dict[str, Any]]:
        """Interface config, used to find interfaces with role=wan."""
        data = await self._request(
            "GET", "cmdb/system/interface", params={"format": "name|alias|role|type"}
        )
        return data.get("results") or []

    async def get_policies(self) -> list[dict[str, Any]]:
        """Firewall policies (id, name, enabled state, action)."""
        data = await self._request(
            "GET",
            "cmdb/firewall/policy",
            params={"format": "policyid|name|status|action|comments"},
        )
        return data.get("results") or []
