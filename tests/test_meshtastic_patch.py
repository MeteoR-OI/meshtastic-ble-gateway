# SPDX-License-Identifier: AGPL-3.0-or-later
import sys
import types

from mbg.meshtastic_patch import apply_meshtastic_patches


def make_mi_class():
    """Classe fraîche imitant MeshInterface (isolation entre tests)."""

    class MI:
        def __init__(self):
            self.sent = []

        def _sendDisconnect(self):
            self.sent.append("disconnect")  # comportement d'origine (écriture radio)

        def _sendToRadio(self, m):
            self.sent.append(m)

    return MI


def test_neutralizes_send_disconnect():
    MI = make_mi_class()
    assert apply_meshtastic_patches(MI) is True
    inst = MI()
    assert inst._sendDisconnect() is None  # no-op
    assert inst.sent == []  # n'écrit plus rien au radio


def test_idempotent():
    MI = make_mi_class()
    assert apply_meshtastic_patches(MI) is True
    patched = MI._sendDisconnect
    assert apply_meshtastic_patches(MI) is True  # 2e appel : rien à faire
    assert MI._sendDisconnect is patched  # pas re-patché


def test_skips_when_method_missing():
    class Bare:
        pass

    assert apply_meshtastic_patches(Bare) is False


def test_default_import_success(monkeypatch):
    MI = make_mi_class()
    fake_mod = types.ModuleType("meshtastic.mesh_interface")
    fake_mod.MeshInterface = MI
    monkeypatch.setitem(sys.modules, "meshtastic.mesh_interface", fake_mod)
    assert apply_meshtastic_patches() is True  # importe MeshInterface depuis le module injecté
    assert MI()._sendDisconnect() is None


def test_default_import_failure(monkeypatch):
    monkeypatch.setitem(sys.modules, "meshtastic.mesh_interface", None)
    assert apply_meshtastic_patches() is False  # import casse -> no-op silencieux
