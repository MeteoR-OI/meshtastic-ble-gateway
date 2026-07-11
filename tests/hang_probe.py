# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sonde de terminaison, exécutée en SUBPROCESS par test_provision.

Reproduit le chemin exit-2 (commit OK, reconnexion post-reboot épuisée) AVEC un thread
**non-daemon** résiduel — comme les threads bleak/meshtastic réels. Sans le `os._exit`
de `provision.cli`, un arrêt normal joindrait ce thread et le process GÈLERAIT (bug
hw-test T114) ; avec, il sort en 2 aussitôt, JSON flushé. Le test parent l'exécute sous
un `timeout` : un hang ⇒ `TimeoutExpired` ⇒ échec.

Nommé `hang_probe.py` (pas `test_*.py`) pour ne pas être collecté comme test.
"""
import threading

from meshtastic.protobuf import channel_pb2, localonly_pb2

import mbg.provision as provision


class _Node:
    def __init__(self):
        self.moduleConfig = localonly_pb2.LocalModuleConfig()
        self.localConfig = localonly_pb2.LocalConfig()
        self.channels = [channel_pb2.Channel(index=0)]

    def beginSettingsTransaction(self):
        pass

    def writeConfig(self, name):
        pass

    def writeChannel(self, index):
        pass

    def commitSettingsTransaction(self):
        pass


class _Iface:
    def __init__(self):
        self.localNode = _Node()

    def getMyNodeInfo(self):
        return {"user": {"id": "!534bbea5", "longName": "N"}}

    def close(self):
        pass


def _main():
    # Thread NON-daemon qui ne meurt jamais : gèle tout arrêt normal de l'interpréteur.
    threading.Thread(target=threading.Event().wait, daemon=False).start()
    # Budget de reconnexion instantané (on teste la TERMINAISON, pas la patience).
    provision.REBOOT_WAIT = 0.0
    provision.RECONNECT_DELAY = 0.0
    provision.RECONNECT_MAX_DELAY = 0.0
    state = {"n": 0}

    def factory(mac):
        state["n"] += 1
        if state["n"] == 1:
            return _Iface()  # connexion initiale OK -> apply -> commit -> reboot
        raise TimeoutError("no peripheral")  # reconnexion post-reboot : budget épuisé

    provision.cli(
        ["--mac", "AA:BB:CC:DD:EE:FF", "--apply"],
        interface_factory=factory,
        sleep=lambda s: None,
    )


if __name__ == "__main__":
    _main()
