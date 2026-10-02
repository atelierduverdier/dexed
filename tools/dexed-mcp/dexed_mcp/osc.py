"""Client OSC minimal pour le serveur OSC de Dexed (et réutilisable pour Ardour).

Chaque requête Dexed porte en premier argument le port UDP sur lequel on attend
la réponse ; Dexed répond par "/dexed/reply" <adresse de la requête> <json>.
"""

from __future__ import annotations

import json
import socket
import time
from typing import Any, Iterable

from pythonosc.osc_message import OscMessage
from pythonosc.osc_message_builder import OscMessageBuilder
from pythonosc.osc_bundle import OscBundle

DEXED_BASE_PORT = 9000
DEXED_PORT_RANGE = 16


class OscError(RuntimeError):
    pass


def build_message(address: str, args: Iterable[Any]) -> bytes:
    """Encode un message OSC ; int -> i, float -> f, str -> s, bytes -> b."""
    b = OscMessageBuilder(address=address)
    for a in args:
        if isinstance(a, bool):
            b.add_arg(int(a), OscMessageBuilder.ARG_TYPE_INT)
        elif isinstance(a, int):
            b.add_arg(a, OscMessageBuilder.ARG_TYPE_INT)
        elif isinstance(a, float):
            b.add_arg(a, OscMessageBuilder.ARG_TYPE_FLOAT)
        elif isinstance(a, (bytes, bytearray)):
            b.add_arg(bytes(a), OscMessageBuilder.ARG_TYPE_BLOB)
        else:
            b.add_arg(str(a), OscMessageBuilder.ARG_TYPE_STRING)
    return b.build().dgram


def parse_packet(data: bytes) -> list[OscMessage]:
    """Décode un paquet (message ou bundle) en liste de messages."""
    if OscBundle.dgram_is_bundle(data):
        out: list[OscMessage] = []
        for item in OscBundle(data):
            out.extend(parse_packet(item.dgram) if isinstance(item, OscBundle) else [item])
        return out
    return [OscMessage(data)]


class DexedClient:
    """Pilote une instance de Dexed modifié via OSC sur 127.0.0.1."""

    def __init__(self, port: int = DEXED_BASE_PORT, host: str = "127.0.0.1", timeout: float = 3.0):
        self.host = host
        self.port = port
        self.timeout = timeout

    def request(self, address: str, *args: Any, timeout: float | None = None) -> dict:
        """Envoie une requête et attend la réponse JSON correspondante."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(("127.0.0.1", 0))
            reply_port = sock.getsockname()[1]
            sock.sendto(build_message(address, [reply_port, *args]), (self.host, self.port))
            deadline = time.monotonic() + (timeout or self.timeout)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise OscError(f"pas de réponse de Dexed sur le port {self.port} pour {address}")
                sock.settimeout(remaining)
                try:
                    data, _ = sock.recvfrom(65535)
                except socket.timeout:
                    continue
                for msg in parse_packet(data):
                    if msg.address == "/dexed/reply" and len(msg.params) >= 2 and msg.params[0] == address:
                        payload = json.loads(msg.params[1])
                        if isinstance(payload, dict) and payload.get("ok") is False:
                            raise OscError(payload.get("error", "erreur inconnue"))
                        return payload
        finally:
            sock.close()

    # --- raccourcis -----------------------------------------------------------
    def ping(self) -> dict:
        return self.request("/dexed/ping", timeout=0.5)

    def get(self) -> dict:
        return self.request("/dexed/get")

    def params(self) -> list[dict]:
        return self.request("/dexed/params")["params"]

    def set(self, values: dict[int, int]) -> dict:
        flat: list[int] = []
        for off, val in values.items():
            flat += [int(off), int(val)]
        return self.request("/dexed/set", *flat)

    def set_param(self, idx: int, host_value: float) -> dict:
        return self.request("/dexed/set_param", int(idx), float(host_value))

    def voice(self, data: bytes) -> dict:
        return self.request("/dexed/voice", bytes(data))

    def program(self, idx: int) -> dict:
        return self.request("/dexed/program", int(idx))

    def load_cart(self, path: str, program: int = 0) -> dict:
        return self.request("/dexed/load_cart", path, int(program))

    def store(self, idx: int, name: str | None = None) -> dict:
        return self.request("/dexed/store", int(idx), *([name] if name else []))

    def save_cart(self, path: str) -> dict:
        return self.request("/dexed/save_cart", path)

    def note(self, note: int, velocity: int = 100, duration_ms: int = 500) -> dict:
        return self.request("/dexed/note", int(note), int(velocity), int(duration_ms))

    def panic(self) -> dict:
        return self.request("/dexed/panic")

    def render(self, path: str, note: int = 60, velocity: int = 100, hold_ms: int = 1000, total_ms: int = 2000) -> dict:
        return self.request("/dexed/render", path, int(note), int(velocity), int(hold_ms), int(total_ms), timeout=30)


def discover(base: int = DEXED_BASE_PORT, count: int = DEXED_PORT_RANGE) -> list[dict]:
    """Liste les instances de Dexed qui répondent sur base..base+count-1."""
    found = []
    for p in range(base, base + count):
        try:
            info = DexedClient(p, timeout=0.25).request("/dexed/ping", timeout=0.25)
            found.append(info)
        except OscError:
            continue
    return found
