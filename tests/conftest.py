"""Test fixtures: a fake FortiGate 90G answering the FortiOS REST API."""

from __future__ import annotations

import copy

import pytest

from homeassistant.const import CONF_HOST, CONF_PORT

from custom_components.fortigate_local.const import (
    CONF_API_TOKEN,
    CONF_VERIFY_SSL,
    DOMAIN,
    OPT_DEVICE_NAMES,
    OPT_POLICIES,
    OPT_TRACKED_DEVICES,
)

BASE = "https://192.168.1.99:443/api/v2"
SERIAL = "FG90GTK0000TEST"
PHONE_MAC = "aa:bb:cc:dd:ee:01"

ENTRY_DATA = {
    CONF_HOST: "192.168.1.99",
    CONF_PORT: 443,
    CONF_API_TOKEN: "test-token",
    CONF_VERIFY_SSL: False,
}
ENTRY_OPTIONS = {
    OPT_POLICIES: ["7"],
    OPT_TRACKED_DEVICES: [PHONE_MAC],
    OPT_DEVICE_NAMES: {PHONE_MAC: "Michaels-iPhone"},
}

STATUS = {
    "http_method": "GET",
    "results": {"model_name": "FortiGate", "model": "FG90G", "hostname": "FG90G-Home"},
    "status": "success",
    "serial": SERIAL,
    "version": "v7.6.4",
    "build": 3596,
}
RESOURCES = {
    "results": {
        "cpu": [{"current": 7, "historical": {}}],
        "mem": [{"current": 41, "historical": {}}],
        "session": [{"current": 1834, "historical": {}}],
    },
    "status": "success",
}
INTERFACES = {
    "results": {
        "wan1": {
            "name": "wan1", "ip": "203.0.113.10", "mask": 24, "link": True,
            "speed": 1000, "duplex": 1, "rx_bytes": 1_000_000, "tx_bytes": 500_000,
        },
        "internal": {"name": "internal", "ip": "192.168.1.99", "link": True,
                     "rx_bytes": 1, "tx_bytes": 1},
    },
    "status": "success",
}
IFACE_CONFIG = {
    "results": [
        {"name": "wan1", "role": "wan", "alias": "AT&T", "type": "physical"},
        {"name": "internal", "role": "lan", "alias": "", "type": "hard-switch"},
    ],
    "status": "success",
}
IPSEC = {
    "results": [
        {
            "name": "Jan_Site", "rgwy": "198.51.100.5",
            "incoming_bytes": 1234, "outgoing_bytes": 5678,
            "proxyid": [{"p2name": "Jan_Site", "status": "up",
                         "incoming_bytes": 1234, "outgoing_bytes": 5678}],
        },
        {
            "name": "Travel", "rgwy": "0.0.0.0",
            "proxyid": [{"p2name": "Travel", "status": "down"}],
        },
        {"name": "Dialup_0", "parent": "Dialup", "proxyid": [{"status": "up"}]},
    ],
    "status": "success",
}
POLICIES = {
    "results": [
        {"policyid": 7, "name": "Block JellyFish cloud", "status": "enable", "action": "deny"},
        {"policyid": 12, "name": "Cynthia PC internet", "status": "disable", "action": "accept"},
    ],
    "status": "success",
}
FIRMWARE = {
    "results": {
        "current": {"version": "v7.6.4", "major": 7, "minor": 6, "patch": 4, "build": 3596},
        "available": [
            {"version": "v7.6.5", "major": 7, "minor": 6, "patch": 5, "build": 3651},
            {"version": "v8.0.0", "major": 8, "minor": 0, "patch": 0, "build": 100},
            {"version": "v7.4.9", "major": 7, "minor": 4, "patch": 9, "build": 2900},
        ],
    },
    "status": "success",
}
ARP = {
    "results": [
        {"ip": "192.168.1.50", "mac": "AA:BB:CC:DD:EE:01", "interface": "internal"},
        {"ip": "192.168.1.51", "mac": "aa:bb:cc:dd:ee:02", "interface": "internal"},
    ],
    "status": "success",
}
DHCP = {
    "results": [
        {"ip": "192.168.1.50", "mac": "aa:bb:cc:dd:ee:01", "hostname": "Michaels-iPhone",
         "interface": "internal"},
    ],
    "status": "success",
}

ENDPOINTS = {
    "monitor/system/status": STATUS,
    "monitor/system/resource/usage": RESOURCES,
    "monitor/system/interface": INTERFACES,
    "cmdb/system/interface": IFACE_CONFIG,
    "monitor/vpn/ipsec": IPSEC,
    "cmdb/firewall/policy": POLICIES,
    "monitor/system/firmware": FIRMWARE,
    "monitor/network/arp": ARP,
    "monitor/system/dhcp": DHCP,
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let HA load custom_components/ in tests."""
    return


def mock_fortigate(aioclient_mock, overrides: dict | None = None) -> None:
    """Register every endpoint; overrides map path -> payload or HTTP status int."""
    aioclient_mock.clear_requests()
    overrides = overrides or {}
    for path, payload in ENDPOINTS.items():
        value = overrides.get(path, payload)
        if isinstance(value, int):
            aioclient_mock.get(f"{BASE}/{path}", status=value)
        else:
            aioclient_mock.get(f"{BASE}/{path}", json=copy.deepcopy(value))


@pytest.fixture
def fortigate(aioclient_mock):
    mock_fortigate(aioclient_mock)
    return aioclient_mock


@pytest.fixture
def entry():
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=SERIAL,
        data=dict(ENTRY_DATA),
        options=copy.deepcopy(ENTRY_OPTIONS),
        title="FG90G-Home",
    )
