"""Pilotage d'Ardour : OSC pour le transport, pont Lua pour le reste.

- OSC (UDP 3819 par défaut) : lecture, arrêt, retour au début, boucle,
  positionnement, sauvegarde. À activer dans Préférences → Surfaces de contrôle → OSC.
- Pont Lua : le script d'action « Claude Bridge » (ardour/claude_bridge.lua)
  exécute un fichier Lua déposé ici, déclenché par l'OSC /access_action. Il sert
  à lister les pistes, créer une piste avec Dexed, importer des clips MIDI,
  changer le tempo, etc.
"""

from __future__ import annotations

import json
import os
import socket
import time
import uuid
from pathlib import Path

from .osc import build_message

BRIDGE_DIR = Path(os.environ.get("DEXED_MCP_ARDOUR_DIR", Path.home() / ".local/share/dexed-mcp/ardour"))
DEFAULT_SLOT = int(os.environ.get("DEXED_MCP_ARDOUR_SLOT", "1"))


class ArdourError(RuntimeError):
    pass


def lua_str(s: str) -> str:
    """Littéral de chaîne Lua sûr (UTF-8 conservé)."""
    out = ['"']
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ord(ch) < 32:
            out.append(f"\\{ord(ch):03d}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


class ArdourClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 3819, slot: int = DEFAULT_SLOT):
        self.host = host
        self.port = int(os.environ.get("DEXED_MCP_ARDOUR_PORT", port))
        self.slot = slot

    # --- OSC ------------------------------------------------------------------
    def osc(self, address: str, *args) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.sendto(build_message(address, list(args)), (self.host, self.port))
        finally:
            sock.close()

    # --- pont Lua -------------------------------------------------------------
    def lua(self, body: str, timeout: float = 10.0):
        """Exécute `body` (corps de fonction Lua, `local json = ...` disponible) dans Ardour."""
        BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
        result = BRIDGE_DIR / "result.txt"
        cmd = BRIDGE_DIR / "cmd.lua"
        if result.exists():
            result.unlink()
        rid = uuid.uuid4().hex[:12]
        tmp = BRIDGE_DIR / "cmd.tmp"
        tmp.write_text(f"-- id:{rid}\nlocal json = ...\n{body}\n", encoding="utf-8")
        tmp.replace(cmd)

        self.osc("/access_action", f"LuaAction/script-{self.slot}")

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if result.exists():
                text = result.read_text(encoding="utf-8")
                lines = text.split("\n", 2)
                if len(lines) == 3 and lines[0] == rid:
                    result.unlink()
                    payload = json.loads(lines[2]) if lines[2] else None
                    if lines[1] != "ok":
                        raise ArdourError(f"erreur Lua dans Ardour : {payload}")
                    return payload
            time.sleep(0.05)

        if cmd.exists():
            cmd.unlink()
            raise ArdourError(
                "Ardour n'a pas exécuté la commande. Vérifier : Ardour ouvert avec une session, "
                f"OSC activé (port {self.port}), script « Claude Bridge » assigné à l'action Lua n°{self.slot}.")
        raise ArdourError("la commande a été lue mais aucun résultat n'est revenu (erreur dans Ardour ?)")

    # --- commandes de haut niveau ----------------------------------------------
    def status(self) -> dict:
        return self.lua("""
local tm = Temporal.TempoMap.read ()
local tempo = tm:tempo_at (Temporal.timepos_t (0))
local tracks = {}
for r in Session:get_routes ():iter () do
	if not r:is_master () and not r:is_monitor () then
		local plugins = {}
		local i = 0
		while true do
			local p = r:nth_plugin (i)
			if p:isnil () then break end
			plugins[#plugins + 1] = p:display_name ()
			i = i + 1
		end
		local t = r:to_track ()
		local kind = "bus"
		if not t:isnil () then
			kind = t:data_type ():to_string ()
		end
		tracks[#tracks + 1] = { name = r:name (), kind = kind, plugins = plugins,
		                        muted = r:muted (), soloed = r:soloed () }
	end
end
return {
	session = Session:name (),
	path = Session:path (),
	sample_rate = Session:nominal_sample_rate (),
	tempo_bpm = tempo:quarter_notes_per_minute (),
	playhead_sample = Session:transport_sample (),
	rolling = Session:transport_rolling (),
	tracks = tracks,
}
""")

    def add_midi_track(self, name: str, instrument: str = "Dexed") -> dict:
        return self.lua(f"""
local name = {lua_str(name)}
local inst = ARDOUR.LuaAPI.new_plugin_info ({lua_str(instrument)}, ARDOUR.PluginType.VST3)
if inst:isnil () then
	inst = ARDOUR.LuaAPI.new_plugin_info ({lua_str(instrument)}, ARDOUR.PluginType.LV2)
end
if inst:isnil () then
	error ("plugin " .. {lua_str(instrument)} .. " introuvable : lancer un scan des plugins (Fenêtre → Gestionnaire de plugins)")
end
local tl = Session:new_midi_track (ARDOUR.ChanCount (ARDOUR.DataType ("midi"), 1),
	ARDOUR.ChanCount (ARDOUR.DataType ("audio"), 2), true, inst, nil, nil, 1, name,
	ARDOUR.PresentationInfo.max_order, ARDOUR.TrackMode.Normal, true)
local created = {{}}
for t in tl:iter () do created[#created + 1] = t:name () end
return {{ tracks = created }}
""")

    def import_midi(self, path: str, track: str | None, beat: float, new_track_instrument: str = "Dexed") -> dict:
        """Importe un .mid à la position `beat` (en noires depuis le début).

        Si `track` existe : import dans cette piste. Sinon : nouvelle piste MIDI
        avec l'instrument, nommée d'après le nom de piste du fichier MIDI.
        """
        ticks = int(round(beat * 1920))   # Temporal.ticks_per_beat = 1920
        return self.lua(f"""
local files = C.StringVector ()
files:push_back ({lua_str(path)})
local pos = Temporal.timepos_t.from_ticks ({ticks})
local target = nil
local tname = {lua_str(track or "")}
if tname ~= "" then
	local r = Session:route_by_name (tname)
	if not r:isnil () and not r:to_track ():isnil () then target = r:to_track () end
end
if target then
	Editor:do_import (files, Editing.ImportDistinctFiles, Editing.ImportToTrack, ARDOUR.SrcQuality.SrcBest,
		ARDOUR.MidiTrackNameSource.SMFTrackName, ARDOUR.MidiTempoMapDisposition.SMFTempoIgnore,
		pos, ARDOUR.PluginInfo (), target, false)
	return {{ imported_into = tname }}
end
local inst = ARDOUR.LuaAPI.new_plugin_info ({lua_str(new_track_instrument)}, ARDOUR.PluginType.VST3)
if inst:isnil () then inst = ARDOUR.PluginInfo () end
Editor:do_import (files, Editing.ImportDistinctFiles, Editing.ImportAsTrack, ARDOUR.SrcQuality.SrcBest,
	ARDOUR.MidiTrackNameSource.SMFTrackName, ARDOUR.MidiTempoMapDisposition.SMFTempoIgnore,
	pos, inst, ARDOUR.Track (), false)
return {{ new_track = true, instrument_found = not inst:isnil () }}
""", timeout=20)

    def set_tempo(self, bpm: float) -> dict:
        return self.lua(f"""
local tm = Temporal.TempoMap.write_copy ()
tm:set_tempo (Temporal.Tempo ({float(bpm)}, {float(bpm)}, 4), Temporal.timepos_t (0))
Temporal.TempoMap.update (tm)
return {{ tempo_bpm = Temporal.TempoMap.read ():tempo_at (Temporal.timepos_t (0)):quarter_notes_per_minute () }}
""")

    def locate_beat(self, beat: float, roll: bool = False) -> dict:
        """Place la tête de lecture (tempo constant supposé)."""
        st = self.status()
        samples = int(round(beat * 60.0 / st["tempo_bpm"] * st["sample_rate"]))
        self.osc("/locate", samples, 1 if roll else 0)
        return {"sample": samples}
