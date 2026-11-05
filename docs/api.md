# API de pilotage local

py-multiview peut être piloté depuis le réseau local : [py-livedeck](https://github.com/lfpoulain/py-livedeck), Bitfocus Companion, un boîtier domotique, un script.

L'API reprend les conventions des autres services que pilote py-livedeck (py-intercom, Tally32) : HTTP simple, JSON dans les deux sens, commandes en `POST` à corps vide, réponse qui renvoie l'état complet.

## Ouvrir l'API

**Préférences → Pilotage à distance** : cocher *Ouvrir l'API locale*, choisir le port (5090 par défaut) et décocher *Ce poste uniquement* pour la rendre joignable depuis le réseau. L'adresse exacte est affichée sous les réglages.

L'API est **fermée par défaut** et **sans mot de passe**, comme les autres services de l'écosystème : à n'ouvrir que sur un réseau de confiance.

## Lire l'état

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

## Commandes

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

## Erreurs

```json
{ "error": { "code": "NOT_FOUND", "message": "flux « 42 » inconnu (flux : 1=Caméra 1, 2=Plateau)" } }
```

| Code HTTP | Quand |
|---|---|
| `404` | Route ou flux inconnu — le message liste ce qui existe |
| `405` | Verbe HTTP incorrect (les commandes exigent `POST`) |
| `409` | Refus motivé : aucun écran assigné, écran débranché, port déjà pris… |
| `422` | Valeur invalide (mode inconnu, volume hors bornes) |
| `503` | L'application n'a pas répondu à temps |

Le message est écrit pour être affiché tel quel à l'opérateur.

## Exemple py-livedeck

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
curl http://127.0.0.1:5090/status
curl -X POST http://127.0.0.1:5090/monitor/on
curl -X POST http://127.0.0.1:5090/monitor/volume/40
```
