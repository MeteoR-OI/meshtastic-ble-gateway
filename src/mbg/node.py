# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Mickaël Hoareau — MeteoR-OI
"""Adaptateur node : connexion BLE + routage des messages Client Proxy.

meshtastic-python publie les messages Client Proxy entrants sur le topic pubsub
`meshtastic.mqttclientproxymessage` avec les kwargs (proxymessage, interface).
On s'y abonne et on route vers un callback. Toutes les dépendances externes
(BLEInterface, pub.subscribe/unsubscribe) sont injectables pour les tests.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

from . import metrics
from . import traceroute as traceroute_mod
from .control import execute_command

log = logging.getLogger("mbg.node")

PROXY_TOPIC = "meshtastic.mqttclientproxymessage"
CONNECTION_LOST_TOPIC = "meshtastic.connection.lost"
RECEIVE_TOPIC = "meshtastic.receive"  # tous les paquets décodés reçus
ROUTING_PORTNUM = "ROUTING_APP"  # portnum d'un accusé (ACK/NAK)
ACK_TIMEOUT = 60.0  # au-delà, on logue un « timeout » d'accusé radio (want_ack)

OnProxy = Callable[[object], None]
OnLost = Callable[[], None]


def _hop_bucket(packet: dict) -> int:
    """Nombre de sauts traversés par un paquet reçu, pour l'histogramme `/hops`.

    `hopStart`/`hopLimit` sont camelCase au niveau **top** du dict décodé (sœurs de `fromId`),
    **pas** dans `decoded`. meshtastic sérialise via `MessageToDict` (mesh_interface.py), qui
    **omet les champs à zéro** : un `hopLimit` absent vaut donc **0** (paquet ayant épuisé son
    budget de sauts), ce n'est **PAS** un Inconnu. Seul `hopStart` absent/`0` (firmware ancien
    qui ne le peuple pas) rend le saut indéterminable → bucket **`-1` = Inconnu** (contrat A,
    amendé 2026-07-17 : sans cet amendement, tout paquet multi-hop à budget épuisé partait en
    `-1` — fraction massive sur mesh chargé).

    Chemin RADIO : **ne lève JAMAIS**. `bool` étant une sous-classe d'`int`, on l'exclut
    explicitement (un `True`/`False` exotique ne doit pas être arithmétisé).
    """
    hs = packet.get("hopStart")
    if isinstance(hs, int) and not isinstance(hs, bool) and hs >= 1:
        hl = packet.get("hopLimit")
        if not (isinstance(hl, int) and not isinstance(hl, bool)):
            hl = 0  # champ omis par MessageToDict -> 0 (budget épuisé), pas Inconnu
        hops = hs - hl
        if 0 <= hops <= 7:
            return hops
    return -1


def _ack_status(packet: Any) -> str:
    """Interprète un paquet ROUTING en accusé radio lisible."""
    try:
        reason = packet["decoded"]["routing"]["errorReason"]
    except (KeyError, TypeError):
        return "reçu (ACK)"  # pas d'erreur de routage -> livré
    if reason in (0, "NONE", None):
        return "reçu (ACK)"
    return f"échec ({reason})"


def default_interface_factory(address: str):
    """Ouvre une connexion BLE réelle au node (import paresseux de meshtastic)."""
    from meshtastic.ble_interface import BLEInterface

    return BLEInterface(address)


def default_liveness(iface: object) -> bool:
    """Vrai si le lien BLE est encore up, d'après l'état D-Bus BlueZ (via bleak).

    C'est LE signal qui détecte la coupure silencieuse : meshtastic ne lève ni
    exception ni `connection.lost`, mais BlueZ, lui, sait que `Connected: no`.
    Fail-open (True) si l'introspection échoue, pour ne jamais reconnecter à tort.
    """
    client = getattr(iface, "client", None)
    bleak_client = getattr(client, "bleak_client", None)
    connected = getattr(bleak_client, "is_connected", None)
    if connected is None:
        return True
    return bool(connected)


def default_subscribe(handler: Callable, topic: str) -> None:
    from pubsub import pub

    pub.subscribe(handler, topic)


def default_unsubscribe(handler: Callable, topic: str) -> None:
    from pubsub import pub

    pub.unsubscribe(handler, topic)


class MeshtasticNodeLink:
    """Lien BLE vers le node ; délivre chaque ProxyMessage à `on_proxy`."""

    def __init__(
        self,
        address: str,
        on_proxy: OnProxy,
        on_lost: Optional[OnLost] = None,
        *,
        interface_factory: Callable[[str], object] = default_interface_factory,
        subscribe: Callable[[Callable, str], None] = default_subscribe,
        unsubscribe: Callable[[Callable, str], None] = default_unsubscribe,
        liveness: Callable[[object], bool] = default_liveness,
        executor: Callable[[object, dict], dict] = execute_command,
        timer_factory: Callable[[float, Callable[[], None]], Any] = threading.Timer,
    ) -> None:
        self._address = address
        self._on_proxy = on_proxy
        self._on_lost = on_lost
        self._interface_factory = interface_factory
        self._subscribe = subscribe
        self._unsubscribe = unsubscribe
        self._liveness = liveness
        self._executor = executor
        self._timer_factory = timer_factory
        self._iface = None
        self._pending_acks = {}  # packet_id -> (label, timer) pour want_ack
        self._ack_lock = threading.Lock()
        self._packet_counts = {}  # node_id -> nb de paquets reçus depuis le dernier flush
        self._packet_hop_counts = {}  # nb de sauts (0..7, -1 Inconnu, -2 LOCAL) -> nb de paquets (/hops)
        self._local_id = None  # id !hex du nœud LOCAL, mis en cache (bucket -2) — cf. _local_node_id
        self._packet_lock = threading.Lock()  # le comptage (par nœud ET par saut) vit dans le thread pubsub
        self._traceroute = None  # TracerouteCoordinator (attaché par la session si activé)

    def _handler(self, proxymessage=None, interface=None) -> None:
        """Signature attendue par le pubsub meshtastic (kwargs nommés)."""
        self._on_proxy(proxymessage)

    def _handler_lost(self, interface=None) -> None:
        """Perte du lien BLE signalée par meshtastic-python."""
        log.warning("lien BLE perdu (node %s)", self._address)
        if self._on_lost is not None:
            self._on_lost()

    def is_alive(self) -> bool:
        """Sonde de vivacité du lien BLE (sans I/O). False si non ouvert ou lien mort."""
        if self._iface is None:
            return False
        return self._liveness(self._iface)

    def read_metrics(self, *, now: Optional[float] = None, active_window: Optional[float] = None) -> dict:
        """Relève les métriques du node (device, identité, statut MQTT, position, voisins) — sans I/O radio.

        `now`/`active_window` (V0.8.2) : ne comptent que les voisins ACTIFS (entendus depuis
        `now - active_window`) — le filtre s'applique à l'extraction, donc `count`/`best_snr`,
        les voisins stockés (donc `distinct_*`) ET `max_distance_km` excluent les périmés.
        """
        info = self._iface.getMyNodeInfo() or {}
        # Statut MQTT (onboarding) : lu de la config LOCALE du node (pas d'I/O radio).
        module_config = getattr(getattr(self._iface, "localNode", None), "moduleConfig", None)
        mqtt = metrics.mqtt_status(getattr(module_config, "mqtt", None))
        node = dict(metrics.node_metrics(info), **metrics.node_identity(info), **mqtt)
        nodes_by_num = getattr(self._iface, "nodesByNum", None) or {}
        pos = metrics.position(info)
        nbrs = metrics.neighbors(nodes_by_num, info.get("num"), now=now, active_window=active_window)
        # node_metrics.max_distance_km = série temporelle du DIRECT (0-hop) sur données live
        # (continuité v0.8.1 / /history). Les distances /metrics restant-surviving (direct +
        # multi-hop) sont recalculées par store.latest() depuis le registre persistant.
        direct = [n for n in nbrs if n["hops_away"] == 0]
        node["max_distance_km"] = metrics.max_distance_km(pos, direct)
        return {
            "node": node,
            "position": pos,
            "neighbors": nbrs,
            # Noms affichables de TOUTE la NodeDB (bloc `nodes` de /packets) : sur-ensemble
            # volontaire de `neighbors` (ni filtre hops/activité, ni exclusion du node local).
            "node_names": metrics.node_names(nodes_by_num),
        }

    def send(self, command: dict) -> dict:
        """Exécute une commande downlink (write BLE). Voir `control`. Suit l'ACK si demandé.

        `type == "traceroute"` est routé vers le coordinateur (émission + corrélation async) au
        lieu de `control` : la réponse arrive plus tard dans la boucle de réception (voir traceroute)."""
        if command.get("type") == "traceroute":
            return self._send_traceroute_command(command)
        result = self._executor(self._iface, command)
        packet_id = result.get("packet_id")
        if command.get("want_ack") and packet_id is not None:
            self._track_ack(packet_id, f"canal={command.get('channel')}")
        return result

    def _send_traceroute_command(self, command: dict) -> dict:
        if self._traceroute is None:
            return {"ok": False, "error": "traceroute non activé"}
        try:
            return self._traceroute.start(
                command.get("dest"),
                hop_limit=command.get("hop_limit", 7),
                channel_index=command.get("channel_index", 0),
                timeout_s=command.get("timeout_s", 30.0),
                source=command.get("source", "api"),
            )
        except ValueError as exc:  # dest invalide (déjà validé côté API, ceinture+bretelles)
            return {"ok": False, "error": str(exc)}

    # --- Traceroute : frontières exposées au coordinateur (injecté par la session) ---
    def attach_traceroute(self, coordinator) -> None:
        """Branche un `TracerouteCoordinator` (l'`on_packet` sera nourri par `_handler_receive`)."""
        self._traceroute = coordinator

    def send_traceroute(self, dest_num: int, hop_limit: int, channel_index: int):
        """Émet un paquet TRACEROUTE_APP (RouteDiscovery vide, wantResponse) → renvoie son id."""
        from meshtastic.protobuf import mesh_pb2, portnums_pb2

        packet = self._iface.sendData(
            mesh_pb2.RouteDiscovery(),
            destinationId=dest_num,
            portNum=portnums_pb2.PortNum.TRACEROUTE_APP,
            wantResponse=True,
            channelIndex=channel_index,
            hopLimit=hop_limit,
        )
        return getattr(packet, "id", None)

    def nodes(self) -> dict:
        return getattr(self._iface, "nodesByNum", None) or {}

    def my_num(self):
        return (self._iface.getMyNodeInfo() or {}).get("num")

    def node_id_of(self, num: int) -> str:
        node = self.nodes().get(num) or {}
        return (node.get("user") or {}).get("id") or traceroute_mod.hexid(num)

    def gateway_id(self):
        info = self._iface.getMyNodeInfo() or {}
        gid = (info.get("user") or {}).get("id")
        if gid:
            return gid
        num = info.get("num")
        return traceroute_mod.hexid(num) if num is not None else None

    def _track_ack(self, packet_id, label: str) -> None:
        """Arme l'attente d'un ACK radio (ROUTING_APP entrant) + un timeout de repli."""
        timer = self._timer_factory(ACK_TIMEOUT, lambda: self._ack_timeout(packet_id, label))
        timer.daemon = True
        with self._ack_lock:
            self._pending_acks[packet_id] = (label, timer)
        timer.start()

    def _ack_timeout(self, packet_id, label: str) -> None:
        with self._ack_lock:
            present = self._pending_acks.pop(packet_id, None)
        if present is not None:
            log.info("[downlink] ACK %s → timeout (aucun accusé reçu)", label)

    def _count_packet(self, packet: dict) -> None:
        """Compte un paquet entrant pour son nœud émetteur (histogramme `/packets`).

        Chemin RADIO (thread pubsub) : un `dict[node_id] += 1` sous verrou, aucune I/O, et
        surtout **ne lève JAMAIS** — un paquet exotique ne doit pas casser la réception. Compté
        AVANT le filtre portnum : le contrat compte tous portnums confondus.

        Le paquet est ici un dict DÉJÀ décodé par meshtastic (≠ le ServiceEnvelope protobuf de
        `proxy.py`) : `fromId` est déjà `!hex`, avec repli sur `from` (int) comme `metrics.py`.

        **Le nœud LOCAL (la passerelle) est compté comme les autres** — décision produit
        explicite (arbitrage 2026-07-16, validée sur banc) : *il émet, donc il compte*.
        ⚠️ Asymétrie ASSUMÉE avec `metrics.neighbors()`, qui EXCLUT le nœud local
        (`if num == my_num: continue`) : l'histogramme montre donc N+1 émetteurs là où le
        voisinage compte N voisins. C'est voulu — « voisins » répond à *qui est autour de moi*,
        l'histogramme à *qui émet*. **Ne pas « corriger » cette différence** : ce n'est pas un
        bug, et la légende cliquable du chart permet de retirer un nœud local bavard.

        Côté **/hops**, le local va dans un bucket DÉDIÉ `-2` (voir plus bas) : ses paquets locaux
        n'ont ~jamais de `hopStart` (le champ n'est peuplé qu'à la transmission mesh, pas sur l'écho
        local) → sans ce bucket ils noieraient l'Inconnu `-1`, qui doit rester « paquet DISTANT au
        saut indéterminé » (finding live PAM289 : ~92 % du local sans `hopStart`).
        """
        try:
            node_id = packet.get("fromId")
            if not node_id:
                num = packet.get("from")
                if num is None:
                    return  # émetteur inconnu : non attribuable -> ne pas inventer de nœud
                node_id = "!%08x" % (num & 0xFFFFFFFF)
        except Exception:  # noqa: BLE001 — paquet malformé : on l'ignore, jamais d'exception ici
            return
        # Saut du paquet (histogramme /hops), sur la MÊME population que /packets — d'où l'incrément
        # sous le même verrou, dans le même bloc : un paquet compté par nœud l'est aussi par saut
        # (invariant Σ count(hops, tous buckets dont -1 ET -2) == total /packets). Calculé hors verrou.
        # Le bucket LOCAL -2 est PRIORITAIRE sur le calcul du saut : un paquet local avec un hopStart
        # exotique reste -2 (c'est l'émetteur qui tranche, pas le champ).
        hops = -2 if node_id == self._local_node_id() else _hop_bucket(packet)
        with self._packet_lock:
            self._packet_counts[node_id] = self._packet_counts.get(node_id, 0) + 1
            self._packet_hop_counts[hops] = self._packet_hop_counts.get(hops, 0) + 1

    def _local_node_id(self):
        """Id `!hex` du nœud LOCAL (passerelle), mis en cache — clé du bucket `-2` de `/hops`.

        Source = `getMyNodeInfo()['num']` (la MÊME que `metrics.py` `my_num`, pas une réinvention),
        formaté comme le repli du compteur par nœud (`!%08x`) → comparable au `node_id` dérivé.
        `getMyNodeInfo()` est une lecture du cache NodeDB (dict), **aucune I/O BLE** — sûr dans le
        thread pubsub. Résolu une seule fois (dès que l'iface le connaît), puis mémoïsé ; `None` tant
        qu'inconnu (aucun paquet ne matche → comportement d'avant l'ajout du bucket). Ne lève jamais.
        """
        if self._local_id is None and self._iface is not None:
            try:
                num = (self._iface.getMyNodeInfo() or {}).get("num")
            except Exception:  # noqa: BLE001 — jamais d'exception dans le chemin radio
                num = None
            if num is not None:
                self._local_id = "!%08x" % (num & 0xFFFFFFFF)
        return self._local_id

    def drain_packet_counts(self) -> dict:
        """Vide le compteur et renvoie `{node_id: count}` (RAM seule, aucune I/O BLE).

        Sûr sur lien MORT — c'est ce qui autorise le flush de fin de session (voir `session`),
        sans lequel toute session plus courte que `monitor_interval` perdrait ses comptages.
        """
        with self._packet_lock:
            counts = self._packet_counts
            self._packet_counts = {}
        return counts

    def drain_packet_hop_counts(self) -> dict:
        """Vide le compteur par saut et renvoie `{hops: count}` (RAM seule, aucune I/O BLE).

        Sûr sur lien MORT (même raison que `drain_packet_counts`) : c'est ce qui autorise le
        flush de fin de session, sans lequel une session plus courte que `monitor_interval`
        perdrait ses comptages par saut.
        """
        with self._packet_lock:
            counts = self._packet_hop_counts
            self._packet_hop_counts = {}
        return counts

    def _handler_receive(self, packet=None, interface=None) -> None:
        """Compte le paquet, puis : ROUTING_APP -> accusé radio (want_ack) ; TRACEROUTE_APP ->
        corrélation traceroute."""
        self._count_packet(packet or {})
        if self._traceroute is not None:
            self._traceroute.on_packet(packet or {})
        decoded = (packet or {}).get("decoded") or {}
        if decoded.get("portnum") != ROUTING_PORTNUM:
            return
        with self._ack_lock:
            entry = self._pending_acks.pop(decoded.get("requestId"), None)
        if entry is None:
            return
        label, timer = entry
        timer.cancel()
        log.info("[downlink] ACK %s → %s", label, _ack_status(packet))

    def open(self) -> None:
        self._subscribe(self._handler, PROXY_TOPIC)
        self._subscribe(self._handler_lost, CONNECTION_LOST_TOPIC)
        self._subscribe(self._handler_receive, RECEIVE_TOPIC)
        self._iface = self._interface_factory(self._address)
        log.info("node connecté (BLE %s)", self._address)

    def close(self) -> None:
        self._unsubscribe(self._handler, PROXY_TOPIC)
        self._unsubscribe(self._handler_lost, CONNECTION_LOST_TOPIC)
        self._unsubscribe(self._handler_receive, RECEIVE_TOPIC)
        with self._ack_lock:
            for _label, timer in self._pending_acks.values():
                timer.cancel()
            self._pending_acks.clear()
        if self._traceroute is not None:
            self._traceroute.cancel_all()
        if self._iface is not None:
            self._iface.close()
            self._iface = None
