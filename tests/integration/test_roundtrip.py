# SPDX-License-Identifier: AGPL-3.0-or-later
"""Intégration : PahoPublisher contre un vrai broker Mosquitto.

Se skippe tout seul si aucun broker n'écoute sur localhost:1883.
En local : `docker compose -f poc/docker-compose.yml up -d` puis pytest.
"""
import socket
import time

import pytest

from mbg.mqtt_publisher import PahoPublisher


def _broker_available(host="localhost", port=1883):
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def _make_subscriber():
    import paho.mqtt.client as mqtt

    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    except AttributeError:  # paho < 2.0
        return mqtt.Client()


@pytest.mark.skipif(not _broker_available(), reason="pas de broker MQTT sur localhost:1883")
def test_publish_reaches_broker():
    received = []
    subscribed = []
    sub = _make_subscriber()
    # S'abonner DANS on_connect évite la course subscribe/CONNACK ; QoS 1 pour fiabilité.
    sub.on_connect = lambda c, u, flags, rc, *a: (c.subscribe("mbg/test/#", qos=1), subscribed.append(True))
    sub.on_message = lambda c, u, m: received.append((m.topic, m.payload))
    sub.connect("localhost", 1883)
    sub.loop_start()

    ready = time.time() + 5
    while not subscribed and time.time() < ready:
        time.sleep(0.05)
    time.sleep(0.3)  # laisser le SUBACK se poser

    pub = PahoPublisher("localhost", 1883)
    pub.connect()
    pub.publish("mbg/test/x", b"hello")

    deadline = time.time() + 5
    while not received and time.time() < deadline:
        time.sleep(0.05)

    pub.close()
    sub.loop_stop()
    sub.disconnect()

    assert ("mbg/test/x", b"hello") in received
