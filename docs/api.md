# API de pilotage local

py-multiview peut être piloté depuis le réseau local : [py-livedeck](https://github.com/lfpoulain/py-livedeck), Bitfocus Companion, un boîtier domotique, un script.

Deux contrats cohabitent :

- **L'API Stream Deck (`/api/deck`)**, la même qu'orchestream-v2, commune à l'écosystème. Le serveur décrit lui-même chaque touche et pousse les changements en direct : c'est celle que py-livedeck doit utiliser.
- **Les routes d'origine** (`/status`, `/sender/on`…), gardées pour les boutons existants, Companion et les scripts.

## Ouvrir l'API

**Réglages → Pilotage à distance** : activer *Ouvrir l'API locale*, choisir le port (5090 par défaut) et désactiver *Ce poste uniquement* pour la rendre joignable depuis le réseau. L'adresse exacte est affichée sous les réglages.

L'API est **fermée par défaut**.

### Jeton (facultatif)

- **Vide** : l'API est ouverte, comme les autres services de l'écosystème. À n'ouvrir que sur un réseau de confiance.
- **Rempli** (bouton *Générer*) : **toutes** les routes, y compris les routes d'origine, exigent l'en-tête `Authorization: Bearer <jeton>`. Un jeton placé dans l'URL n'est jamais lu. Sans le bon jeton : `401 INVALID_TOKEN`.

## API Stream Deck (`/api/deck`)

```
GET  /api/deck/state                     -> état complet, avec commands et keys
GET  /api/deck/events                    -> text/event-stream : state, puis patch
GET  /api/deck/commands                  -> commandes, avec les valeurs de leurs arguments
POST /api/deck/commands/<id>  { "args": { "<nom>": "<valeur>" } }  -> { ok, message, data }
```

Un pupitre n'a besoin que de deux appels : **`/api/deck/events`** pour dessiner les touches et **`POST /api/deck/commands/<id>`** pour les lancer, avec la commande et les arguments que donne la touche.

### Touches

`keys` décrit chaque touche, prête à dessiner :

```json
{
  "id": "stream.toggle:stream-ab12",
  "group": "Flux",
  "command": "stream.toggle",
  "args": { "stream": "stream-ab12" },
  "title": "Caméra 1",
  "subtitle": "Actif",
  "active": true,
  "tone": "success",
  "available": true,
  "reason": null
}
```

| Touche (`id`) | Rubrique | Ce qu'elle affiche |
|---|---|---|
| `reception.toggle` | Réception | « 2/3 actifs » ou « Arrêtée » ; grisée si aucun flux n'a d'écran |
| `stream.toggle:<flux>` | Flux | Nom du flux, son état ; grisée « Aucun écran assigné. » |
| `stream.muteToggle:<flux>` | Son des flux | « Son coupé » (allumée, rouge) ou « Son actif » |
| `sender.toggle` | Émission | État de l'émission OMT, ton `live` quand elle diffuse |
| `monitor.toggle` | Retour audio | Source écoutée |
| `monitor.volumeDown` / `monitor.volumeUp` | Retour audio | Volume actuel, pas de 5 ; grisée aux bornes |
| `floating.toggle` | Retour audio | Source de la fenêtre de retour |
| `route.toggle:<route>` | Routes | Nom de la route, son état |

- `id` est **stable** : c'est l'identifiant interne du flux ou de la route, qui ne change pas quand on les renomme ou les réordonne.
- `tone` : `neutral` (arrêté), `success` (actif), `warning` (démarrage, attente, relance), `danger` (erreur, son coupé), `live` (émission en cours).
- `available` / `reason` : l'appui doit marcher maintenant, sinon pourquoi. Le serveur revérifie à l'appui.

### Commandes

| Commande | Argument |
|---|---|
| `reception.start` / `.stop` / `.toggle` | |
| `sender.start` / `.stop` / `.toggle` | |
| `monitor.start` / `.stop` / `.toggle` | |
| `floating.start` / `.stop` / `.toggle` | |
| `stream.start` / `.stop` / `.toggle` | `stream` |
| `stream.mute` / `.unmute` / `.muteToggle` | `stream` |
| `route.start` / `.stop` / `.toggle` | `route` |
| `monitor.volume` | `volume` (0 à 100) |
| `monitor.volumeUp` / `.volumeDown` | |
| `monitor.source` | `source` (nom OMT, voir `GET /sources`) |

`stream` et `route` acceptent l'identifiant interne, la position (`1`, `2`…) ou le nom. `GET /api/deck/commands` donne les valeurs possibles (`args[0].values`).

Réponse : `{"ok": true, "message": "…", "data": {…le /status complet…}}`.

### Flux en direct (SSE)

```
event: state
data: {…l'état complet, comme GET /api/deck/state…}

event: patch
data: {"changed":{"sender":{…}},"keys":[{…sender.toggle…}],"removedKeys":[],"updatedAt":"…"}

: keepalive
```

- `state` à l'ouverture, puis `patch` à chaque changement. `changed` contient les sections modifiées, en entier. `keys` contient les touches modifiées ou nouvelles, à remplacer par `id`. `removedKeys` liste les touches disparues.
- L'état est relu chaque seconde, et aussitôt après une commande.
- Une ligne de commentaire part toutes les 25 s pour garder la connexion ouverte.
- Au plus 16 flux ouverts en même temps (`429 TOO_MANY_STREAMS`). `GET /api/deck/state` reste disponible en secours.

### Erreurs

`{"error": {"code", "message", "details?"}}`, avec un message écrit pour être affiché tel quel.

| HTTP | Code | Quand |
|---|---|---|
| `401` | `INVALID_TOKEN` | Jeton réglé, absent ou faux |
| `404` | `COMMAND_NOT_FOUND` | Commande inconnue |
| `404` | `ROUTE_NOT_FOUND` | Route `/api/deck/…` inconnue |
| `405` | `METHOD_NOT_ALLOWED` | Mauvais verbe HTTP |
| `409` | `REFUSED`, `NO_DISPLAY` | Refus motivé : aucun écran assigné, port déjà pris… |
| `422` | `VALIDATION_FAILED` | Argument manquant ou invalide, flux inconnu (`details["args.stream"]`) |
| `429` | `TOO_MANY_STREAMS` | Trop de flux en direct |
| `503` | `BUSY` | L'application n'a pas répondu à temps |

Un flux ou une route inconnu donne un `422`, jamais un `404 NOT_FOUND` : py-livedeck comprend ce code comme « adresse publique refusée ».

## Routes d'origine

### Lire l'état

```
GET /status
```

```json
{
  "app": "py-multiview",
  "version": "0.3.7",
  "connected": true,
  "reception": { "running": true, "active": 2, "total": 3 },
  "streams": [
    { "index": 1, "id": "stream-ab12", "name": "Caméra 1", "source": "srt",
      "running": true, "status": "active", "detail": "", "muted": false, "display": "Écran 2" }
  ],
  "sender":   { "running": false, "status": "stopped", "detail": "", "name": "Régie", "audio": "loopback", "fps": 25 },
  "monitor":  { "running": true, "status": "active", "detail": "", "source": "RÉGIE (Master)", "volume": 42 },
  "floating": { "running": false, "status": "stopped", "detail": "", "source": "RÉGIE (Master)" },
  "routes":   [ { "index": 1, "id": "route-7c", "name": "Multicast", "running": true, "status": "active" } ]
}
```

`status` vaut `stopped`, `starting`, `waiting`, `active`, `retrying` ou `error`.

La clé `connected` est là pour la surveillance « Équipements réseau » de py-livedeck, qui allume un bouton vert dès que l'application répond.

```
GET /sources
```

Les sources OMT annoncées sur le réseau : `{"sources": ["RÉGIE (Master)", …]}`.

### Commandes

Toutes en **`POST`, corps vide**. Chaque réponse est le `/status` complet après l'action.

| Route | Effet |
|---|---|
| `POST /reception/<on\|off\|toggle>` | Toute la réception |
| `POST /stream/<n>/<on\|off\|toggle>` | Un flux |
| `POST /stream/<n>/mute/<on\|off\|toggle>` | Couper le son d'un flux |
| `POST /sender/<on\|off\|toggle>` | L'émission OMT |
| `POST /monitor/<on\|off\|toggle>` | Le retour audio |
| `POST /monitor/volume/<0-100>` | Volume du retour |
| `POST /monitor/source/<nom>` | Source écoutée (nom encodé pour l'URL) |
| `POST /floating/<on\|off\|toggle>` | La fenêtre de retour |
| `POST /route/<n>/<on\|off\|toggle>` | Une route multicast |

`<n>` désigne un flux ou une route **par position** (`1`, `2`…, dans l'ordre de l'interface), **par nom** (insensible à la casse) ou par identifiant interne. Un bouton garde ainsi un libellé stable : `/stream/Caméra 1/on` marche aussi bien que `/stream/1/on`.

`toggle` est accepté, mais un pupitre qui suit l'état a intérêt à envoyer `on` ou `off` : la commande est alors idempotente et deux appuis rapides ne se croisent pas.

### Erreurs

```json
{ "error": { "code": "NOT_FOUND", "message": "flux « 42 » inconnu (flux : 1=Caméra 1, 2=Plateau)" } }
```

| Code HTTP | Quand |
|---|---|
| `401` | Jeton réglé, absent ou faux |
| `404` | Route ou flux inconnu — le message liste ce qui existe |
| `405` | Verbe HTTP incorrect (les commandes exigent `POST`) |
| `409` | Refus motivé : aucun écran assigné, écran débranché, port déjà pris… |
| `422` | Valeur invalide (mode inconnu, volume hors bornes) |
| `503` | L'application n'a pas répondu à temps |

### Exemple py-livedeck (requête simple)

Action **Requête HTTP / curl** :

| Champ | Valeur |
|---|---|
| Méthode | `POST` |
| URL | `http://192.168.1.42:5090/sender/on` |
| Corps | *(vide)* |

Pour un bouton bascule, mettre `/sender/on` dans les actions et `/sender/off` dans les actions *off*.

Pour l'état du bouton, un retour **API JSON** sur `http://192.168.1.42:5090/status` avec le chemin `sender.running`, opérateur *vrai*. Les flux s'adressent par position : `streams[0].running`, `streams[0].status`.

## Essayer en ligne de commande

```powershell
curl http://127.0.0.1:5090/api/deck/state
curl -N http://127.0.0.1:5090/api/deck/events
curl -X POST http://127.0.0.1:5090/api/deck/commands/stream.toggle -H "Content-Type: application/json" -d '{\"args\":{\"stream\":\"1\"}}'
curl -X POST http://127.0.0.1:5090/monitor/volume/40
curl -H "Authorization: Bearer <jeton>" http://127.0.0.1:5090/status
```
