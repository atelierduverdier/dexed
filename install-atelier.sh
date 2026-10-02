#!/usr/bin/env bash
# Installation de Dexed (version Atelier du Verdier, avec contrôle OSC) et du
# serveur MCP « dexed » pour Claude, sous Linux (testé pour Arch / CachyOS).
#
#   ./install-atelier.sh                    compile et installe tout
#   ./install-atelier.sh --configure-claude ajoute aussi le serveur MCP à Claude Desktop
#   ./install-atelier.sh --skip-build       réinstalle sans recompiler
#
# Rien n'est fait avec sudo : les dépendances manquantes sont seulement signalées.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD="$ROOT/build"
ART="$BUILD/Source/Dexed_artefacts/Release"
CONFIGURE_CLAUDE=0
SKIP_BUILD=0

for arg in "$@"; do
    case "$arg" in
        --configure-claude) CONFIGURE_CLAUDE=1 ;;
        --skip-build)       SKIP_BUILD=1 ;;
        -h|--help)          sed -n '2,10p' "$0"; exit 0 ;;
        *) echo "Option inconnue : $arg" >&2; exit 1 ;;
    esac
done

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }

# --- 1. dépendances -----------------------------------------------------------
say "Vérification des dépendances"
missing=()
for tool in git cmake ninja g++ pkg-config uv; do
    command -v "$tool" >/dev/null || missing+=("$tool")
done
for pc in alsa freetype2 x11 xrandr xinerama xcursor xcomposite jack; do
    pkg-config --exists "$pc" 2>/dev/null || missing+=("pc:$pc")
done
if ((${#missing[@]})); then
    warn "Manquant : ${missing[*]}"
    warn "Sur Arch/CachyOS : sudo pacman -S --needed base-devel git cmake ninja uv alsa-lib freetype2 libx11 libxrandr libxinerama libxcursor libxcomposite pipewire-jack"
    exit 1
fi

# --- 2. compilation -------------------------------------------------------------
if ((SKIP_BUILD == 0)); then
    say "Sous-modules (JUCE, VST3 SDK, …)"
    git -C "$ROOT" submodule update --init --recursive --depth 1

    say "Compilation (Release, quelques minutes)"
    cmake -S "$ROOT" -B "$BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release
    cmake --build "$BUILD" -j"$(nproc)"
fi

[[ -d "$ART/VST3/Dexed.vst3" ]] || { warn "Build introuvable dans $ART"; exit 1; }

# --- 3. installation des plugins et de l'application ---------------------------------
say "Installation : VST3 → ~/.vst3, CLAP → ~/.clap, application → ~/.local/bin/dexed"
mkdir -p "$HOME/.vst3" "$HOME/.clap" "$HOME/.local/bin" "$HOME/.local/share/applications"
rm -rf "$HOME/.vst3/Dexed.vst3"
cp -r "$ART/VST3/Dexed.vst3" "$HOME/.vst3/"
cp "$ART/CLAP/Dexed.clap" "$HOME/.clap/"
install -m 755 "$ART/Standalone/Dexed" "$HOME/.local/bin/dexed"
install -m 644 "$ROOT/assets/ui/dexedIcon.png" "$HOME/.local/share/applications/dexed-atelier.png"
cat > "$HOME/.local/share/applications/dexed-atelier.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Dexed (Atelier)
Comment=Synthé FM type DX7, pilotable par Claude (OSC)
Exec=$HOME/.local/bin/dexed
Icon=$HOME/.local/share/applications/dexed-atelier.png
Categories=AudioVideo;Audio;Midi;Music;
EOF

# --- 4. serveur MCP ---------------------------------------------------------------
say "Serveur MCP dexed-mcp (uv tool)"
uv tool install --force "$ROOT/tools/dexed-mcp"
MCP_BIN="$(uv tool dir --bin 2>/dev/null || echo "$HOME/.local/bin")/dexed-mcp"
[[ -x "$MCP_BIN" ]] || MCP_BIN="$(command -v dexed-mcp || true)"

# --- 5. pont Lua pour Ardour ----------------------------------------------------------
say "Script Lua « Claude Bridge » pour Ardour"
shopt -s nullglob
dirs=("$HOME"/.config/ardour[0-9]*)
((${#dirs[@]})) || dirs=("$HOME/.config/ardour8")
for d in "${dirs[@]}"; do
    mkdir -p "$d/scripts"
    cp "$ROOT/tools/dexed-mcp/ardour/claude_bridge.lua" "$d/scripts/"
    echo "   → $d/scripts/claude_bridge.lua"
done
mkdir -p "$HOME/.local/share/dexed-mcp/ardour"

# --- 6. Claude Desktop ---------------------------------------------------------------
CFG="$HOME/.config/Claude/claude_desktop_config.json"
if ((CONFIGURE_CLAUDE)); then
    say "Ajout du serveur « dexed » dans $CFG"
    mkdir -p "$(dirname "$CFG")"
    [[ -f "$CFG" ]] && cp "$CFG" "$CFG.bak.$(date +%Y%m%d%H%M%S)"
    python3 - "$CFG" "$MCP_BIN" <<'PY'
import json, sys, os
path, binary = sys.argv[1], sys.argv[2]
data = {}
if os.path.exists(path):
    with open(path) as f:
        data = json.load(f)
data.setdefault("mcpServers", {})["dexed"] = {"command": binary, "args": []}
with open(path, "w") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
PY
    echo "   Redémarrer l'app Claude pour charger les outils."
else
    echo
    echo "Pour brancher Claude : relancer avec --configure-claude, ou ajouter dans $CFG :"
    echo "  \"dexed\": { \"command\": \"$MCP_BIN\", \"args\": [] }"
fi

cat <<EOF

Terminé. Reste à faire une fois dans Ardour (voir tools/dexed-mcp/README.md) :
  1. Préférences → Surfaces de contrôle → cocher « Open Sound Control (OSC) ».
  2. Fenêtre → Gestionnaire de plugins → relancer un scan pour trouver Dexed (VST3).
  3. Édition → Scripts Lua → Gestionnaire de scripts → onglet « Action Scripts »,
     emplacement n°1 → choisir « Claude Bridge ».
EOF
