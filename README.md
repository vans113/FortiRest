# FortiGate Local

Read-only Home Assistant integration for a FortiGate firewall, using the FortiOS REST API over the LAN. No cloud, and no write access to the firewall.

## What you get

| Entity | Source |
|---|---|
| CPU, memory, sessions | `monitor/system/resource/usage` |
| Per-WAN link, IP, download/upload Mbps | `monitor/system/interface` (WANs default to interfaces with role `wan`) |
| One connectivity sensor per IPsec tunnel | `monitor/vpn/ipsec` (new tunnels appear automatically; dial-up child instances are skipped) |
| Enabled/disabled state of the policies you pick | `cmdb/firewall/policy` |
| Firmware update (same major.minor train only; never installs) | `monitor/system/firmware` |
| Presence for the devices you pick | `monitor/network/arp`, with a configurable consider-home window |

Event `fortigate_local_wan_ip_changed` fires with `interface`, `old_ip`, `new_ip` when a WAN address changes.

If the FortiGate can't be reached, every entity goes **unavailable**, never "down" or "away". If the API profile can't read one endpoint, only that endpoint's entities go unavailable.

## FortiGate setup

1. **System → Admin Profiles → Create New**: set everything to **Read**.
2. **System → Administrators → Create New → REST API Admin**: assign that profile and set **Trusted Hosts** to your Home Assistant IP (`/32`).
3. Copy the token.

## Install

HACS → Custom repositories → add this repo as **Integration** → download → restart HA → **Settings → Devices & services → Add integration → FortiGate Local**. Then open **Configure** to pick WANs, policies, and devices.

## Development

```
pip install pytest-homeassistant-custom-component
pytest
```
