# Guide d'utilisation

[← Retour au README](../README.md)

- [Installation](#installation)
- [L'interface](#linterface)
- [Recevoir un flux SRT](#recevoir-un-flux-srt)
- [Recevoir une source OMT](#recevoir-une-source-omt)
- [Un flux sur plusieurs écrans : le routage multicast](#un-flux-sur-plusieurs-écrans--le-routage-multicast)
- [Émettre un écran en OMT](#émettre-un-écran-en-omt)
- [Écouter le retour du master](#écouter-le-retour-du-master)
- [Préférences](#préférences)
- [Réseau et pare-feu](#réseau-et-pare-feu)
- [Dépannage](#dépannage)
- [Fichiers](#fichiers)

## Installation

Télécharger l'une des deux versions depuis la [dernière release](https://github.com/lfpoulain/py-multiview/releases/latest) :

- **Installeur** (`py-multiview-setup.exe`) : installe l'application pour tous les utilisateurs ou seulement pour soi, ajoute un raccourci au menu Démarrer et propose de créer les règles du pare-feu Windows (recommandé).
- **Portable** (`py-multiview-portable.zip`) : dézipper n'importe où, par exemple sur une clé USB, puis lancer `py-multiview.exe`.

Tout est inclus, FFmpeg compris. Une seule instance peut tourner à la fois : relancer l'application ramène simplement la fenêtre existante au premier plan.

## L'interface

| Zone | Rôle |
|---|---|
| **En-tête** | Version, adresse IP du poste (celle à donner à la régie), nombre de flux en direct, état de l'émission OMT, bouton **Démarrer / Arrêter la réception**. |
| **Écrans** | Écrans détectés avec leur résolution. Double-clic pour renommer. **Identifier** affiche le nom de chaque écran en grand sur l'écran lui-même. **Auto-assigner** donne un écran libre à chaque flux et crée les flux manquants. |
| **Émission OMT** | Publication d'un écran du poste sur le réseau. |
| **Retour audio OMT** | Écoute du son d'une source OMT sur ce poste, sans image. |
| **Routage multicast** | État des routes ; **Gérer** ouvre leur configuration. |
| **Flux entrants** | Une carte par flux : aperçu, état, source, écran et options d'affichage. |
| **Barre d'état** | Messages, et état des builds FFmpeg (clic pour le diagnostic). |

Chaque carte affiche un état :

| État | Signification |
|---|---|
| **Arrêté** | Le flux n'est pas démarré. |
| **Démarrage** | Le lecteur se lance. |
| **En attente** | Le lecteur est prêt mais rien n'arrive encore : la régie n'envoie pas, ou pas au bon endroit. |
| **En direct** | L'image arrive et s'affiche. |
| **Reconnexion** | La source a coupé : le lecteur se réarme tout seul. |
| **Erreur** | Le flux ne peut pas démarrer ; la raison est écrite sous la carte (écran absent, port déjà utilisé…). |

Tous les réglages sont enregistrés automatiquement. Un réglage modifié sur un flux en cours (écran, port, rotation, mode…) est appliqué immédiatement.

## Recevoir un flux SRT

1. **Ajouter un flux** (Ctrl+N). Un port libre lui est attribué (9001, 9002…).
2. Choisir l'**écran** de sortie.
3. Régler l'affichage :
   - **Adapter** : image entière, bandes noires si les proportions diffèrent ;
   - **Remplir** : plein écran, les bords sont rognés ;
   - **Étirer** : plein écran, l'image est déformée ;
   - **Rotation** 90°/180°/270° pour les écrans en portrait ;
   - **Muet** pour ne pas jouer le son.
4. **Démarrer** le flux, ou **Démarrer la réception** pour tous les flux qui ont un écran.
5. Côté régie, envoyer en **caller** vers l'adresse affichée sur la carte. Le bouton copier fournit l'URL complète.

L'application écoute en *listener* : c'est la régie qui se connecte. La **latence** doit être identique, ou supérieure, des deux côtés (120 ms par défaut).

**OBS** : *Paramètres → Sortie → Enregistrement → Type : Sortie personnalisée (FFmpeg)*, ou une sortie *Stream* de type *Custom*, avec l'URL :

```
srt://192.168.1.20:9001?mode=caller&latency=120000
```

Côté OBS et FFmpeg, `latency` est en **microsecondes** : 120000 = 120 ms.

**vMix** : *Outputs → SRT*, type **Caller**, hôte = IP du poste, port = port de la carte, latence en millisecondes.

Quand la régie arrête d'envoyer, le flux passe en *Reconnexion* puis *En attente* : il reprendra dès qu'elle renverra, sans aucune action.

## Recevoir une source OMT

1. Sur la carte, choisir la source **OMT**.
2. **Rechercher** liste les sources annoncées sur le réseau, au format `MACHINE (Nom)`. Double-clic pour en choisir une, ou saisir son nom à la main.
3. Choisir l'écran puis **Démarrer**.

Les flux OMT sont décodés par libomt : l'option de décodage GPU ne les concerne pas.

## Un flux sur plusieurs écrans : le routage multicast

Une **route** reçoit un flux SRT une seule fois et le rediffuse en UDP multicast, sans réencodage. Plusieurs cartes peuvent alors afficher le même flux.

1. **Routage multicast → Gérer → Nouvelle route**.
2. Régler le **port SRT entrant** (celui vers lequel la régie envoie) et, si besoin, le **groupe** et le **port multicast** (239.10.10.10:1234 par défaut). TTL 1 limite la diffusion au réseau local.
3. Sur chaque carte concernée, choisir la source **Route** puis la route.

La route démarre automatiquement avec le premier flux qui l'utilise. Elle peut aussi être lancée seule depuis la fenêtre de routage. Arrêter une route arrête les flux qui la lisent.

## Émettre un écran en OMT

Dans le panneau **Émission OMT** :

| Réglage | |
|---|---|
| **Écran** | Écran du poste à capturer, curseur compris. |
| **Nom** | Nom publié sur le réseau ; les récepteurs le voient sous la forme `MACHINE (Nom)`. |
| **Cadence** | 1 à 60 images par seconde. |
| **Format** | UYVY 4:2:2 (recommandé), BGRA 4:4:4 (couleurs complètes, plus de débit) ou YUV 10 bits. |
| **Audio** | **Sans son**, **Son du PC** (tout ce que joue la sortie audio par défaut de Windows) ou une **entrée** de la liste : micro, carte son, câble virtuel (VB-Audio…), bus vMix Audio. ↻ actualise la liste. |
| **Horloge de sortie** | Cadence la sortie sur l'horloge OMT plutôt que sur la capture. |

L'audio est envoyé en 48 kHz stéréo. Changer un réglage pendant l'émission la relance aussitôt avec les nouveaux paramètres.

## Écouter le retour du master

Le panneau **Retour audio OMT** lit le son d'une source OMT sur la sortie audio du poste, **sans ouvrir de fenêtre et sans décoder l'image** : de quoi garder le master de la régie à l'oreille pendant qu'on travaille, sans mobiliser un écran.

1. Saisir le nom de la source, ou cliquer sur 🔍 pour la choisir parmi celles découvertes sur le réseau.
2. Régler le **Volume** (0 à 100 %).
3. Cliquer sur **Écouter**.

Le volume et la source se changent en cours d'écoute : le retour redémarre aussitôt. Si la source disparaît, l'écoute se relance toute seule dès qu'elle revient. Le panneau est indépendant de la réception : on peut écouter le master sans afficher le moindre flux.

## Préférences

| Réglage | |
|---|---|
| **Ne jamais afficher de flux sur l'écran principal** | Réserve l'écran de travail : il n'est pas proposé dans les cartes ni utilisé par Auto-assigner. |
| **Décodage vidéo** | Processeur (recommandé, tous codecs), ou GPU NVIDIA / Intel / AMD pour soulager le processeur avec beaucoup de flux **H.264**. Seuls les décodeurs disponibles sur le poste sont proposés. |
| **Au lancement** | Démarre automatiquement la réception et/ou l'émission. Les sources ou écrans absents au démarrage sont repris dès qu'ils arrivent. |
| **Moteur FFmpeg** | Diagnostic des builds FFmpeg détectés et de leurs capacités. |
| **Fichiers** | Accès aux dossiers de configuration et de journaux ; **Réinitialiser** efface toute la configuration. |

## Réseau et pare-feu

| Usage | Protocole et ports |
|---|---|
| Réception SRT et routes | UDP entrant sur les ports configurés (9001… par défaut) |
| Lecture multicast | UDP, groupe et port de la route (239.10.10.10:1234 par défaut) |
| OMT, réception et émission | TCP 6400-6600, découverte mDNS (UDP 5353) |

- **Installeur** : l'option pare-feu crée les règles nécessaires.
- **Portable** : accepter la demande de Windows au premier démarrage d'un flux, ou autoriser manuellement `ffplay.exe` et `ffmpeg.exe` du dossier `_internal\bin\standard` et `_internal\bin\omt`.
- **Plusieurs cartes réseau** (VPN, Hyper-V, Wi-Fi + Ethernet) : le multicast utilise l'interface de l'adresse IP affichée dans l'en-tête, c'est-à-dire celle de la route par défaut.

## Dépannage

| Symptôme | Piste |
|---|---|
| Flux bloqué *en attente* | La régie envoie-t-elle en **caller** vers l'IP et le port de la carte ? Le pare-feu laisse-t-il passer l'UDP ? Le journal du flux (icône sur la carte) indique ce que reçoit le lecteur. |
| *Port SRT déjà utilisé* | Deux flux, ou un flux et une route, écoutent le même port : en changer un. |
| *Écran assigné absent* | L'écran est débranché ou en veille ; le flux reprendra seul à son retour. |
| *L'écran principal est exclu* | Le flux vise l'écran principal alors que les Préférences l'excluent. |
| Image saccadée avec beaucoup de flux | Essayer le décodage GPU (flux H.264) et le mode **Adapter**, qui ne coûte rien au processeur. |
| Source OMT introuvable | Même réseau / VLAN, mDNS autorisé, TCP 6400-6600 ouverts des deux côtés. |
| Pas de son dans l'émission | Vérifier la source **Audio** ; avec *Son du PC*, le son doit sortir sur la sortie audio par défaut de Windows. |
| Retour audio muet | La source OMT porte-t-elle bien de l'audio ? Vérifier le **Volume** et la sortie audio par défaut de Windows. Le journal du panneau liste les flux reçus. |
| *Aucun ffplay avec support SRT* | Uniquement en lançant depuis les sources sans `bin/standard` : voir [Développement](developpement.md). |

## Fichiers

| | Emplacement |
|---|---|
| Configuration | `%APPDATA%\py-multiview\config.json` |
| Journal de l'application | `%APPDATA%\py-multiview\logs\py-multiview.log` (3 fichiers de 1 Mo en rotation) |

La configuration est écrite de façon atomique. Un fichier illisible n'est jamais écrasé : il est mis de côté sous le nom `config.corrupt-<date>.json` et l'application repart d'une configuration neuve. Chaque flux, chaque route et l'émission ont en plus leur propre journal en direct, ouvert depuis l'icône correspondante.
