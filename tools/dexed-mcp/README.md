# dexed-mcp

Serveur MCP qui permet à Claude de piloter **Dexed** (synthé FM type DX7) et
**Ardour** sur ton PC.

```
Claude ──MCP──▶ dexed-mcp ──OSC──▶ Dexed (standalone ou VST3 dans Ardour)
                    │
                    ├──OSC──▶ Ardour (transport)
                    └──fichier + /access_action──▶ script Lua « Claude Bridge » dans Ardour
```

Il nécessite la version modifiée de Dexed (dépôt `atelierduverdier/dexed`,
branche `claude-osc`), qui ajoute un serveur OSC local. Voir
[`Documentation/OSC.md`](../../Documentation/OSC.md).

## Installation

Depuis la racine du dépôt Dexed :

```bash
./install-atelier.sh --configure-claude
```

Le script compile Dexed et installe VST3 → `~/.vst3`, CLAP → `~/.clap`,
application → `~/.local/bin/dexed`. Il installe aussi `dexed-mcp` avec
`uv tool`, copie le script Lua dans `~/.config/ardour*/scripts/` et déclare le
serveur dans `~/.config/Claude/claude_desktop_config.json` (une sauvegarde est
faite avant).

Redémarre ensuite l'app Claude.

## Réglages Ardour (une seule fois)

1. **OSC** : *Édition → Préférences → Surfaces de contrôle*, coche
   **Open Sound Control (OSC)**. Le port par défaut est 3819.
2. **Plugins** : *Fenêtre → Gestionnaire de plugins*, puis relance un scan pour
   que Dexed (VST3) apparaisse.
3. **Pont Lua** : *Édition → Scripts Lua → Gestionnaire de scripts*, onglet
   *Action Scripts*. Sélectionne l'emplacement **1**, clique *Ajouter/Définir* et
   choisis **Claude Bridge**. Si tu prends un autre emplacement, indique-le avec
   `DEXED_MCP_ARDOUR_SLOT`.

Les intitulés exacts des menus peuvent varier selon la langue et la version
d'Ardour.

## Outils exposés à Claude

| Dexed | |
|---|---|
| `dexed_instances` | instances joignables (ports 9000-9015) |
| `dexed_lire_voix` | résumé lisible opérateur par opérateur + détail |
| `dexed_modifier` | changer des paramètres (`op`, `param`, `valeur`, avec `ratio` en raccourci) |
| `dexed_nouvelle_voix` | créer une voix complète d'un coup |
| `dexed_ecouter` | rendu hors-ligne + analyse (enveloppe, brillance, harmoniques, inharmonicité) |
| `dexed_jouer_note` | jouer une note en direct |
| `dexed_operateurs_actifs` | solo / mute d'opérateurs |
| `dexed_filtre_volume` | filtre et volume de Dexed |
| `dexed_programme`, `dexed_liste_programmes`, `dexed_charger_cartouche`, `dexed_enregistrer` | cartouches `.syx` |
| `dexed_parametres`, `dexed_algorithmes`, `dexed_renommer`, `dexed_panique` | aide et divers |

| Ardour | |
|---|---|
| `ardour_etat` | session, tempo, pistes et plugins |
| `ardour_transport` | lecture, stop, début, fin, boucle, sauvegarde, annuler… |
| `ardour_aller_a` | se placer à une mesure |
| `ardour_tempo` | changer le tempo |
| `ardour_creer_piste` | nouvelle piste MIDI avec Dexed |
| `ardour_clip_midi` | écrire des notes et les importer dans une piste (nouvelle ou existante) |
| `ardour_lua` | Lua libre dans Ardour (outil avancé) |

## Variables d'environnement

| Variable | Défaut |
|---|---|
| `DEXED_MCP_PORT` | détection automatique |
| `DEXED_MCP_ARDOUR_PORT` | 3819 |
| `DEXED_MCP_ARDOUR_SLOT` | 1 |
| `DEXED_MCP_WORKDIR` | `~/.local/share/dexed-mcp` (rendus WAV) |
| `DEXED_MCP_ARDOUR_DIR` | `~/.local/share/dexed-mcp/ardour` (pont Lua, clips) |

## État des tests

Tests réalisés en environnement Linux sans carte son :

- **Dexed** : serveur OSC testé de bout en bout sur le standalone (Xvfb), y
  compris la lecture/écriture des paramètres, les programmes, les cartouches, le
  rendu WAV et l'analyse.
- **Serveur MCP** : testé via un vrai client MCP.
- **Ardour 8.4** (moteur Lua sans interface) : les fonctions état, tempo et
  création de piste, ainsi que le script « Claude Bridge », ont été exécutés et
  testés.

Restent **non testés** et à valider chez toi :

- le déclenchement du pont via `/access_action` dans Ardour avec son interface ;
- l'import des clips MIDI (`Editor:do_import`) ;
- la recherche du plugin Dexed après le scan ;
- le comportement de Dexed chargé en VST3 dans Ardour ;
- le son lui-même.
