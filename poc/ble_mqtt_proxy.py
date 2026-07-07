#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Mickaël Hoareau — MeteoR-OI
"""
PoC — MQTT Client Proxy over BLE pour Meshtastic.

But : vérifier EMPIRIQUEMENT (sur un T114 réel) que le Client Proxy fonctionne
en Bluetooth, alors que la communauté le déconseille sur BLE. Si ça marche, ce
spike devient le cœur de la V0.1 de meshtastic-ble-gateway.

Principe (aucune reconstruction de format, on relaie tel quel) :
  - UPLINK   node -> broker : meshtastic-python publie en pubsub sur
    'meshtastic.mqttclientproxymessage' -> on republie topic+data au broker.
  - DOWNLINK broker -> node : interface.sendMqttClientProxyMessage(topic, data).
    Désactivé par défaut (--downlink) : en s'abonnant à 'msh/#' on ré-enverrait
    nos propres uplinks au node (boucle d'écho). Pour un test uplink pur, off.

Ce fichier est un PoC volontairement minimal : il n'est pas couvert par les
tests (sa valeur est la validation matérielle BLE, hors CI).
"""

import argparse
import logging
import signal
import sys
import time

import paho.mqtt.client as mqtt
from pubsub import pub  # fourni par meshtastic (pypubsub)
from meshtastic.ble_interface import BLEInterface

DEFAULT_BLE = "E6:E3:53:4B:BE:A5"  # T114 de test
log = logging.getLogger("ble-mqtt-proxy")


def make_mqtt_client() -> mqtt.Client:
    """Compatible paho-mqtt 1.x et 2.x (API callback versionnée en 2.0)."""
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    except AttributeError:  # paho < 2.0
        return mqtt.Client()


def main() -> int:
    ap = argparse.ArgumentParser(description="PoC MQTT Client Proxy over BLE (Meshtastic)")
    ap.add_argument("--ble", default=DEFAULT_BLE, help=f"MAC/nom BLE du node (défaut {DEFAULT_BLE})")
    ap.add_argument("--broker", default="localhost", help="hôte du broker MQTT (défaut localhost)")
    ap.add_argument("--port", type=int, default=1883, help="port MQTT (défaut 1883)")
    ap.add_argument("--username", default=None, help="user MQTT (optionnel)")
    ap.add_argument("--password", default=None, help="password MQTT (optionnel)")
    ap.add_argument("--downlink", action="store_true", help="activer broker->node (attention aux boucles d'écho)")
    ap.add_argument("-v", "--verbose", action="store_true", help="logs debug")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    # --- Broker MQTT ---
    mqttc = make_mqtt_client()
    if args.username:
        mqttc.username_pw_set(args.username, args.password)
    log.info("Connexion broker MQTT %s:%s ...", args.broker, args.port)
    mqttc.connect(args.broker, args.port, keepalive=60)
    mqttc.loop_start()

    # --- Relais UPLINK : node -> broker ---
    def on_proxy(proxymessage=None, interface=None):  # signature pubsub meshtastic
        topic = proxymessage.topic
        data = proxymessage.data
        mqttc.publish(topic, data)
        log.info("[uplink] %s (%d octets) -> broker", topic, len(data))

    pub.subscribe(on_proxy, "meshtastic.mqttclientproxymessage")

    # --- BLE : connexion au node ---
    log.info("Connexion BLE au node %s ...", args.ble)
    iface = BLEInterface(args.ble)
    log.info("Connecté. En écoute des messages Client Proxy. Ctrl-C pour quitter.")

    # --- Relais DOWNLINK optionnel : broker -> node ---
    if args.downlink:
        def on_mqtt_message(client, userdata, msg):
            iface.sendMqttClientProxyMessage(msg.topic, msg.payload)
            log.info("[downlink] %s (%d octets) -> node", msg.topic, len(msg.payload))
        mqttc.on_message = on_mqtt_message
        mqttc.subscribe("msh/#")
        log.warning("Downlink actif (msh/#) : risque de boucle d'écho sur tes propres uplinks.")

    # --- Boucle + arrêt propre ---
    stop = {"flag": False}
    def handle_sigint(signum, frame):
        stop["flag"] = True
    signal.signal(signal.SIGINT, handle_sigint)
    signal.signal(signal.SIGTERM, handle_sigint)

    try:
        while not stop["flag"]:
            time.sleep(0.5)
    finally:
        log.info("Arrêt : fermeture BLE et MQTT ...")
        try:
            iface.close()
        except Exception as exc:  # noqa: BLE001 — best effort au shutdown
            log.debug("close BLE: %s", exc)
        mqttc.loop_stop()
        mqttc.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
