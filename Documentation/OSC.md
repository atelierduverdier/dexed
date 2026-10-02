# Protocole OSC de Dexed (version Atelier du Verdier)

Dexed écoute en UDP sur **127.0.0.1 uniquement**, port **9000** par défaut. Si le
port est pris (plusieurs instances dans une DAW), il prend le suivant libre
jusqu'à 9015.

| Variable d'environnement | Effet |
|---|---|
| `DEXED_OSC=0` | désactive le serveur |
| `DEXED_OSC_PORT=9100` | change le port de base |

## Convention

Le **premier argument de chaque message est un int32 : le port de réponse**.
S'il vaut plus que 0, Dexed répond sur `127.0.0.1:<port>` avec :

```
/dexed/reply  <adresse de la requête : string>  <résultat JSON : string>
```

Le JSON contient toujours `"ok": true|false`, et `"error"` en cas d'échec. Mettre
0 pour ne pas recevoir de réponse.

Les messages sont traités sur le thread de messages JUCE (le même que
l'interface). Chaque changement passe par l'hôte : l'automation, l'interface de
Dexed et le marqueur « modifié » de la DAW suivent.

## Messages

| Adresse | Arguments après le port de réponse | Réponse |
|---|---|---|
| `/dexed/ping` | — | version, port, hôte (`Standalone`, `VST3`…), programme, nom |
| `/dexed/get` | — | `voice` (156 octets : 155 DX7 + interrupteurs d'opérateurs), noms des 32 programmes, cartouche, moteur, filtre/volume |
| `/dexed/params` | — | liste des contrôles : `idx`, `label`, valeur hôte 0..1, affichage, et pour les paramètres DX7 `offset`, `max`, `value` |
| `/dexed/set` | `offset value [offset value …]` (int) | modifie des octets de la voix (0..154), bornés au maximum du paramètre ; 145..154 = nom |
| `/dexed/set_param` | `idx` (int), `valeur` (float 0..1) | n'importe quel contrôle par son index (coupure, résonance, volume, OPn SWITCH, accord…) |
| `/dexed/voice` | blob de 155 ou 156 octets | remplace toute la voix ; le 156e octet = interrupteurs (bit 5 = OP1 … bit 0 = OP6) |
| `/dexed/program` | `n` 0..31 | sélectionne un programme de la cartouche |
| `/dexed/load_cart` | `chemin absolu` [, `programme`] | charge une cartouche `.syx` DX7 32 voix |
| `/dexed/store` | `n` 0..31 [, `nom`] | range la voix courante dans la cartouche (en mémoire) |
| `/dexed/save_cart` | `chemin absolu .syx` | écrit la cartouche courante sur disque |
| `/dexed/note` | `note` [, `vélocité`=100, `durée ms`=500] | joue une note en direct |
| `/dexed/panic` | — | coupe toutes les notes |
| `/dexed/render` | `chemin absolu .wav` [, `note`=60, `vélocité`=100, `tenue ms`=1000, `total ms`=2000] | rend la voix courante hors-ligne dans un moteur privé, en WAV 24 bits stéréo ; renvoie crête et RMS |

### Disposition de la voix (155 octets)

Six blocs de 21 octets, **OP6 en premier** (offset 0), OP1 en dernier (offset 105) :

| +0..3 | +4..7 | +8 | +9 | +10 | +11 | +12 | +13 | +14 | +15 | +16 | +17 | +18 | +19 | +20 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| vitesses EG 1-4 | niveaux EG 1-4 | pivot | prof. G | prof. D | courbe G | courbe D | rate scaling | AM sens | vélocité | niveau | mode | coarse | fine | detune |

Puis : 126-129 vitesses EG de hauteur, 130-133 niveaux EG de hauteur, 134
algorithme (0-31), 135 rétroaction, 136 synchro osc, 137-142 LFO (vitesse,
délai, PMD, AMD, synchro, onde), 143 sensibilité de modulation de hauteur, 144
transposition (24 = C3), 145-154 nom.

## Exemple (Python, python-osc)

```python
from dexed_mcp.osc import DexedClient
c = DexedClient(9000)
c.set({134: 4})                      # algorithme 5
c.note(48, 100, 400)                 # C2
print(c.render("/tmp/test.wav", 48))
```

## Limites connues

- Le rendu hors-ligne utilise la fréquence d'échantillonnage du moteur en
  direct (48 kHz si Dexed n'a pas encore démarré l'audio). Les tables globales
  de msfa en dépendent.
- L'accordage SCL/KBM et MTS-ESP ne sont pas recopiés dans le moteur de rendu.
- `/dexed/store` et `/dexed/load_cart` ne passent pas par les boîtes de dialogue
  de Dexed : aucune confirmation n'est demandée.
