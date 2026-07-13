# SPDX-License-Identifier: AGPL-3.0-or-later
import json

import pytest

from mbg import traceroute as tr


# --- Fonctions pures ---------------------------------------------------------
def test_hexid():
    assert tr.hexid(0x6984DDB0) == "!6984ddb0"
    assert tr.hexid(0xAABBCCDD) == "!aabbccdd"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("!6984ddb0", (0x6984DDB0, "!6984ddb0")),
        ("6984ddb0", (0x6984DDB0, "!6984ddb0")),
        (0x6984DDB0, (0x6984DDB0, "!6984ddb0")),
    ],
)
def test_normalize_dest_ok(value, expected):
    assert tr.normalize_dest(value) == expected


@pytest.mark.parametrize(
    "value",
    [True, "", "!", "xyz", 0, 0xFFFFFFFF, -1, 0x1_0000_0000, None, 1.5],
)
def test_normalize_dest_invalid(value):
    with pytest.raises(ValueError):
        tr.normalize_dest(value)


def test_decode_snr():
    assert tr.decode_snr(tr.UNKNOWN_SNR) is None
    assert tr.decode_snr(24) == 6.0
    assert tr.decode_snr(-34) == -8.5


def test_align_snr_match_and_mismatch():
    assert tr._align_snr([24, -34], 2) == [6.0, -8.5]
    # mauvais alignement (firmware partiel) -> tout inconnu
    assert tr._align_snr([24], 2) == [None, None]


def test_decode_route_forward_only():
    # route us(1) -> R(2) -> dest(3), pas de retour
    out = tr.decode_route(
        route=[2], snr_towards=[20, 24], route_back=[], snr_back=[],
        origin_num=1, dest_num=3, id_of=tr.hexid,
    )
    assert out["hops_to"] == 2
    assert out["route_to"] == [
        {"node": "!00000001", "snr": None},
        {"node": "!00000002", "snr": 5.0},
        {"node": "!00000003", "snr": 6.0},
    ]
    assert out["route_back"] is None and out["hops_back"] is None


def test_decode_route_direct_neighbor():
    # route vide = voisin direct : chemin [origin, dest]
    out = tr.decode_route(
        route=[], snr_towards=[24], route_back=[], snr_back=[],
        origin_num=1, dest_num=3, id_of=tr.hexid,
    )
    assert out["hops_to"] == 1
    assert [n["node"] for n in out["route_to"]] == ["!00000001", "!00000003"]


def test_decode_route_with_back():
    out = tr.decode_route(
        route=[2], snr_towards=[20, 24], route_back=[2], snr_back=[-32, tr.UNKNOWN_SNR],
        origin_num=1, dest_num=3, id_of=tr.hexid,
    )
    assert out["hops_back"] == 2
    assert out["route_back"] == [
        {"node": "!00000003", "snr": None},
        {"node": "!00000002", "snr": -8.0},
        {"node": "!00000001", "snr": None},  # sentinelle -128 -> None
    ]


def test_iso_and_build_result_timeout():
    r = tr.build_result(
        status="timeout", gateway_node="!aa", dest="!bb", request_id=7,
        hop_limit=7, sent_ts=1000.0, recv_ts=None,
    )
    assert r["sent_ts"] == "1970-01-01T00:16:40Z"
    assert r["recv_ts"] is None and r["rtt_ms"] is None
    assert r["route_to"] is None and "error" not in r


def test_build_result_ok_and_error():
    route = {"route_to": [{"node": "!aa", "snr": None}], "route_back": None, "hops_to": 1, "hops_back": None}
    ok = tr.build_result(
        status="ok", gateway_node="!gw", dest="!bb", request_id=7, hop_limit=7,
        sent_ts=1000.0, recv_ts=1004.12, route=route, source="scheduler:staleness",
    )
    assert ok["rtt_ms"] == 4120 and ok["hops_to"] == 1 and ok["source"] == "scheduler:staleness"
    err = tr.build_result(
        status="error", gateway_node=None, dest="!bb", request_id=0, hop_limit=7,
        sent_ts=1000.0, error="BLE down",
    )
    assert err["error"] == "BLE down" and err["status"] == "error"


# --- Coordinateur ------------------------------------------------------------
class FakeTimer:
    def __init__(self, interval, callback):
        self.interval = interval
        self.callback = callback
        self.started = False
        self.cancelled = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        self.callback()


class FakeStore:
    def __init__(self, raise_on_write=False):
        self.rows = []
        self._raise = raise_on_write

    def record_traceroute(self, result, sent_epoch, recv_epoch):
        if self._raise:
            raise RuntimeError("db locked")
        self.rows.append((result, sent_epoch, recv_epoch))


def _route_discovery_bytes(route, snr_towards, route_back=(), snr_back=()):
    from meshtastic.protobuf import mesh_pb2

    rd = mesh_pb2.RouteDiscovery()
    rd.route.extend(route)
    rd.snr_towards.extend(snr_towards)
    rd.route_back.extend(route_back)
    rd.snr_back.extend(snr_back)
    return rd.SerializeToString()


def _make_coord(store=None, publish=None, send=None, clock=None, timers=None):
    timers = timers if timers is not None else []

    def timer_factory(interval, cb):
        t = FakeTimer(interval, cb)
        timers.append(t)
        return t

    clk = clock or (lambda: 1000.0)
    coord = tr.TracerouteCoordinator(
        send_fn=send or (lambda dest, hop, ch: 12345),
        publish_fn=publish or (lambda topic, payload: None),
        store=store,
        id_of=tr.hexid,
        gateway_id_fn=lambda: "!362e105b",
        topic="mbg/traceroute",
        clock=clk,
        timer_factory=timer_factory,
    )
    return coord, timers


def test_coordinator_start_success():
    store = FakeStore()
    published = []
    coord, timers = _make_coord(store=store, publish=lambda t, p: published.append((t, p)))
    res = coord.start("!00000003", hop_limit=5, channel_index=0, timeout_s=30, source="api")
    assert res == {"ok": True, "request_id": 12345, "dest": "!00000003"}
    assert timers[0].started and timers[0].daemon is True
    assert published == []  # rien publié tant que pas de réponse


def test_coordinator_start_send_fails():
    store = FakeStore()
    published = []

    def boom(dest, hop, ch):
        raise RuntimeError("BLE down")

    coord, _ = _make_coord(store=store, send=boom, publish=lambda t, p: published.append((t, p)))
    res = coord.start("!00000003")
    assert res["ok"] is False and "BLE down" in res["error"]
    # une ligne error écrite + publiée
    assert store.rows[0][0]["status"] == "error"
    assert published[0][0] == "mbg/traceroute"
    assert json.loads(published[0][1])["status"] == "error"


def test_coordinator_on_packet_success():
    store = FakeStore()
    published = []
    clock_vals = iter([1000.0, 1004.0])  # sent, recv
    coord, timers = _make_coord(
        store=store, publish=lambda t, p: published.append((t, p)),
        clock=lambda: next(clock_vals),
    )
    coord.start("!00000003", hop_limit=7, timeout_s=30)
    payload = _route_discovery_bytes([2], [20, 24])
    coord.on_packet({
        "from": 3, "to": 1,
        "decoded": {"portnum": "TRACEROUTE_APP", "requestId": 12345, "payload": payload},
    })
    assert timers[0].cancelled
    result = store.rows[0][0]
    assert result["status"] == "ok" and result["rtt_ms"] == 4000
    assert result["gateway_node"] == "!362e105b"
    assert [n["node"] for n in result["route_to"]] == ["!00000001", "!00000002", "!00000003"]
    assert json.loads(published[0][1])["status"] == "ok"


def test_coordinator_on_packet_ignored_cases():
    coord, timers = _make_coord()
    coord.start("!00000003")
    # mauvais portnum
    coord.on_packet({"decoded": {"portnum": "TEXT_MESSAGE_APP"}})
    # requestId absent
    coord.on_packet({"decoded": {"portnum": "TRACEROUTE_APP"}})
    # requestId inconnu
    coord.on_packet({"decoded": {"portnum": "TRACEROUTE_APP", "requestId": 999}})
    # requestId ok mais from != dest -> ignoré
    coord.on_packet({"from": 99, "to": 1, "decoded": {"portnum": "TRACEROUTE_APP", "requestId": 12345}})
    assert not timers[0].cancelled  # aucune corrélation -> pending intact


def test_coordinator_on_packet_bad_payload_is_error():
    store = FakeStore()
    coord, _ = _make_coord(store=store)
    coord.start("!00000003")
    coord.on_packet({
        "from": 3, "to": 1,
        "decoded": {"portnum": "TRACEROUTE_APP", "requestId": 12345, "payload": b"\xff\xff not proto"},
    })
    assert store.rows[0][0]["status"] == "error"


def test_coordinator_timeout_and_double():
    store = FakeStore()
    published = []
    coord, timers = _make_coord(store=store, publish=lambda t, p: published.append((t, p)))
    coord.start("!00000003", timeout_s=30)
    timers[0].fire()  # timeout
    assert store.rows[0][0]["status"] == "timeout"
    # 2e déclenchement (déjà résolu) -> no-op
    timers[0].fire()
    assert len(store.rows) == 1


def test_coordinator_emit_resilient(caplog):
    # store qui lève + publish qui lève : _emit ne propage jamais
    store = FakeStore(raise_on_write=True)

    def bad_publish(topic, payload):
        raise RuntimeError("broker down")

    coord, timers = _make_coord(store=store, publish=bad_publish)
    coord.start("!00000003")
    timers[0].fire()  # timeout -> _emit avec store+publish cassés
    # aucune exception remontée


def test_coordinator_emit_store_none():
    published = []
    coord, timers = _make_coord(store=None, publish=lambda t, p: published.append((t, p)))
    coord.start("!00000003")
    timers[0].fire()
    assert json.loads(published[0][1])["status"] == "timeout"


def test_coordinator_cancel_all():
    coord, timers = _make_coord()
    coord.start("!00000003")
    coord.cancel_all()
    assert timers[0].cancelled
