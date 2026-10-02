"""Modèle de voix DX7 (format « unpacked » 155 octets, celui de Dexed).

Fournit :
- la table des paramètres (offset, nom technique, nom clair en français, bornes) ;
- le décodage / encodage d'une voix ;
- la topologie des 32 algorithmes, dérivée de la table de msfa (fm_core.cc)
  plutôt que recopiée de mémoire ;
- des conversions lisibles (ratio de fréquence, Hz en mode fixe, désaccord).
"""

from __future__ import annotations

from dataclasses import dataclass
import math

VOICE_SIZE = 155
OP_SIZE = 21

# --- paramètres par opérateur (offset relatif dans le bloc de 21 octets) ------
@dataclass(frozen=True)
class Field:
    rel: int
    key: str
    label: str
    max: int
    help: str = ""


OP_FIELDS: list[Field] = [
    Field(0, "eg_rate1", "Enveloppe – vitesse 1 (attaque)", 99, "0 = très lent, 99 = instantané"),
    Field(1, "eg_rate2", "Enveloppe – vitesse 2 (déclin 1)", 99),
    Field(2, "eg_rate3", "Enveloppe – vitesse 3 (déclin 2)", 99),
    Field(3, "eg_rate4", "Enveloppe – vitesse 4 (relâchement)", 99),
    Field(4, "eg_level1", "Enveloppe – niveau 1 (pic d'attaque)", 99),
    Field(5, "eg_level2", "Enveloppe – niveau 2", 99),
    Field(6, "eg_level3", "Enveloppe – niveau 3 (maintien)", 99, "niveau tenu tant que la touche est enfoncée"),
    Field(7, "eg_level4", "Enveloppe – niveau 4 (repos)", 99, "en général 0"),
    Field(8, "kls_break", "Suivi clavier – note pivot", 99, "39 = C3"),
    Field(9, "kls_left_depth", "Suivi clavier – profondeur à gauche", 99),
    Field(10, "kls_right_depth", "Suivi clavier – profondeur à droite", 99),
    Field(11, "kls_left_curve", "Suivi clavier – courbe gauche", 3, "0 -LIN, 1 -EXP, 2 +EXP, 3 +LIN"),
    Field(12, "kls_right_curve", "Suivi clavier – courbe droite", 3, "0 -LIN, 1 -EXP, 2 +EXP, 3 +LIN"),
    Field(13, "rate_scaling", "Enveloppe plus rapide dans les aigus", 7),
    Field(14, "amp_mod_sens", "Sensibilité au trémolo (LFO)", 3),
    Field(15, "velocity_sens", "Sensibilité à la vélocité", 7),
    Field(16, "level", "Intensité (volume ou quantité de modulation)", 99),
    Field(17, "osc_mode", "Mode fréquence", 1, "0 = ratio (suit la note), 1 = fixe en Hz"),
    Field(18, "coarse", "Harmonique (fréquence grossière)", 31, "0 = ×0,5 ; n = ×n"),
    Field(19, "fine", "Fréquence fine", 99, "+0..+99 % du ratio"),
    Field(20, "detune", "Désaccord", 14, "7 = centré"),
]

GLOBAL_FIELDS: list[Field] = [
    Field(126, "pitch_eg_rate1", "Enveloppe de hauteur – vitesse 1", 99),
    Field(127, "pitch_eg_rate2", "Enveloppe de hauteur – vitesse 2", 99),
    Field(128, "pitch_eg_rate3", "Enveloppe de hauteur – vitesse 3", 99),
    Field(129, "pitch_eg_rate4", "Enveloppe de hauteur – vitesse 4", 99),
    Field(130, "pitch_eg_level1", "Enveloppe de hauteur – niveau 1", 99, "50 = hauteur normale"),
    Field(131, "pitch_eg_level2", "Enveloppe de hauteur – niveau 2", 99),
    Field(132, "pitch_eg_level3", "Enveloppe de hauteur – niveau 3", 99),
    Field(133, "pitch_eg_level4", "Enveloppe de hauteur – niveau 4", 99),
    Field(134, "algorithm", "Algorithme (0-31 ⇒ 1-32)", 31),
    Field(135, "feedback", "Rétroaction (grain, saturation)", 7),
    Field(136, "osc_sync", "Synchro des oscillateurs à chaque note", 1),
    Field(137, "lfo_speed", "LFO – vitesse", 99),
    Field(138, "lfo_delay", "LFO – délai d'apparition", 99),
    Field(139, "lfo_pitch_depth", "LFO – vibrato (profondeur)", 99),
    Field(140, "lfo_amp_depth", "LFO – trémolo (profondeur)", 99),
    Field(141, "lfo_sync", "LFO – redémarre à chaque note", 1),
    Field(142, "lfo_wave", "LFO – forme d'onde", 5, "0 tri, 1 scie-, 2 scie+, 3 carré, 4 sinus, 5 S&H"),
    Field(143, "pitch_mod_sens", "Sensibilité au vibrato", 7),
    Field(144, "transpose", "Transposition (24 = C3, ±1 = demi-ton)", 48),
]

LFO_WAVES = ["triangle", "scie descendante", "scie montante", "carré", "sinus", "échantillonné-bloqué"]
CURVES = ["-LIN", "-EXP", "+EXP", "+LIN"]
OP_KEYS = {f.key: f for f in OP_FIELDS}
GLOBAL_KEYS = {f.key: f for f in GLOBAL_FIELDS}


def op_base(op: int) -> int:
    """Offset du bloc d'un opérateur 1..6 (OP6 est stocké en premier)."""
    if not 1 <= op <= 6:
        raise ValueError("opérateur 1..6")
    return (6 - op) * OP_SIZE


def op_offset(op: int, key: str) -> int:
    return op_base(op) + OP_KEYS[key].rel


def field_for_offset(offset: int) -> tuple[int | None, Field]:
    """(opérateur ou None, champ) pour un offset 0..144."""
    if offset < 126:
        op = 6 - offset // OP_SIZE
        return op, OP_FIELDS[offset % OP_SIZE]
    for f in GLOBAL_FIELDS:
        if f.rel == offset:
            return None, f
    raise ValueError(f"offset {offset} hors table")


# --- algorithmes -----------------------------------------------------------------
# Table msfa : index 0 = OP6 ... index 5 = OP1 (ordre de calcul).
_MSFA_ALGOS = [
    (0xc1, 0x11, 0x11, 0x14, 0x01, 0x14), (0x01, 0x11, 0x11, 0x14, 0xc1, 0x14),
    (0xc1, 0x11, 0x14, 0x01, 0x11, 0x14), (0xc1, 0x11, 0x94, 0x01, 0x11, 0x14),
    (0xc1, 0x14, 0x01, 0x14, 0x01, 0x14), (0xc1, 0x94, 0x01, 0x14, 0x01, 0x14),
    (0xc1, 0x11, 0x05, 0x14, 0x01, 0x14), (0x01, 0x11, 0xc5, 0x14, 0x01, 0x14),
    (0x01, 0x11, 0x05, 0x14, 0xc1, 0x14), (0x01, 0x05, 0x14, 0xc1, 0x11, 0x14),
    (0xc1, 0x05, 0x14, 0x01, 0x11, 0x14), (0x01, 0x05, 0x05, 0x14, 0xc1, 0x14),
    (0xc1, 0x05, 0x05, 0x14, 0x01, 0x14), (0xc1, 0x05, 0x11, 0x14, 0x01, 0x14),
    (0x01, 0x05, 0x11, 0x14, 0xc1, 0x14), (0xc1, 0x11, 0x02, 0x25, 0x05, 0x14),
    (0x01, 0x11, 0x02, 0x25, 0xc5, 0x14), (0x01, 0x11, 0x11, 0xc5, 0x05, 0x14),
    (0xc1, 0x14, 0x14, 0x01, 0x11, 0x14), (0x01, 0x05, 0x14, 0xc1, 0x14, 0x14),
    (0x01, 0x14, 0x14, 0xc1, 0x14, 0x14), (0xc1, 0x14, 0x14, 0x14, 0x01, 0x14),
    (0xc1, 0x14, 0x14, 0x01, 0x14, 0x04), (0xc1, 0x14, 0x14, 0x14, 0x04, 0x04),
    (0xc1, 0x14, 0x14, 0x04, 0x04, 0x04), (0xc1, 0x05, 0x14, 0x01, 0x14, 0x04),
    (0x01, 0x05, 0x14, 0xc1, 0x14, 0x04), (0x04, 0xc1, 0x11, 0x14, 0x01, 0x14),
    (0xc1, 0x14, 0x01, 0x14, 0x04, 0x04), (0x04, 0xc1, 0x11, 0x14, 0x04, 0x04),
    (0xc1, 0x14, 0x04, 0x04, 0x04, 0x04), (0xc4, 0x04, 0x04, 0x04, 0x04, 0x04),
]


@dataclass(frozen=True)
class Algorithm:
    number: int                       # 1..32
    carriers: tuple[int, ...]         # opérateurs entendus directement
    modulates: dict                   # op -> tuple des opérateurs qu'il module
    feedback: tuple[int, int]         # (source, destination) de la rétroaction

    def describe(self) -> str:
        parts = [f"Algorithme {self.number} : porteurs {', '.join(f'OP{c}' for c in self.carriers)}"]
        for src in sorted(self.modulates):
            dst = self.modulates[src]
            parts.append(f"OP{src} → " + ", ".join(f"OP{d}" for d in dst))
        s, d = self.feedback
        parts.append(f"rétroaction OP{s}" + ("" if s == d else f" → OP{d}"))
        return " ; ".join(parts)


def _derive(number: int, flags: tuple[int, ...]) -> Algorithm:
    buses: dict[int, set[int]] = {1: set(), 2: set()}
    modulates: dict[int, set[int]] = {}
    carriers: list[int] = []
    fb_out = fb_in = None
    for idx, f in enumerate(flags):
        op = 6 - idx
        inbus, outbus = (f >> 4) & 3, f & 3
        if inbus:
            for src in buses[inbus]:
                modulates.setdefault(src, set()).add(op)
        if f & 0x80:
            fb_out = op
        if f & 0x40:
            fb_in = op
        if outbus == 0:
            carriers.append(op)
        elif f & 0x04:
            buses[outbus].add(op)
        else:
            buses[outbus] = {op}
    return Algorithm(
        number=number,
        carriers=tuple(sorted(carriers)),
        modulates={k: tuple(sorted(v)) for k, v in modulates.items()},
        # FB_OUT désigne l'opérateur dont la sortie est réinjectée, FB_IN celui qui la reçoit
        feedback=(fb_out if fb_out is not None else fb_in, fb_in),
    )


ALGORITHMS = [_derive(i + 1, f) for i, f in enumerate(_MSFA_ALGOS)]


# --- conversions lisibles ----------------------------------------------------------
def ratio(coarse: int, fine: int) -> float:
    base = 0.5 if coarse == 0 else float(coarse)
    return base * (1 + fine / 100.0)


def fixed_hz(coarse: int, fine: int) -> float:
    """Mode fixe : coarse choisit la décade (1, 10, 100, 1000 Hz), fine multiplie par 10^(fine/100)."""
    return 10 ** (coarse & 3) * 10 ** (fine / 100.0)


def coarse_fine_for_ratio(target: float) -> tuple[int, int, float]:
    """Meilleur (coarse, fine) pour un ratio voulu ; renvoie aussi le ratio obtenu."""
    best = (1, 0, 1.0)
    best_err = math.inf
    for coarse in range(32):
        base = 0.5 if coarse == 0 else coarse
        fine = round((target / base - 1) * 100)
        if 0 <= fine <= 99:
            got = ratio(coarse, fine)
            err = abs(math.log(got / target))
            # à précision égale, préférer le plus petit fine (harmonique « pure »)
            if err < best_err - 1e-9:
                best, best_err = (coarse, fine, got), err
    return best


# --- voix ------------------------------------------------------------------------
def voice_name(data: list[int] | bytes) -> str:
    """Même normalisation que Dexed : 92 (yen) -> Y, 126 -> >, 127 -> <."""
    special = {92: "Y", 126: ">", 127: "<"}
    out = []
    for c in data[145:155]:
        c &= 0x7F
        out.append(special.get(c, chr(c) if 32 <= c < 127 else " "))
    return "".join(out).rstrip()


def decode(data: list[int] | bytes) -> dict:
    """Voix 155/156 octets -> structure lisible."""
    if len(data) < VOICE_SIZE:
        raise ValueError("voix incomplète")
    alg = ALGORITHMS[data[134] & 31]
    op_switch = data[155] if len(data) > 155 else 0x3F
    ops = {}
    for op in range(1, 7):
        b = op_base(op)
        o = {f.key: int(data[b + f.rel]) for f in OP_FIELDS}
        o["enabled"] = bool((op_switch >> (6 - op)) & 1)   # bit 5 = OP1 ... bit 0 = OP6
        o["role"] = "porteur" if op in alg.carriers else "modulateur"
        if o["osc_mode"] == 0:
            o["frequency"] = f"×{ratio(o['coarse'], o['fine']):.3g}"
        else:
            o["frequency"] = f"{fixed_hz(o['coarse'], o['fine']):.4g} Hz (fixe)"
        o["detune_steps"] = o["detune"] - 7
        ops[f"OP{op}"] = o
    g = {f.key: int(data[f.rel]) for f in GLOBAL_FIELDS}
    g["algorithm_number"] = alg.number
    g["lfo_wave_name"] = LFO_WAVES[min(g["lfo_wave"], 5)]
    g["transpose_semitones"] = g["transpose"] - 24
    return {
        "name": voice_name(data),
        "algorithm": alg.describe(),
        "global": g,
        "operators": ops,
    }


def summarize(data: list[int] | bytes) -> str:
    """Résumé court et lisible d'une voix, opérateur par opérateur."""
    d = decode(data)
    g = d["global"]
    lines = [f"« {d['name']} » — {d['algorithm']}",
             f"Rétroaction {g['feedback']}/7, LFO {g['lfo_wave_name']} vitesse {g['lfo_speed']} "
             f"(vibrato {g['lfo_pitch_depth']}, trémolo {g['lfo_amp_depth']}), transposition {g['transpose_semitones']:+d}"]
    for name, o in d["operators"].items():
        state = "" if o["enabled"] else " [coupé]"
        env = "R " + "/".join(str(o[f"eg_rate{i}"]) for i in range(1, 5)) + \
              " L " + "/".join(str(o[f"eg_level{i}"]) for i in range(1, 5))
        lines.append(f"{name} {o['role']}{state} : intensité {o['level']}, {o['frequency']}, "
                     f"désaccord {o['detune_steps']:+d}, vélocité {o['velocity_sens']}, env {env}")
    return "\n".join(lines)


def clamp_value(offset: int, value: int) -> int:
    if offset >= 145:
        return max(32, min(126, int(value)))
    _, f = field_for_offset(offset)
    return max(0, min(f.max, int(value)))
