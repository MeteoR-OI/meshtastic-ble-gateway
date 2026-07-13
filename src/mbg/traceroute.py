# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Mickaël Hoareau — MeteoR-OI
"""Traceroute Meshtastic : émission + corrélation asynchrone de la réponse.

Exécuté DANS le worker (il détient l'`iface` vivante et la boucle de réception) — donc
**aucune 2ᵉ connexion BLE**, aucune coupure de la passerelle. On envoie un paquet
`TRACEROUTE_APP` (`RouteDiscovery` vide, `want_response=True`) via `sendData` — qui retourne
le paquet émis, donc son `id` → clé de corrélation. La réponse (paquet `TRACEROUTE_APP`
entrant) arrive dans la boucle `meshtastic.receive` déjà branchée (`node.py`) ; on la corrèle
par `requestId == id` **et** `from == dest`, on parse la route, puis on publie MQTT + on écrit
SQLite (et un timer de repli produit un statut `timeout` si rien n'arrive).

Le mode bloquant (`wait:true`) de l'endpoint N'attend PAS ici (ça figerait la boucle de poll
du worker → plus de heartbeat → SIGKILL) : l'API (côté superviseur) relit la ligne SQLite via
le mode WAL. Tout le crypto/relais reste opaque ailleurs ; ici on ne lit que des `RouteDiscovery`
en clair (portnum de service, non chiffré de bout en bout par le firmware).

Fonctions pures (`normalize_dest`, `decode_snr`, `decode_route`, `build_result`) testables sans
matériel ; `TracerouteCoordinator` a toutes ses frontières (envoi/publish/store/horloge/timer)
injectées.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger("mbg.traceroute")

TRACEROUTE_PORTNUM = "TRACEROUTE_APP"
UNKNOWN_SNR = -128  # sentinelle firmware "SNR inconnu" → None
MAX_NODE_NUM = 0xFFFFFFFF


def hexid(num: int) -> str:
    """Num de node → identifiant `!hex` canonique (8 chiffres)."""
    return "!%08x" % (num & MAX_NODE_NUM)


def normalize_dest(dest: Any) -> Tuple[int, str]:
    """Normalise une cible (`"!hex"`, `"hex"`, ou num `int`) → `(num, "!hex")`.

    Un `int` est un numéro de node ; une chaîne est de l'hexadécimal (préfixe `!` optionnel).
    Lève `ValueError` si invalide (hex non parsable, hors 32 bits, type inattendu, broadcast).
    """
    if isinstance(dest, bool):  # bool est un int en Python — refuser explicitement
        raise ValueError("dest invalide")
    if isinstance(dest, int):
        num = dest
    elif isinstance(dest, str):
        raw = dest.strip()
        if raw.startswith("!"):
            raw = raw[1:]
        if not raw:
            raise ValueError("dest vide")
        try:
            num = int(raw, 16)
        except ValueError:
            raise ValueError("dest invalide (hex attendu): %s" % dest) from None
    else:
        raise ValueError("dest invalide (type %s)" % type(dest).__name__)
    if num < 0 or num > MAX_NODE_NUM:
        raise ValueError("dest hors plage 32 bits: %s" % dest)
    if num in (0, MAX_NODE_NUM):  # 0 / ^all (broadcast) : un traceroute doit viser un node précis
        raise ValueError("dest doit être un node précis (ni 0 ni broadcast)")
    return num, hexid(num)


def decode_snr(value: int) -> Optional[float]:
    """SNR firmware (entier = dB×4, sentinelle -128 = inconnu) → dB flottant ou None."""
    if value == UNKNOWN_SNR:
        return None
    return value / 4.0


def _align_snr(snr_values: List[int], expected: int) -> List[Optional[float]]:
    """Aligne la liste SNR sur `expected` liens ; si l'alignement est faux → tout inconnu.

    Le firmware renseigne un SNR de plus que le nb de relais (le destinataire ajoute le sien) ;
    si le compte ne correspond pas (firmware distant partiel), on n'invente rien → None partout.
    """
    if len(snr_values) == expected:
        return [decode_snr(v) for v in snr_values]
    return [None] * expected


def _leg(nodes: List[int], snrs: List[Optional[float]], id_of: Callable[[int], str]) -> List[Dict[str, Any]]:
    return [{"node": id_of(n), "snr": s} for n, s in zip(nodes, snrs)]


def decode_route(
    route: List[int],
    snr_towards: List[int],
    route_back: List[int],
    snr_back: List[int],
    origin_num: int,
    dest_num: int,
    id_of: Callable[[int], str],
) -> Dict[str, Any]:
    """Traduit un `RouteDiscovery` (listes brutes) en chemins lisibles (aller + retour).

    Chemin aller complet = `[origin, *route, dest]` ; `snr_towards` compte un SNR par lien
    APRÈS l'origine (`[*route, dest]`), donc l'origine porte `snr=None`. Idem retour
    `[dest, *route_back, origin]` avec `snr_back`. `route_back` n'est présent que si le firmware
    distant l'a renseigné (`snr_back` non vide). `hops_* = nb de liens = nb de nœuds - 1`.
    """
    fwd_nodes = [origin_num] + list(route) + [dest_num]
    fwd_snr = [None] + _align_snr(list(snr_towards), len(route) + 1)
    result: Dict[str, Any] = {
        "route_to": _leg(fwd_nodes, fwd_snr, id_of),
        "hops_to": len(route) + 1,
        "route_back": None,
        "hops_back": None,
    }
    if snr_back:  # le firmware distant a renseigné le chemin retour
        bwd_nodes = [dest_num] + list(route_back) + [origin_num]
        bwd_snr = [None] + _align_snr(list(snr_back), len(route_back) + 1)
        result["route_back"] = _leg(bwd_nodes, bwd_snr, id_of)
        result["hops_back"] = len(route_back) + 1
    return result


def _iso(ts: Optional[float]) -> Optional[str]:
    """Epoch → ISO-8601 UTC (`…Z`). None → None. Déterministe (testable sans horloge réelle)."""
    if ts is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def build_result(
    *,
    status: str,
    gateway_node: Optional[str],
    dest: str,
    request_id: int,
    hop_limit: int,
    sent_ts: float,
    recv_ts: Optional[float] = None,
    route: Optional[Dict[str, Any]] = None,
    source: str = "api",
    error: Optional[str] = None,
) -> Dict[str, Any]:
    """Construit le dict résultat (format MQTT + `wait:true` + SQLite). Pur."""
    route = route or {}
    rtt_ms = None
    if recv_ts is not None:
        rtt_ms = int(round((recv_ts - sent_ts) * 1000))
    result = {
        "type": "traceroute",
        "gateway_node": gateway_node,
        "dest": dest,
        "request_id": request_id,
        "status": status,
        "sent_ts": _iso(sent_ts),
        "recv_ts": _iso(recv_ts),
        "rtt_ms": rtt_ms,
        "hop_limit": hop_limit,
        "hops_to": route.get("hops_to"),
        "hops_back": route.get("hops_back"),
        "route_to": route.get("route_to"),
        "route_back": route.get("route_back"),
        "source": source,
    }
    if error is not None:
        result["error"] = error
    return result


class _Pending:
    """Une requête traceroute en attente de réponse (protégée par le lock du coordinateur)."""

    __slots__ = ("dest_num", "dest_id", "sent_ts", "hop_limit", "source", "timer")

    def __init__(self, dest_num, dest_id, sent_ts, hop_limit, source, timer) -> None:
        self.dest_num = dest_num
        self.dest_id = dest_id
        self.sent_ts = sent_ts
        self.hop_limit = hop_limit
        self.source = source
        self.timer = timer


class TracerouteCoordinator:
    """Émet un traceroute et corrèle sa réponse asynchrone (thread-safe, ne bloque jamais).

    Frontières injectées : `send_fn(dest_num, hop_limit, channel_index) -> packet_id` (write BLE),
    `publish_fn(topic, payload_bytes)` (MQTT), `store` (SQLite `record_traceroute`),
    `id_of(num) -> "!hex"` (mapping NodeDB), `gateway_id_fn() -> "!hex"|None` (identité locale).
    """

    def __init__(
        self,
        *,
        send_fn: Callable[[int, int, int], int],
        publish_fn: Callable[[str, bytes], None],
        store,
        id_of: Callable[[int], str],
        gateway_id_fn: Callable[[], Optional[str]],
        topic: str,
        clock: Callable[[], float] = time.time,
        timer_factory: Callable[[float, Callable[[], None]], Any] = threading.Timer,
    ) -> None:
        self._send_fn = send_fn
        self._publish_fn = publish_fn
        self._store = store
        self._id_of = id_of
        self._gateway_id_fn = gateway_id_fn
        self._topic = topic
        self._clock = clock
        self._timer_factory = timer_factory
        self._pending: Dict[int, _Pending] = {}
        self._lock = threading.Lock()

    def start(
        self,
        dest: Any,
        *,
        hop_limit: int = 7,
        channel_index: int = 0,
        timeout_s: float = 30.0,
        source: str = "api",
    ) -> Dict[str, Any]:
        """Émet un traceroute vers `dest`. Renvoie `{ok, request_id, dest}` (ou `{ok:False, error}`).

        Ne bloque pas : le résultat complet arrive plus tard (MQTT + SQLite) via `on_packet`
        ou le timer de timeout. Un échec d'émission (BLE down) → ligne `error` + `{ok:False}`.
        """
        dest_num, dest_id = normalize_dest(dest)
        sent_ts = self._clock()
        try:
            packet_id = self._send_fn(dest_num, hop_limit, channel_index)
        except Exception as exc:  # noqa: BLE001 — échec d'émission (BLE down) → statut error
            log.warning("[traceroute] échec émission → %s : %s", dest_id, exc)
            self._finalize_error(dest_id, sent_ts, hop_limit, source, str(exc))
            return {"ok": False, "error": str(exc), "dest": dest_id}
        timer = self._timer_factory(timeout_s, lambda: self._on_timeout(packet_id))
        setattr(timer, "daemon", True)
        with self._lock:
            self._pending[packet_id] = _Pending(dest_num, dest_id, sent_ts, hop_limit, source, timer)
        timer.start()
        log.info("[traceroute] envoyé → %s (request_id=%s, source=%s)", dest_id, packet_id, source)
        return {"ok": True, "request_id": packet_id, "dest": dest_id}

    def on_packet(self, packet: Dict[str, Any]) -> None:
        """Branché sur `meshtastic.receive` : corrèle un `TRACEROUTE_APP` entrant à une requête."""
        decoded = (packet or {}).get("decoded") or {}
        if decoded.get("portnum") != TRACEROUTE_PORTNUM:
            return
        request_id = decoded.get("requestId")
        if request_id is None:
            return
        with self._lock:
            pending = self._pending.get(request_id)
            # Corrélation stricte : requestId ET from == dest (ignore une réponse à autrui).
            if pending is None or packet.get("from") != pending.dest_num:
                return
            self._pending.pop(request_id, None)
        pending.timer.cancel()
        recv_ts = self._clock()
        origin_num = packet.get("to")  # destinataire de la réponse = nous (l'origine)
        route = self._parse_payload(decoded.get("payload"), origin_num, pending.dest_num)
        status = "ok" if route is not None else "error"
        result = build_result(
            status=status,
            gateway_node=self._gateway_id_fn(),
            dest=pending.dest_id,
            request_id=request_id,
            hop_limit=pending.hop_limit,
            sent_ts=pending.sent_ts,
            recv_ts=recv_ts,
            route=route,
            source=pending.source,
            error=None if route is not None else "réponse traceroute illisible",
        )
        log.info("[traceroute] réponse ← %s (%s, %s ms)", pending.dest_id, status, result["rtt_ms"])
        self._emit(result, pending.sent_ts, recv_ts)

    def _parse_payload(self, payload, origin_num, dest_num) -> Optional[Dict[str, Any]]:
        """Parse le `RouteDiscovery` du payload. None si illisible (ne plante jamais la boucle)."""
        try:
            from meshtastic.protobuf import mesh_pb2

            rd = mesh_pb2.RouteDiscovery()
            rd.ParseFromString(payload)
            return decode_route(
                list(rd.route), list(rd.snr_towards), list(rd.route_back), list(rd.snr_back),
                origin_num, dest_num, self._id_of,
            )
        except Exception as exc:  # noqa: BLE001 — payload corrompu → error, jamais de crash
            log.warning("[traceroute] payload illisible : %s", exc)
            return None

    def _on_timeout(self, request_id: int) -> None:
        with self._lock:
            pending = self._pending.pop(request_id, None)
        if pending is None:
            return
        log.info("[traceroute] timeout ← %s (request_id=%s)", pending.dest_id, request_id)
        result = build_result(
            status="timeout",
            gateway_node=self._gateway_id_fn(),
            dest=pending.dest_id,
            request_id=request_id,
            hop_limit=pending.hop_limit,
            sent_ts=pending.sent_ts,
            recv_ts=None,
            source=pending.source,
        )
        self._emit(result, pending.sent_ts, None)

    def _finalize_error(self, dest_id, sent_ts, hop_limit, source, error) -> None:
        result = build_result(
            status="error",
            gateway_node=self._gateway_id_fn(),
            dest=dest_id,
            request_id=0,
            hop_limit=hop_limit,
            sent_ts=sent_ts,
            recv_ts=None,
            source=source,
            error=error,
        )
        self._emit(result, sent_ts, None)

    def _emit(self, result: Dict[str, Any], sent_epoch: float, recv_epoch: Optional[float]) -> None:
        """Publie MQTT + écrit SQLite. Chaque frontière est isolée (jamais de crash de boucle)."""
        if self._store is not None:
            try:
                self._store.record_traceroute(result, sent_epoch, recv_epoch)
            except Exception as exc:  # noqa: BLE001
                log.warning("[traceroute] écriture SQLite échouée : %s", exc)
        try:
            self._publish_fn(self._topic, json.dumps(result).encode())
        except Exception as exc:  # noqa: BLE001 — un échec broker ne tue pas la corrélation
            log.warning("[traceroute] publish MQTT échoué : %s", exc)

    def cancel_all(self) -> None:
        """Annule les timers en attente (nettoyage de fin de session)."""
        with self._lock:
            for pending in self._pending.values():
                pending.timer.cancel()
            self._pending.clear()
