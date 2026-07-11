# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests de l'outil de provisionnement (CONTRACTS-onboarding §2).

La fake iface porte de VRAIS protobufs meshtastic (sémantique CopyFrom/Serialize
fidèle) ; le reboot post-commit est simulé par le factory (l'interface pré-reboot
meurt, la reconnexion relit l'état persisté — ou pas, pour le cas d'échec).
"""
import json
import os
import subprocess
import sys
import threading

import pytest
from meshtastic.protobuf import channel_pb2, localonly_pb2

from mbg import provision


class FakeNode:
    """localNode factice : vrais protobufs + journal des écritures admin."""

    def __init__(self, uplink=False):
        # mêmes types que meshtastic.node.Node (localonly = agrégats locaux)
        self.moduleConfig = localonly_pb2.LocalModuleConfig()
        self.localConfig = localonly_pb2.LocalConfig()
        ch = channel_pb2.Channel(index=0)
        ch.settings.uplink_enabled = uplink
        self.channels = [ch]
        self.writes = []

    def beginSettingsTransaction(self):
        self.writes.append("begin")

    def writeConfig(self, name):
        self.writes.append(f"write:{name}")

    def writeChannel(self, index):
        self.writes.append(f"channel:{index}")

    def commitSettingsTransaction(self):
        self.writes.append("commit")


def conformant_node(args, username="u", password="p"):
    """Node déjà à la config cible (aucune écriture attendue)."""
    node = FakeNode(uplink=True)
    mqtt = node.moduleConfig.mqtt
    mqtt.enabled = True
    mqtt.proxy_to_client_enabled = True
    mqtt.encryption_enabled = True
    mqtt.json_enabled = True
    mqtt.tls_enabled = False
    mqtt.map_reporting_enabled = True
    mqtt.address = provision.target_address(args.broker, args.port)
    mqtt.root = args.root
    mqtt.username = username
    mqtt.password = password
    mqtt.map_report_settings.publish_interval_secs = args.publish_interval
    mqtt.map_report_settings.position_precision = args.precision
    mqtt.map_report_settings.should_report_location = args.consent
    node.localConfig.position.position_broadcast_secs = args.broadcast_secs
    return node


class FakeIface:
    def __init__(self, node, info="default"):
        self.localNode = node
        self._info = (
            {"user": {"id": "!534bbea5", "longName": "974SJOLM8CIN_P5"}} if info == "default" else info
        )
        self.closed = False

    def getMyNodeInfo(self):
        return self._info

    def close(self):
        self.closed = True


class ImmediateThread:
    """Exécute target au start() — rend le fire-and-forget synchrone et observable."""

    instances = []

    def __init__(self, target=None, daemon=None):
        self._target = target
        self.daemon = daemon
        self.joined_with = "unset"
        ImmediateThread.instances.append(self)

    def start(self):
        self._target()

    def join(self, timeout=None):
        self.joined_with = timeout


class NeverRunsThread(ImmediateThread):
    """Le commit ne rend jamais la main (node qui reboote en cours d'appel)."""

    def start(self):
        pass


@pytest.fixture(autouse=True)
def _reset_threads():
    ImmediateThread.instances = []


def _args(*extra):
    return provision.build_parser().parse_args(["--mac", "AA:BB:CC:DD:EE:FF", *extra])


# --- parsing / helpers -------------------------------------------------------


def test_parse_bool():
    assert provision._parse_bool("true") is True
    assert provision._parse_bool("0") is False
    with pytest.raises(Exception):
        provision._parse_bool("peut-être")


def test_parser_defaults_match_contract():
    args = _args("--inspect")
    assert args.broker == "mqtt-mt.meteor-oi.re" and args.port == 1883
    assert args.root == "msh/EU_868" and args.precision == 15
    assert args.publish_interval == 3600 and args.consent is True
    assert args.broadcast_secs == 900 and args.fixed_position is False
    assert args.username is None and args.password is None


def test_target_address():
    assert provision.target_address("mqtt-mt.meteor-oi.re", 1883) == "mqtt-mt.meteor-oi.re"
    assert provision.target_address("h", 8883) == "h:8883"


# --- retry BLE ---------------------------------------------------------------


def test_connect_first_try_no_sleep():
    slept = []
    iface = provision.connect_with_retry("MAC", factory=lambda a: f"iface-{a}", sleep=slept.append)
    assert iface == "iface-MAC" and slept == []


def test_connect_retry_backoff_caps():
    calls = {"n": 0}
    slept = []

    def flaky(address):
        calls["n"] += 1
        if calls["n"] <= 4:
            raise TimeoutError("BLE timeout")  # 1er connect timeout fréquent (Phase 0)
        return "iface"

    iface = provision.connect_with_retry("MAC", factory=flaky, attempts=5, sleep=slept.append)
    assert iface == "iface"
    assert slept == [3.0, 6.0, 12.0, 15.0]  # backoff x2 plafonné à CONNECT_MAX_DELAY


def test_connect_exhausted_raises():
    def dead(address):
        raise TimeoutError("no peripheral")

    with pytest.raises(provision.ProvisionError, match="4 tentatives"):
        provision.connect_with_retry("MAC", factory=dead, sleep=lambda s: None)


# --- inspect -----------------------------------------------------------------


def test_inspect_outputs_contract_json():
    args = _args("--inspect")
    node = conformant_node(args)
    iface = FakeIface(node)
    code, out = provision.run(args, interface_factory=lambda a: iface,
                              sleep=lambda s: None, thread_factory=ImmediateThread)
    assert code == 0
    assert out["node_id"] == "!534bbea5" and out["node_name"] == "974SJOLM8CIN_P5"
    assert out["mqtt"]["address"] == "mqtt-mt.meteor-oi.re"
    assert out["mqtt"]["proxy_to_client"] is True and out["mqtt"]["tls"] is False
    assert out["mqtt"]["map_report"] == {
        "publish_interval_secs": 3600, "position_precision": 15, "should_report_location": True,
    }
    assert out["position"] == {"broadcast_secs": 900, "fixed": False}
    assert out["broker_matches"] is True and out["creds_present"] is True
    assert out["needs_register"] is False and out["applied"] is False
    assert node.writes == []  # lecture seule : AUCUNE écriture
    assert iface.closed is True  # close fire-and-forget exécuté


def test_inspect_needs_register_on_foreign_broker():
    args = _args("--inspect")
    node = conformant_node(args)
    node.moduleConfig.mqtt.address = "mqtt.meshtastic.org"
    code, out = provision.run(args, interface_factory=lambda a: FakeIface(node),
                              sleep=lambda s: None, thread_factory=ImmediateThread)
    assert code == 0
    assert out["broker_matches"] is False and out["needs_register"] is True


def test_inspect_needs_register_on_missing_creds():
    args = _args("--inspect")
    node = conformant_node(args, username="u", password="")  # password vide
    _, out = provision.run(args, interface_factory=lambda a: FakeIface(node),
                           sleep=lambda s: None, thread_factory=ImmediateThread)
    assert out["creds_present"] is False and out["needs_register"] is True
    node2 = conformant_node(args, username="", password="p")  # username vide (court-circuit)
    _, out2 = provision.run(args, interface_factory=lambda a: FakeIface(node2),
                            sleep=lambda s: None, thread_factory=ImmediateThread)
    assert out2["creds_present"] is False


def test_inspect_without_node_info():
    args = _args("--inspect")
    node = conformant_node(args)
    _, out = provision.run(args, interface_factory=lambda a: FakeIface(node, info=None),
                           sleep=lambda s: None, thread_factory=ImmediateThread)
    assert out["node_id"] is None and out["node_name"] is None


# --- apply -------------------------------------------------------------------


def test_apply_writes_one_transaction_and_verifies():
    args = _args("--apply", "--username", "mhar_x", "--password", "s3cret", "--fixed-position")
    node = FakeNode(uplink=False)  # node vierge : tout change
    ifaces = [FakeIface(node), FakeIface(node)]  # même node : la config a persisté au reboot
    slept = []
    code, out = provision.run(args, interface_factory=lambda a: ifaces.pop(0),
                              sleep=slept.append, thread_factory=ImmediateThread)
    assert code == 0 and out["applied"] is True
    # UNE transaction, toutes les sections modifiées, commit en dernier
    assert node.writes == ["begin", "write:mqtt", "write:position", "channel:0", "commit"]
    assert provision.REBOOT_WAIT in slept  # attente reboot avant reconnexion
    assert node.moduleConfig.mqtt.username == "mhar_x"
    assert node.moduleConfig.mqtt.password == "s3cret"
    assert node.localConfig.position.fixed_position is True
    assert node.channels[0].settings.uplink_enabled is True
    assert out["creds_present"] is True and out["needs_register"] is False


def test_apply_noop_when_already_conformant():
    args = _args("--apply")
    node = conformant_node(args)
    factory_calls = []

    def factory(address):
        factory_calls.append(address)
        return FakeIface(node)

    slept = []
    code, out = provision.run(args, interface_factory=factory,
                              sleep=slept.append, thread_factory=ImmediateThread)
    assert code == 0 and out["applied"] is True
    assert node.writes == []  # déjà conforme : AUCUNE écriture, pas de reboot
    assert len(factory_calls) == 1 and slept == []


def test_apply_only_channel_when_rest_conformant():
    args = _args("--apply")
    node = conformant_node(args)
    node.channels[0].settings.uplink_enabled = False
    code, _ = provision.run(args, interface_factory=lambda a: FakeIface(node),
                            sleep=lambda s: None, thread_factory=ImmediateThread)
    assert code == 0
    assert node.writes == ["begin", "channel:0", "commit"]  # ni mqtt ni position réécrits


def test_apply_only_mqtt_when_rest_conformant():
    args = _args("--apply")
    node = conformant_node(args)
    node.moduleConfig.mqtt.root = "msh/ANCIEN"
    code, _ = provision.run(args, interface_factory=lambda a: FakeIface(node),
                            sleep=lambda s: None, thread_factory=ImmediateThread)
    assert code == 0
    assert node.writes == ["begin", "write:mqtt", "commit"]


def test_apply_blank_creds_keep_node_creds():
    args = _args("--apply")  # ni --username ni --password (§7.3 : blancs -> lus du node)
    node = conformant_node(args, username="du_node", password="garde")
    node.moduleConfig.mqtt.map_reporting_enabled = False  # force une écriture mqtt
    code, out = provision.run(args, interface_factory=lambda a: FakeIface(node),
                              sleep=lambda s: None, thread_factory=ImmediateThread)
    assert code == 0 and out["applied"] is True
    assert node.moduleConfig.mqtt.username == "du_node"  # PAS écrasés
    assert out["mqtt"]["username"] == "du_node" and out["mqtt"]["password"] == "garde"


def test_apply_verify_failure_exits_1():
    args = _args("--apply")
    stale = FakeNode(uplink=False)  # node vierge avant apply
    lost = FakeNode(uplink=False)  # après reboot : la config n'a PAS persisté
    ifaces = [FakeIface(stale), FakeIface(lost)]
    code, out = provision.run(args, interface_factory=lambda a: ifaces.pop(0),
                              sleep=lambda s: None, thread_factory=ImmediateThread)
    assert code == 1 and out["applied"] is False


def test_apply_committed_but_unverified_exit_2():
    # Le commit part, le node reboote… et ne ré-annonce pas dans le budget de reconnexion
    # (finding hw-test T114) : exit 2 + champs dédiés, PAS l'enveloppe {"error"} — sinon
    # l'installateur conclurait à tort à un échec alors que le write est appliqué.
    args = _args("--apply")
    node = FakeNode(uplink=False)
    calls = {"n": 0}

    def factory(address):
        calls["n"] += 1
        if calls["n"] == 1:
            return FakeIface(node)  # connexion initiale OK
        raise TimeoutError("no peripheral")  # le node ne ré-annonce jamais

    slept = []
    code, out = provision.run(args, interface_factory=factory,
                              sleep=slept.append, thread_factory=ImmediateThread)
    assert code == 2
    assert out["applied"] is None and out["committed"] is True and out["verified"] is False
    assert out["warning"] == provision.COMMITTED_UNVERIFIED_WARNING
    assert out["node_id"] == "!534bbea5"  # identité capturée AVANT le reboot
    assert "error" not in out
    # budget de reconnexion PATIENT et séparé : 10 tentatives, ≥150 s de sleeps + REBOOT_WAIT ~2 min
    assert calls["n"] == 1 + provision.RECONNECT_ATTEMPTS
    assert slept[0] == provision.REBOOT_WAIT == 120.0
    assert sum(slept[1:]) >= 150


def test_apply_commit_never_returns_still_reconnects():
    # Le node reboote PENDANT le commit : l'appel ne rend jamais la main (Phase 0)
    args = _args("--apply")
    node = FakeNode(uplink=False)
    ifaces = [FakeIface(node), FakeIface(node)]
    code, out = provision.run(args, interface_factory=lambda a: ifaces.pop(0),
                              sleep=lambda s: None, thread_factory=NeverRunsThread)
    assert code == 0 and out["applied"] is True  # writeConfig a persisté ; commit fire-and-forget
    assert "commit" not in node.writes  # le thread n'a jamais rendu la main
    committer = ImmediateThread.instances[0]
    assert committer.daemon is True and committer.joined_with == provision.COMMIT_JOIN_TIMEOUT


def test_matches_target_checks_optional_fields():
    args = _args("--apply", "--username", "u", "--password", "p", "--fixed-position")
    node = conformant_node(args, username="u", password="p")
    node.localConfig.position.fixed_position = True
    assert provision.matches_target(node, args) is True
    node.moduleConfig.mqtt.password = "autre"
    assert provision.matches_target(node, args) is False


# --- main (entrée CLI) -------------------------------------------------------


def test_main_inspect_prints_json():
    args_node = conformant_node(_args("--inspect"))
    printed = []
    code = provision.main(
        ["--mac", "AA:BB:CC:DD:EE:FF", "--inspect"],
        interface_factory=lambda a: FakeIface(args_node),
        sleep=lambda s: None, thread_factory=ImmediateThread, out=printed.append,
    )
    assert code == 0
    payload = json.loads(printed[0])
    assert payload["applied"] is False and payload["broker_matches"] is True


def test_main_connection_failure_json_error_exit_1():
    def dead(address):
        raise TimeoutError("no peripheral")

    printed = []
    code = provision.main(
        ["--mac", "AA:BB:CC:DD:EE:FF", "--inspect"],
        interface_factory=dead, sleep=lambda s: None,
        thread_factory=ImmediateThread, out=printed.append,
    )
    assert code == 1
    assert "connexion BLE impossible" in json.loads(printed[0])["error"]


def test_main_unexpected_error_json_error_exit_1():
    class Broken:
        localNode = None

        def getMyNodeInfo(self):
            raise RuntimeError("boum")

    printed = []
    code = provision.main(
        ["--mac", "AA:BB:CC:DD:EE:FF", "--inspect"],
        interface_factory=lambda a: Broken(), sleep=lambda s: None,
        thread_factory=ImmediateThread, out=printed.append,
    )
    assert code == 1
    assert "RuntimeError" in json.loads(printed[0])["error"]


def test_main_apply_verify_failure_exit_code_passthrough():
    stale, lost = FakeNode(), FakeNode()
    ifaces = [FakeIface(stale), FakeIface(lost)]
    printed = []
    code = provision.main(
        ["--mac", "AA:BB:CC:DD:EE:FF", "--apply"],
        interface_factory=lambda a: ifaces.pop(0), sleep=lambda s: None,
        thread_factory=ImmediateThread, out=printed.append,
    )
    assert code == 1
    assert json.loads(printed[0])["applied"] is False


# --- terminaison dure (cli / os._exit) — finding hw-test T114 2026-07-11 ------


def test_cli_flushes_json_then_terminates_hard():
    # Un thread NON-daemon résiduel gèlerait un arrêt normal (raise SystemExit joindrait
    # le thread). cli() DOIT atteindre terminate() malgré lui (en prod = os._exit).
    ev = threading.Event()
    residual = threading.Thread(target=ev.wait, daemon=False)
    residual.start()
    try:
        node = conformant_node(_args("--inspect"))
        printed, seen = [], {}
        provision.cli(
            ["--mac", "AA:BB:CC:DD:EE:FF", "--inspect"],
            terminate=lambda c: seen.setdefault("code", c),
            interface_factory=lambda a: FakeIface(node),
            sleep=lambda s: None, thread_factory=ImmediateThread, out=printed.append,
        )
        assert seen["code"] == 0
        assert json.loads(printed[0])["applied"] is False  # JSON émis AVANT terminate
    finally:
        ev.set()
        residual.join()


def test_cli_terminates_under_residual_nondaemon_thread():
    # Terminaison EFFECTIVE (pas seulement la valeur de retour) : un vrai subprocess
    # reproduit le chemin exit-2 AVEC un thread non-daemon vivant. Sans os._exit il
    # hangerait -> le timeout ci-dessous lèverait TimeoutExpired (échec du test).
    repo = os.path.dirname(os.path.dirname(__file__))
    probe = os.path.join(repo, "tests", "hang_probe.py")
    env = dict(os.environ, PYTHONPATH=os.path.join(repo, "src"))
    proc = subprocess.run(
        [sys.executable, probe], env=env, capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 2, proc.stderr
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload == {
        "node_id": "!534bbea5", "node_name": "N",
        "applied": None, "committed": True, "verified": False,
        "warning": provision.COMMITTED_UNVERIFIED_WARNING,
    }
