"""Analyse d'un rendu WAV de Dexed : de quoi « entendre » un son par les chiffres.

Ce n'est pas une oreille : ça mesure l'enveloppe, la hauteur, le contenu
harmonique et la part inharmonique, ce qui suffit pour comparer deux réglages
et vérifier qu'un changement va dans le bon sens.
"""

from __future__ import annotations

import math
import wave

import numpy as np

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def midi_to_hz(note: int) -> float:
    return 440.0 * 2 ** ((note - 69) / 12)


def note_name(note: int) -> str:
    return f"{NOTE_NAMES[note % 12]}{note // 12 - 2}"   # convention Yamaha : 60 = C3


def load_wav(path: str) -> tuple[np.ndarray, int]:
    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        width = w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        x = (b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16))
        x = np.where(x >= 1 << 23, x - (1 << 24), x).astype(np.float64) / (1 << 23)
    elif width == 2:
        x = np.frombuffer(raw, dtype=np.int16).astype(np.float64) / 32768
    else:
        raise ValueError(f"profondeur {8 * width} bits non gérée")
    return x.reshape(-1, ch)[:, 0], sr


def _db(v: float) -> float:
    return 20 * math.log10(max(v, 1e-9))


def analyze(path: str, note: int, hold_ms: int) -> dict:
    x, sr = load_wav(path)
    n = len(x)
    if n == 0:
        return {"silence": True}

    peak = float(np.max(np.abs(x)))
    if peak < 1e-5:
        return {"silence": True, "conseil": "aucun son : vérifier les intensités des porteurs et les opérateurs coupés"}

    # --- enveloppe RMS par fenêtres de 10 ms --------------------------------
    hop = max(1, int(sr * 0.010))
    frames = n // hop
    rms = np.sqrt(np.mean(x[: frames * hop].reshape(frames, hop) ** 2, axis=1))
    rms_db = 20 * np.log10(np.maximum(rms, 1e-9))
    top = float(rms_db.max())
    i_peak = int(rms_db.argmax())
    above = np.nonzero(rms_db > top - 20)[0]
    reach = np.nonzero(rms_db >= top - 3)[0]
    attack_ms = float(reach[0] * 10) if len(reach) else float(i_peak * 10)
    t10 = int(above[0]) * 10 if len(above) else 0

    hold_frame = min(frames - 1, int(hold_ms / 10))
    sustain_db = float(rms_db[max(0, hold_frame - 5)] - top)        # juste avant le relâchement
    # temps pour perdre 40 dB après la note relâchée
    after = np.nonzero(rms_db[hold_frame:] < top - 40)[0]
    release_ms = float(after[0] * 10) if len(after) else None
    # pente pendant le maintien (dB/s) : percussif si très négative
    a, b = int(attack_ms / 10) + 2, hold_frame - 3
    decay_db_s = float((rms_db[b] - rms_db[a]) / ((b - a) * 0.010)) if b > a else 0.0

    # --- spectre sur une fenêtre stable (après l'attaque) ---------------------
    start = min(n - 1, int(sr * max(0.05, attack_ms / 1000)))
    seg = x[start: start + min(n - start, int(sr * 0.25))]
    if len(seg) < 2048:
        seg = x[: min(n, 8192)]
    win = np.hanning(len(seg))
    spec = np.abs(np.fft.rfft(seg * win))
    freqs = np.fft.rfftfreq(len(seg), 1 / sr)
    spec_pow = spec ** 2

    f0 = midi_to_hz(note)
    centroid = float(np.sum(freqs * spec_pow) / np.sum(spec_pow))

    # pics spectraux (au-dessus de -50 dB du plus fort), exprimés en multiples de f0
    smax = float(spec.max())
    peak_idx = [i for i in range(1, len(spec) - 1)
                if spec[i] > spec[i - 1] and spec[i] >= spec[i + 1] and spec[i] > smax * 10 ** (-50 / 20)
                and freqs[i] > 20]
    peak_idx.sort(key=lambda i: -spec[i])
    peaks = []
    harm_w = inharm_w = 0.0
    for i in peak_idx[:40]:
        # interpolation parabolique pour une fréquence plus précise que le pas FFT
        a, b, c = np.log(spec[i - 1] + 1e-12), np.log(spec[i] + 1e-12), np.log(spec[i + 1] + 1e-12)
        d = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) != 0 else 0.0
        fi = float((i + d) * (freqs[1] - freqs[0]))
        r = fi / f0
        w = float(spec[i] ** 2)
        if abs(r - round(r)) < 0.06 and round(r) >= 1:
            harm_w += w
        else:
            inharm_w += w
        peaks.append((round(r, 2), round(_db(float(spec[i]) / smax), 1)))
    inharm = inharm_w / (harm_w + inharm_w) if (harm_w + inharm_w) > 0 else 0.0

    harm_db = []
    for k in range(1, 17):
        band = (freqs > k * f0 * 0.97) & (freqs < k * f0 * 1.03)
        harm_db.append(round(_db(float(spec[band].max()) / smax), 1) if band.any() else None)

    # pic spectral principal -> hauteur perçue approximative
    lo = freqs > 20
    f_peak = float(freqs[lo][np.argmax(spec[lo])])

    brightness = "sombre" if centroid < 2 * f0 else "moyen" if centroid < 5 * f0 else "brillant" if centroid < 10 * f0 else "très brillant"
    shape = ("percussif" if decay_db_s < -25 else "décroissant" if decay_db_s < -6 else "tenu")

    return {
        "note": f"{note_name(note)} ({f0:.1f} Hz)",
        "crête_dBFS": round(_db(peak), 1),
        "attaque_ms": round(attack_ms),
        "début_audible_ms": t10,
        "maintien_vs_pic_dB": round(sustain_db, 1),
        "pente_pendant_maintien_dB_s": round(decay_db_s, 1),
        "relâchement_ms_-40dB": release_ms,
        "forme": shape,
        "centroïde_Hz": round(centroid),
        "centroïde_en_harmoniques": round(centroid / f0, 2),
        "brillance": brightness,
        "part_inharmonique": round(inharm, 3),
        "caractère": ("métallique / cloche" if inharm > 0.35 else "légèrement métallique" if inharm > 0.12 else "harmonique"),
        "pic_spectral_Hz": round(f_peak, 1),
        "harmoniques_1_16_dB": harm_db,
        "pics_principaux_ratio_dB": peaks[:12],
        "enveloppe_dB_par_50ms": [round(float(v - top), 1) for v in rms_db[::5]],
    }
