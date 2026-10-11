"""End-to-end tests against the fake FortiGate."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_HOME, STATE_NOT_HOME, STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_capture_events, async_fire_time_changed

from custom_components.fortigate_local.const import DOMAIN, EVENT_WAN_IP_CHANGED

from .conftest import ENTRY_DATA, INTERFACES, SERIAL, mock_fortigate


def eid(hass: HomeAssistant, platform: str, key: str) -> str:
    uid = key if platform == "device_tracker" else f"{SERIAL}_{key}"
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, uid)
    assert entity_id, f"no {platform} entity for {key}"
    return entity_id


def state(hass, platform, key):
    return hass.states.get(eid(hass, platform, key))


async def setup(hass, entry):
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def poll(hass, entry):
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


# ---- config flow ------------------------------------------------------------

async def test_user_flow_creates_entry(hass, fortigate):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    with patch("custom_components.fortigate_local.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], ENTRY_DATA)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "FG90G-Home"
    assert result["result"].unique_id == SERIAL


async def test_user_flow_bad_token(hass, aioclient_mock):
    mock_fortigate(aioclient_mock, {"monitor/system/status": 401})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], ENTRY_DATA)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_flow_unreachable(hass, aioclient_mock):
    mock_fortigate(aioclient_mock, {"monitor/system/status": 502})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], ENTRY_DATA)
    assert result["errors"] == {"base": "cannot_connect"}


async def test_options_flow_lists_live_choices(hass, fortigate, entry):
    await setup(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    schema = result["data_schema"].schema
    keys = {str(k): v for k, v in schema.items()}
    labels = [o["label"] for o in keys["tracked_devices"].config["options"]]
    assert any("Michaels-iPhone" in label for label in labels)
    policy_values = [o["value"] for o in keys["policies"].config["options"]]
    assert policy_values == ["7", "12"]

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "scan_interval": 45.0,
            "wan_interfaces": ["wan1"],
            "policies": ["12"],
            "tracked_devices": ["aa:bb:cc:dd:ee:02"],
            "consider_home": 5.0,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["scan_interval"] == 45
    assert entry.options["tracked_devices"] == ["aa:bb:cc:dd:ee:02"]
    # Reloaded with the new policy sensor.
    assert state(hass, "binary_sensor", "policy_12").state == STATE_OFF


# ---- entities ---------------------------------------------------------------

async def test_entities_after_setup(hass, fortigate, entry):
    await setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED

    cpu = state(hass, "sensor", "cpu")
    # GUI-style all-core average (100 - idle), not the usage endpoint's 7.
    assert cpu.state == "11.0"
    assert cpu.attributes["cores"] == [8.0, 16.0, 2.0, 21.0]
    assert cpu.attributes["busiest_core"] == 21.0
    assert state(hass, "sensor", "memory").state == "41"
    assert state(hass, "sensor", "sessions").state == "1834"
    assert state(hass, "sensor", "wan_wan1_ip").state == "203.0.113.10"
    assert state(hass, "binary_sensor", "wan_wan1_link").state == STATE_ON
    # Only role=wan interfaces become WANs.
    assert er.async_get(hass).async_get_entity_id(
        "binary_sensor", DOMAIN, f"{SERIAL}_wan_internal_link"
    ) is None

    jan = state(hass, "binary_sensor", "ipsec_Jan_Site")
    assert jan.state == STATE_ON
    assert jan.attributes["remote_gateway"] == "198.51.100.5"
    assert state(hass, "binary_sensor", "ipsec_Travel").state == STATE_OFF
    # Dial-up child instances are skipped.
    assert er.async_get(hass).async_get_entity_id(
        "binary_sensor", DOMAIN, f"{SERIAL}_ipsec_Dialup_0"
    ) is None

    policy = state(hass, "binary_sensor", "policy_7")
    assert policy.state == STATE_ON
    assert policy.name.endswith("Policy Block JellyFish cloud")

    fw = state(hass, "update", "firmware_update")
    assert fw.attributes["installed_version"] == "v7.6.4 build3596"
    # Same 7.6 train only: 7.6.5, not 8.0.0.
    assert fw.attributes["latest_version"] == "v7.6.5 build3651"
    assert fw.state == STATE_ON

    tracker = state(hass, "device_tracker", "aa:bb:cc:dd:ee:01")
    assert tracker.state == STATE_HOME
    assert "Michaels-iPhone" in tracker.name

    # No write-capable platforms at all.
    assert not [e for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
                if e.domain == "switch"]


async def test_rates_and_ip_change_event(hass, aioclient_mock, entry):
    mock_fortigate(aioclient_mock)
    await setup(hass, entry)
    assert state(hass, "sensor", "wan_wan1_download").state == "unknown"

    events = async_capture_events(hass, EVENT_WAN_IP_CHANGED)
    ifaces = {"results": {**INTERFACES["results"]}}
    ifaces["results"]["wan1"] = {
        **INTERFACES["results"]["wan1"],
        "ip": "203.0.113.77",
        "rx_bytes": 1_000_000 + 12_500_000,  # 100 Mbit
        "tx_bytes": 500_000 + 1_250_000,  # 10 Mbit
    }
    mock_fortigate(aioclient_mock, {"monitor/system/interface": ifaces})
    loop = hass.loop
    t0 = loop.time()
    with patch.object(loop, "time", return_value=t0 + 1_000_000):
        await poll(hass, entry)  # big dt keeps the math independent of real time
    down = float(state(hass, "sensor", "wan_wan1_download").state)
    assert down >= 0
    assert state(hass, "sensor", "wan_wan1_ip").state == "203.0.113.77"
    assert len(events) == 1
    assert events[0].data == {"interface": "wan1", "old_ip": "203.0.113.10", "new_ip": "203.0.113.77"}


async def test_rate_math(hass, aioclient_mock, entry):
    mock_fortigate(aioclient_mock)
    await setup(hass, entry)
    coord = entry.runtime_data
    coord._last_counters["wan1"] = (0, 0, 100.0)
    ifaces = {"wan1": {"rx_bytes": 12_500_000, "tx_bytes": 1_250_000}}
    with patch.object(hass.loop, "time", return_value=101.0):
        rates = coord._compute_rates(ifaces)
    assert rates["wan1"] == (100.0, 10.0)
    # Counter reset after a reboot -> unknown, not a huge negative.
    coord._last_counters["wan1"] = (99_000_000, 99_000_000, 100.0)
    with patch.object(hass.loop, "time", return_value=101.0):
        assert coord._compute_rates(ifaces)["wan1"] == (None, None)


async def test_unreachable_goes_unavailable_not_down(hass, aioclient_mock, entry):
    mock_fortigate(aioclient_mock)
    await setup(hass, entry)
    mock_fortigate(aioclient_mock, {"monitor/system/status": 504})
    await poll(hass, entry)
    for platform, key in [
        ("binary_sensor", "wan_wan1_link"),
        ("binary_sensor", "ipsec_Jan_Site"),
        ("binary_sensor", "policy_7"),
        ("sensor", "cpu"),
        ("device_tracker", "aa:bb:cc:dd:ee:01"),
    ]:
        assert state(hass, platform, key).state == STATE_UNAVAILABLE, key


async def test_one_forbidden_endpoint_only_blanks_its_entities(hass, aioclient_mock, entry):
    mock_fortigate(aioclient_mock, {"monitor/vpn/ipsec": 403})
    await setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert state(hass, "sensor", "cpu").state == "11.0"
    mock_fortigate(aioclient_mock)
    await poll(hass, entry)
    # Tunnels appear once the endpoint becomes readable.
    assert state(hass, "binary_sensor", "ipsec_Jan_Site").state == STATE_ON
    mock_fortigate(aioclient_mock, {"monitor/vpn/ipsec": 403})
    await poll(hass, entry)
    assert state(hass, "binary_sensor", "ipsec_Jan_Site").state == STATE_UNAVAILABLE
    assert state(hass, "binary_sensor", "wan_wan1_link").state == STATE_ON


async def test_bad_token_starts_reauth(hass, aioclient_mock, entry):
    mock_fortigate(aioclient_mock, {"monitor/system/status": 401})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == "reauth" for f in flows)


async def test_presence_rides_out_short_gaps(hass, aioclient_mock, entry):
    mock_fortigate(aioclient_mock)
    await setup(hass, entry)
    tracker_id = eid(hass, "device_tracker", "aa:bb:cc:dd:ee:01")
    gone = {"results": [{"ip": "192.168.1.51", "mac": "aa:bb:cc:dd:ee:02"}]}
    mock_fortigate(aioclient_mock, {"monitor/network/arp": gone})

    now = dt_util.utcnow()
    with patch("homeassistant.util.dt.utcnow", return_value=now + timedelta(minutes=5)):
        await poll(hass, entry)
        assert hass.states.get(tracker_id).state == STATE_HOME
    with patch("homeassistant.util.dt.utcnow", return_value=now + timedelta(minutes=11)):
        await poll(hass, entry)
        assert hass.states.get(tracker_id).state == STATE_NOT_HOME


async def test_resource_usage_requested_without_filters(hass, fortigate, entry):
    """FortiOS 7.6.7 returns HTTP 400 for resource=cpu,mem,session."""
    await setup(hass, entry)
    calls = [c for c in fortigate.mock_calls if "resource/usage" in str(c[1])]
    assert calls
    for _method, url, *_ in calls:
        assert "resource=" not in str(url)


async def test_cpu_falls_back_when_performance_unreadable(hass, aioclient_mock, entry):
    mock_fortigate(aioclient_mock, {"monitor/system/performance/status": 403})
    await setup(hass, entry)
    assert state(hass, "sensor", "cpu").state == "7"


async def test_cpu_average_from_cores_when_no_aggregate(hass, aioclient_mock, entry):
    perf = {"results": {"cpu": {"cores": [{"idle": 90}, {"idle": 70}]}}}
    mock_fortigate(aioclient_mock, {"monitor/system/performance/status": perf})
    await setup(hass, entry)
    assert state(hass, "sensor", "cpu").state == "20.0"


async def test_diagnostics_redacts_token(hass, fortigate, entry):
    from custom_components.fortigate_local.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    await setup(hass, entry)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["entry"]["api_token"] == "**REDACTED**"
    assert diag["raw"]["status"]["serial"] == "**REDACTED**"
    assert diag["raw"]["performance"]["cpu"]["idle"] == 89
    assert diag["sections_available"]["performance"] is True
