"""Constants for the FortiGate Local integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "fortigate_local"

CONF_API_TOKEN = "api_token"
CONF_VERIFY_SSL = "verify_ssl"
CONF_VDOM = "vdom"

OPT_SCAN_INTERVAL = "scan_interval"
OPT_WAN_INTERFACES = "wan_interfaces"
OPT_POLICIES = "policies"
OPT_TRACKED_DEVICES = "tracked_devices"
OPT_CONSIDER_HOME = "consider_home"
OPT_DEVICE_NAMES = "device_names"

DEFAULT_PORT = 443
DEFAULT_VERIFY_SSL = False
DEFAULT_SCAN_INTERVAL = 30  # seconds
MIN_SCAN_INTERVAL = 10
DEFAULT_CONSIDER_HOME = 10  # minutes

# Firmware availability changes rarely; don't ask FortiGuard every poll.
FIRMWARE_REFRESH = timedelta(hours=6)

REQUEST_TIMEOUT = 15  # seconds

EVENT_WAN_IP_CHANGED = f"{DOMAIN}_wan_ip_changed"
