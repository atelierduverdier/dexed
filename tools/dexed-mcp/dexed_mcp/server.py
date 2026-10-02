"""Serveur MCP « dexed » : pilote Dexed (son) et Ardour (morceau) pour Claude.

Lancement : `dexed-mcp` (stdio). Variables d'environnement utiles :
  DEXED_MCP_PORT          port OSC de l'instance Dexed par défaut (sinon détection 9000-9015)
  DEXED_MCP_ARDOUR_PORT   port OSC d'Ardour (3819)
  DEXED_MCP_ARDOUR_SLOT   n° d'action Lua où « Claude Bridge » est assigné (1)
  DEXED_MCP_WORKDIR       dossier des rendus et clips (~/.local/share/dexed-mcp)
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import dx7
from .analysis import analyze
from .ardour import ArdourClient, BRIDGE_DIR
from .midi import Note, parse_pitch, write_smf
from .osc import DexedClient, OscError, discover

WORKDIR = Path(os.environ.get("DEXED_MCP_WORKDIR", Path.home() / ".local/share/dexed-mcp"))

# voix d'initialisation de Dexed (resetToInitVoice) : un seul porteur OP1 sinusoïdal
INIT_VOICE = (
    [99, 99, 99, 99, 99, 99, 99, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 7] * 5
    + [99, 99, 99, 99, 99, 99, 99, 0, 0, 0, 0, 0, 0, 0, 0, 0, 99, 0, 1, 0, 7]
    + [99, 99, 99, 99, 50, 50, 50, 50, 0, 0, 1, 35, 0, 0, 0, 1, 0, 3, 24]
    + [ord(c) for c in "INIT VOICE"]
)
assert len(INIT_VOICE) == 155

mcp = FastMCP(
    "dexed",
    instructions=(
        "Pilote le synthé FM Dexed (modèle DX7) et la DAW Ardour sur l'ordinateur de l'utilisateur. "
        "Avant de modifier un son, lire la voix (dexed_lire_voix). Pour juger un réglage, utiliser "
        "dexed_ecouter : il rend une note hors-ligne et renvoie des mesures (enveloppe, brillance, "
        "part inharmonique). Les opérateurs sont numérotés 1 à 6 comme sur le DX7 ; les porteurs "
        "s'entendent directement, les modulateurs changent le timbre. Notes : convention C3 = 60."
    ),
)


# --- utilitaires ---------------------------------------------------------------
def _dexed(port: int | None) -> DexedClient:
    if port:
        return DexedClient(port)
    env = os.environ.get("DEXED_MCP_PORT")
    if env:
        return DexedClient(int(env))
    found = discover()
    if not found:
        raise OscError("aucune instance de Dexed (version Atelier du Verdier) ne répond sur 9000-9015. "
                       "Lancer Dexed ou le charger dans Ardour.")
    return DexedClient(found[0]["port"])


def _voice_view(c: DexedClient) -> dict:
    g = c.get()
    return {
        "programme": g["program"],
        "nom": g["name"],
        "cartouche": g["cartFile"] or "(cartouche interne)",
        "résumé": dx7.summarize(g["voice"]),
        "détail": dx7.decode(g["voice"]),
        "filtre_sortie": {k: v["display"] for k, v in g["extra"].items()},
    }


def _resolve_changes(changes: list[dict]) -> dict[int, int]:
    """[{op, param, valeur}] -> {offset: valeur} ; gère le pseudo-paramètre `ratio`."""
    out: dict[int, int] = {}
    for ch in changes:
        param = str(ch.get("param", "")).strip()
        value = ch.get("valeur", ch.get("value"))
        op = ch.get("op")
        if value is None:
            raise ValueError(f"valeur manquante pour {param}")
        if param == "ratio":
            if not op:
                raise ValueError("ratio : préciser l'opérateur (op)")
            coarse, fine, _ = dx7.coarse_fine_for_ratio(float(value))
            out[dx7.op_offset(int(op), "osc_mode")] = 0
            out[dx7.op_offset(int(op), "coarse")] = coarse
            out[dx7.op_offset(int(op), "fine")] = fine
            continue
        if param == "algorithm_number":
            param, value = "algorithm", int(value) - 1
        if op:
            if param not in dx7.OP_KEYS:
                raise ValueError(f"paramètre d'opérateur inconnu : {param} (voir dexed_parametres)")
            off = dx7.op_offset(int(op), param)
        else:
            if param not in dx7.GLOBAL_KEYS:
                raise ValueError(f"paramètre global inconnu : {param} (voir dexed_parametres)")
            off = dx7.GLOBAL_KEYS[param].rel
        out[off] = dx7.clamp_value(off, int(round(float(value))))
    return out


def _name_bytes(name: str) -> dict[int, int]:
    name = (name + " " * 10)[:10]
    return {145 + i: (ord(ch) if 32 <= ord(ch) < 127 else 32) for i, ch in enumerate(name)}


# --- Dexed ------------------------------------------------------------------------
@mcp.tool()
def dexed_instances() -> list[dict]:
    """Liste les instances de Dexed joignables (port, programme, hôte : Standalone ou VST3…)."""
    return discover()


@mcp.tool()
def dexed_parametres() -> dict:
    """Liste les noms de paramètres utilisables avec dexed_modifier (opérateur et globaux), avec bornes et aide."""
    return {
        "operateur": {f.key: {"libellé": f.label, "max": f.max, "aide": f.help} for f in dx7.OP_FIELDS},
        "pseudo_operateur": {"ratio": "ratio de fréquence voulu (ex. 1, 2, 3.5, 0.5) → harmonique + fine"},
        "global": {f.key: {"libellé": f.label, "max": f.max, "aide": f.help} for f in dx7.GLOBAL_FIELDS},
        "pseudo_global": {"algorithm_number": "algorithme 1..32"},
    }


@mcp.tool()
def dexed_algorithmes() -> list[str]:
    """Décrit les 32 algorithmes DX7 : porteurs, qui module qui, rétroaction."""
    return [a.describe() for a in dx7.ALGORITHMS]


@mcp.tool()
def dexed_lire_voix(port: int | None = None) -> dict:
    """Lit la voix courante : résumé lisible par opérateur, détail complet, filtre et volume."""
    return _voice_view(_dexed(port))


@mcp.tool()
def dexed_modifier(changements: list[dict], port: int | None = None) -> dict:
    """Modifie la voix courante en direct (l'interface de Dexed et l'automation suivent).

    changements : liste de {"op": 1-6 (absent pour un paramètre global), "param": nom, "valeur": nombre}.
    Exemples : {"op": 2, "param": "level", "valeur": 70} ; {"op": 1, "param": "ratio", "valeur": 2} ;
    {"param": "feedback", "valeur": 5} ; {"param": "algorithm_number", "valeur": 5}.
    Les noms sont listés par dexed_parametres.
    """
    c = _dexed(port)
    offsets = _resolve_changes(changements)
    c.set(offsets)
    return {"appliqué": len(offsets), "résumé": dx7.summarize(c.get()["voice"])}


@mcp.tool()
def dexed_nouvelle_voix(nom: str, reglages: list[dict] | None = None, base: str = "init",
                        port: int | None = None) -> dict:
    """Crée une voix d'un coup et la charge dans Dexed.

    base : "init" (voix d'initialisation : seul OP1 sonne, sinus pur) ou "courante" (part du son actuel).
    reglages : même format que dexed_modifier. Pensez à régler level, ratio et l'enveloppe de chaque
    opérateur utile, et à mettre level à 0 pour les opérateurs inutiles.
    """
    c = _dexed(port)
    voice = list(INIT_VOICE) if base == "init" else list(c.get()["voice"][:155])
    for off, val in _resolve_changes(reglages or []).items():
        voice[off] = val
    for off, val in _name_bytes(nom).items():
        voice[off] = val
    c.voice(bytes(voice) + bytes([0x3F]))   # tous les opérateurs actifs
    return {"résumé": dx7.summarize(c.get()["voice"])}


@mcp.tool()
def dexed_renommer(nom: str, port: int | None = None) -> dict:
    """Renomme la voix courante (10 caractères ASCII max)."""
    c = _dexed(port)
    c.set(_name_bytes(nom))
    return {"nom": c.get()["name"]}


@mcp.tool()
def dexed_operateurs_actifs(actifs: list[int], port: int | None = None) -> dict:
    """Active uniquement les opérateurs listés (ex. [1, 2] pour écouter la paire OP1+OP2 seule).
    [1,2,3,4,5,6] rétablit tout. N'est pas sauvegardé dans la voix (comme les boutons ON/OFF de Dexed)."""
    c = _dexed(port)
    params = c.params()
    out = {}
    for op in range(1, 7):
        label = f"OP{op} SWITCH"
        p = next((p for p in params if p["label"] == label), None)
        if p is None:
            raise OscError(f"contrôle {label} introuvable")
        c.set_param(p["idx"], 1.0 if op in actifs else 0.0)
        out[f"OP{op}"] = op in actifs
    return out


@mcp.tool()
def dexed_filtre_volume(coupure: float | None = None, resonance: float | None = None,
                        volume: float | None = None, port: int | None = None) -> dict:
    """Règle le filtre passe-bas et le volume de sortie de Dexed (valeurs 0..1, hors voix DX7)."""
    c = _dexed(port)
    params = {p["label"]: p for p in c.params()}
    for label, v in (("Cutoff", coupure), ("Resonance", resonance), ("Output", volume)):
        if v is not None:
            c.set_param(params[label]["idx"], max(0.0, min(1.0, float(v))))
    g = c.get()
    return {k: v["display"] for k, v in g["extra"].items()}


@mcp.tool()
def dexed_programme(numero: int, port: int | None = None) -> dict:
    """Charge le programme 1..32 de la cartouche courante."""
    c = _dexed(port)
    c.program(max(1, min(32, int(numero))) - 1)
    return _voice_view(c)


@mcp.tool()
def dexed_liste_programmes(port: int | None = None) -> dict:
    """Noms des 32 programmes de la cartouche courante."""
    g = _dexed(port).get()
    return {"cartouche": g["cartFile"] or "(interne)", "courant": g["program"] + 1,
            "programmes": {i + 1: n for i, n in enumerate(g["programNames"])}}


@mcp.tool()
def dexed_charger_cartouche(chemin: str, programme: int = 1, port: int | None = None) -> dict:
    """Charge une cartouche .syx DX7 (chemin absolu) et sélectionne un programme 1..32."""
    c = _dexed(port)
    c.load_cart(os.path.expanduser(chemin), max(1, min(32, programme)) - 1)
    return _voice_view(c)


@mcp.tool()
def dexed_enregistrer(emplacement: int, nom: str | None = None, fichier: str | None = None,
                      port: int | None = None) -> dict:
    """Range la voix courante dans l'emplacement 1..32 de la cartouche, puis, si `fichier` est donné
    (chemin absolu .syx), écrit la cartouche sur disque. Sans fichier, c'est conservé dans la session."""
    c = _dexed(port)
    c.store(max(1, min(32, emplacement)) - 1, nom)
    if fichier:
        c.save_cart(os.path.expanduser(fichier))
    return {"emplacement": emplacement, "fichier": fichier, "nom": c.get()["name"]}


@mcp.tool()
def dexed_jouer_note(note: str | int = "C3", velocite: int = 100, duree_ms: int = 600,
                     port: int | None = None) -> dict:
    """Joue une note en direct sur Dexed (l'utilisateur l'entend). note : "C3", "F#2" ou 60."""
    c = _dexed(port)
    c.note(parse_pitch(note), velocite, duree_ms)
    return {"joué": note}


@mcp.tool()
def dexed_ecouter(note: str | int = "C3", velocite: int = 100, tenue_ms: int = 1000, total_ms: int = 2000,
                  port: int | None = None) -> dict:
    """Rend une note hors-ligne avec la voix courante et renvoie une analyse chiffrée :
    attaque, maintien, relâchement, brillance (centroïde), part inharmonique (métallique), niveaux des
    16 premières harmoniques. N'interrompt pas l'audio en cours. Le WAV est conservé sur disque."""
    c = _dexed(port)
    pitch = parse_pitch(note)
    WORKDIR.mkdir(parents=True, exist_ok=True)
    path = str(WORKDIR / f"rendu_{int(time.time() * 1000)}.wav")
    r = c.render(path, pitch, velocite, tenue_ms, total_ms)
    transpose = c.get()["voice"][144] - 24
    res = analyze(path, max(0, min(127, pitch + transpose)), tenue_ms)
    res["fichier"] = r["path"]
    return res


@mcp.tool()
def dexed_panique(port: int | None = None) -> dict:
    """Coupe toutes les notes de Dexed."""
    _dexed(port).panic()
    return {"ok": True}


# --- Ardour ------------------------------------------------------------------------
ardour = ArdourClient()


@mcp.tool()
def ardour_etat() -> dict:
    """Session Ardour ouverte : nom, tempo, tête de lecture, pistes et leurs plugins."""
    return ardour.status()


@mcp.tool()
def ardour_transport(action: str) -> dict:
    """Commande de transport : lecture, stop, debut, fin, boucle, enregistrer_session, annuler, retablir, panique_midi."""
    table = {
        "lecture": "/transport_play", "stop": "/transport_stop", "debut": "/goto_start",
        "fin": "/goto_end", "boucle": "/loop_toggle", "enregistrer_session": "/save_state",
        "annuler": "/undo", "retablir": "/redo", "panique_midi": "/midi_panic",
    }
    if action not in table:
        raise ValueError(f"action inconnue, choisir parmi {sorted(table)}")
    ardour.osc(table[action])
    return {"envoyé": table[action]}


@mcp.tool()
def ardour_aller_a(mesure: int, temps: float = 1.0, lancer: bool = False) -> dict:
    """Place la tête de lecture à une mesure (4/4, tempo constant supposé), et lance la lecture si demandé."""
    return ardour.locate_beat((mesure - 1) * 4 + (temps - 1), lancer)


@mcp.tool()
def ardour_tempo(bpm: float) -> dict:
    """Fixe le tempo de la session (début du morceau)."""
    return ardour.set_tempo(bpm)


@mcp.tool()
def ardour_creer_piste(nom: str, instrument: str = "Dexed") -> dict:
    """Crée une piste MIDI avec un instrument (par défaut Dexed en VST3)."""
    return ardour.add_midi_track(nom, instrument)


@mcp.tool()
def ardour_clip_midi(notes: list[dict], piste: str, mesure: int = 1, tempo_bpm: float | None = None) -> dict:
    """Écrit un clip MIDI et l'importe dans Ardour.

    notes : liste de {"note": "C2" ou 48, "debut": temps en noires depuis le début du clip,
    "duree": en noires, "velocite": 1-127}. Ex. une noire sur le 2e temps : debut 1, duree 1.
    piste : piste existante (import dedans) ou nouveau nom (crée une piste MIDI avec Dexed).
    mesure : mesure de départ (4/4).
    """
    parsed = [Note(parse_pitch(n["note"]), float(n.get("debut", 0)), float(n.get("duree", 1)),
                   int(n.get("velocite", 100))) for n in notes]
    if not parsed:
        raise ValueError("aucune note")
    if tempo_bpm is None:
        try:
            tempo_bpm = float(ardour.status()["tempo_bpm"])
        except Exception:
            tempo_bpm = 120.0
    clips = BRIDGE_DIR / "clips"
    clips.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch if ch.isalnum() else "_" for ch in piste)[:40] or "clip"
    path = clips / f"{safe}_{int(time.time() * 1000)}.mid"
    write_smf(str(path), parsed, tempo_bpm, name=piste)
    res = ardour.import_midi(str(path), piste, (mesure - 1) * 4.0)
    res["fichier"] = str(path)
    res["notes"] = len(parsed)
    return res


@mcp.tool()
def ardour_lua(code: str, delai_s: float = 10.0) -> Any:
    """Exécute du Lua dans Ardour (API Ardour : Session, Editor, ARDOUR, Temporal…).
    Le code est un corps de fonction : utiliser `return` pour renvoyer une valeur (table ou chaîne).
    Outil avancé : à n'utiliser que si les autres outils ne suffisent pas."""
    return ardour.lua(code, timeout=delai_s)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
