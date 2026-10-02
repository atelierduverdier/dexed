"""Écriture de fichiers MIDI standard (SMF type 0) sans dépendance.

Les notes sont décrites en temps musical : début et durée en temps (noires),
ce qui correspond directement à ce qu'Ardour importe.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass

PPQ = 480
NOTE_RE = re.compile(r"^([A-Ga-g])([#b]?)(-?\d+)$")
SEMITONES = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


@dataclass
class Note:
    pitch: int          # 0..127
    start: float        # en temps (noires) depuis le début du clip
    length: float       # en temps
    velocity: int = 100


def parse_pitch(value: int | str) -> int:
    """60, "C3", "F#2", "Bb1" -> numéro MIDI (convention Yamaha/Ardour : C3 = 60)."""
    if isinstance(value, int):
        p = value
    else:
        m = NOTE_RE.match(value.strip())
        if not m:
            raise ValueError(f"note illisible : {value!r} (ex. C3, F#2, 60)")
        letter, acc, octave = m.groups()
        p = SEMITONES[letter.upper()] + (1 if acc == "#" else -1 if acc == "b" else 0) + (int(octave) + 2) * 12
    if not 0 <= p <= 127:
        raise ValueError(f"note hors limites : {value!r}")
    return p


def _varlen(v: int) -> bytes:
    out = [v & 0x7F]
    v >>= 7
    while v:
        out.append((v & 0x7F) | 0x80)
        v >>= 7
    return bytes(reversed(out))


def write_smf(path: str, notes: list[Note], tempo_bpm: float = 120.0, name: str = "Claude", channel: int = 0) -> int:
    """Écrit le fichier, renvoie la longueur en ticks."""
    events: list[tuple[int, int, bytes]] = []    # (tick, ordre, données) ; note-off avant note-on au même tick
    for n in notes:
        on = int(round(n.start * PPQ))
        off = max(on + 1, int(round((n.start + n.length) * PPQ)))
        vel = max(1, min(127, int(n.velocity)))
        events.append((on, 1, bytes([0x90 | channel, n.pitch, vel])))
        events.append((off, 0, bytes([0x80 | channel, n.pitch, 0])))
    events.sort(key=lambda e: (e[0], e[1]))

    track = bytearray()
    tname = name.encode("utf-8")[:64]
    track += b"\x00\xff\x03" + _varlen(len(tname)) + tname
    usec = int(round(60_000_000 / tempo_bpm))
    track += b"\x00\xff\x51\x03" + usec.to_bytes(3, "big")
    track += b"\x00\xff\x58\x04\x04\x02\x18\x08"            # 4/4
    last = 0
    for tick, _, data in events:
        track += _varlen(tick - last) + data
        last = tick
    track += b"\x00\xff\x2f\x00"

    with open(path, "wb") as f:
        f.write(b"MThd" + struct.pack(">IHHH", 6, 0, 1, PPQ))
        f.write(b"MTrk" + struct.pack(">I", len(track)) + track)
    return last
