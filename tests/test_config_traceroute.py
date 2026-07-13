# SPDX-License-Identifier: AGPL-3.0-or-later
from mbg.config import Config


def test_traceroute_defaults():
    c = Config.from_env({})
    assert c.traceroute_enabled is False
    assert c.traceroute_policy == "staleness"
    assert c.traceroute_daily_budget == 6
    assert c.traceroute_hop_limit == 7
    assert c.traceroute_targets == ()
    assert c.traceroute_recent_h == 24.0
    assert c.traceroute_per_node_min_s == 21600.0
    assert c.traceroute_min_gap_s == 900.0
    assert c.traceroute_quiet_hours == "22:00-06:00"
    assert c.traceroute_max_chanutil == 40.0
    assert c.traceroute_priority == ()
    assert c.traceroute_tick_s == 300.0
    assert c.traceroute_topic == "mbg/traceroute"
    assert c.traceroute_active is False  # ni scheduler ni API


def test_traceroute_env_override():
    c = Config.from_env(
        {
            "MBG_TRACEROUTE_ENABLED": "yes",
            "MBG_TRACEROUTE_POLICY": "static",
            "MBG_TRACEROUTE_DAILY_BUDGET": "3",
            "MBG_TRACEROUTE_HOP_LIMIT": "5",
            "MBG_TRACEROUTE_TARGETS": "!6984ddb0, !d1062139 ,",  # espaces + élément vide filtré
            "MBG_TRACEROUTE_RECENT_H": "12",
            "MBG_TRACEROUTE_PER_NODE_MIN_S": "7200",
            "MBG_TRACEROUTE_MIN_GAP_S": "600",
            "MBG_TRACEROUTE_QUIET_HOURS": "23:00-05:00",
            "MBG_TRACEROUTE_MAX_CHANUTIL": "25",
            "MBG_TRACEROUTE_PRIORITY": "!6984ddb0",
            "MBG_TRACEROUTE_TICK_S": "120",
            "MBG_TRACEROUTE_TOPIC": "custom/tr",
        }
    )
    assert c.traceroute_enabled is True
    assert c.traceroute_policy == "static"
    assert c.traceroute_daily_budget == 3
    assert c.traceroute_hop_limit == 5
    assert c.traceroute_targets == ("!6984ddb0", "!d1062139")  # vide filtré
    assert c.traceroute_recent_h == 12.0
    assert c.traceroute_per_node_min_s == 7200.0
    assert c.traceroute_min_gap_s == 600.0
    assert c.traceroute_quiet_hours == "23:00-05:00"
    assert c.traceroute_max_chanutil == 25.0
    assert c.traceroute_priority == ("!6984ddb0",)
    assert c.traceroute_tick_s == 120.0
    assert c.traceroute_topic == "custom/tr"
    assert c.traceroute_active is True


def test_traceroute_active_via_api_token():
    assert Config(api_token="tok").traceroute_active is True
    assert Config(traceroute_enabled=True).traceroute_active is True
    assert Config().traceroute_active is False
