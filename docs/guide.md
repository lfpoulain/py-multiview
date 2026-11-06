# Guide d'utilisation

[← Retour au README](../README.md)

- [Installation](#installation)
- [L'interface](#linterface)
- [Recevoir un flux SRT](#recevoir-un-flux-srt)
- [Recevoir une source OMT](#recevoir-une-source-omt)
- [Un flux sur plusieurs écrans : le routage multicast](#un-flux-sur-plusieurs-écrans--le-routage-multicast)
- [Émettre un écran en OMT](#émettre-un-écran-en-omt)
- [Écouter le retour du master](#écouter-le-retour-du-master)
- [Piloter depuis un pupitre](#piloter-depuis-un-pupitre)
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
| **Barre latérale** | Cinq blocs repliables, décrits ci-dessous. Cliquer sur un titre l'ouvre ou le replie ; l'état est retenu d'un lancement à l'autre. |
| **Écrans** | Écrans détectés avec leur résolution. Double-clic pour renommer. **Identifier** affiche le nom de chaque écran en grand sur l'écran lui-même. **Auto-assigner** donne un écran libre à chaque flux et crée les flux manquants. |
| **Émission OMT** | Publication d'un écran du poste sur le réseau. |
| **Retour audio** | Écoute du son d'une source OMT sur ce poste, sans image. |
| **Fenêtre de retour** | Petite fenêtre vidéo flottante sur une source OMT. |
| **Routage multicast** | État des routes ; **Gérer** ouvre leur configuration. |
| **Flux entrants** | Une carte par flux : aperçu, état, source, écran et options d'affichage. |
| **Barre d'état** | Messages, et état des builds FFmpeg (clic pour le diagnostic). |

### Les blocs repliés restent utiles

Un bloc replié garde son **bouton principal** (Émettre, Écouter, Ouvrir…) et une **ligne de résumé** sous son titre : l'écran, la cadence et le son pour l'émission, la source et le volume pour le retour. Cette ligne dit aussi ce qui ne va pas, sans qu'il faille déplier : en **rouge** pour une erreur (avec le message), en **orange** quand le bloc attend quelque chose. Le message complet apparaît en infobulle.

Chaque bloc déplié a son **Journal** en bas.

Chaque carte de flux affiche un état :

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
| **Audio** | **Sans son**, **Son du PC** (tout ce que joue la sortie audio par défaut), **Son de &lt;programme&gt;**, **Son du PC sauf &lt;programme&gt;**, ou une **entrée** de la liste : micro, carte son, câble virtuel (VB-Audio…), bus vMix Audio. ↻ actualise la liste. |
| **Horloge de sortie** | Cadence la sortie sur l'horloge OMT plutôt que sur la capture. |

L'audio est envoyé en 48 kHz stéréo. Changer un réglage pendant l'émission la relance aussitôt avec les nouveaux paramètres.

### N'émettre que le son d'un programme

**Son de &lt;programme&gt;** ne prend que ce que joue ce logiciel : le navigateur, un lecteur vidéo, un jeu — sans les notifications Windows ni le reste du PC.

**Son du PC sauf &lt;programme&gt;** fait l'inverse, et sert surtout à une chose : si tu écoutes le retour audio sur le même poste pendant que tu émets le son du PC, le retour repart dans l'émission et boucle. Choisir *Son du PC sauf py-multiview* supprime la boucle.

La liste propose les programmes qui ont une fenêtre ouverte. Quelques points à connaître :

- on capture un **programme entier**, pas un onglet : tout le son du navigateur part ensemble ;
- un programme découpé en plusieurs processus (un navigateur) est capturé en entier, Windows suivant l'arborescence ;
- si le programme n'est pas lancé au démarrage de l'émission, celle-ci le dit dans son journal et réessaie ;
- il faut **Windows 10 build 20348 ou plus récent**. Sur une version antérieure, l'émission le signale et il faut passer par un câble virtuel.

**Format** et **Horloge de sortie** sont grisés quand l'émission passe en direct par libomt, ce qui est le cas normal : la bibliothèque encode l'image elle-même. Ils ne servent qu'au repli FFmpeg, signalé par `OMT` au lieu de `OMT natif` dans la barre d'état.

## Écouter le retour du master

Le bloc **Retour audio** lit le son d'une source OMT sur la sortie audio du poste, **sans ouvrir de fenêtre et sans recevoir l'image** : l'application ne demande à la source que ses trames audio, donc la vidéo ne traverse même pas le réseau. De quoi garder le master de la régie à l'oreille pendant qu'on travaille, sans mobiliser un écran ni de bande passante.

1. Saisir le nom de la source, ou cliquer sur 🔍 pour la choisir parmi celles découvertes sur le réseau.
2. Régler le **Volume** (0 à 100 %).
3. Cliquer sur **Écouter**.

Le volume se règle sans interrompre le son ; changer de source relance l'écoute.

### Fenêtre flottante

Le bloc **Fenêtre de retour** ouvre une petite fenêtre vidéo posée sur le bureau, toujours au-dessus des autres, pour surveiller un retour sans lui consacrer un écran entier.

| Réglage | |
|---|---|
| **Source** | Laisser vide pour reprendre la source de l'écoute, ou en choisir une autre avec 🔍. |
| **Taille** | Dimensions de la fenêtre en pixels. |
| **Son** | Volume propre à la fenêtre, à 0 % par défaut pour ne pas doubler l'écoute. |
| **Toujours au-dessus** | Garde la fenêtre visible par-dessus les autres applications. |

La fenêtre se déplace à la souris. Fermer la fenêtre depuis l'application ou la croix revient au même. Si la source disparaît, l'écoute se relance toute seule dès qu'elle revient. Le panneau est indépendant de la réception : on peut écouter le master sans afficher le moindre flux.

## Piloter depuis un pupitre

**Préférences → Pilotage à distance** ouvre une API HTTP sur le réseau local : py-livedeck, Companion, un boîtier domotique ou un simple `curl` peuvent alors démarrer un flux, lancer l'émission, régler le retour audio ou ouvrir la fenêtre flottante.

Cocher *Ouvrir l'API locale*, décocher *Ce poste uniquement* pour y accéder depuis une autre machine. L'adresse à recopier dans le pupitre est affichée juste en dessous.

Exemple de bouton py-livedeck, action **Requête HTTP / curl** : méthode `POST`, URL `http://<ip-du-poste>:5090/sender/toggle`, corps vide.

Les routes complètes, le format des réponses et les codes d'erreur sont dans [l'API de pilotage local](api.md).

L'API n'a **pas de mot de passe**, comme les autres services pilotés par py-livedeck : à n'ouvrir que sur un réseau de confiance. Elle est fermée par défaut.

## Préférences

| Réglage | |
|---|---|
| **Ne jamais afficher de flux sur l'écran principal** | Réserve l'écran de travail : il n'est pas proposé dans les cartes ni utilisé par Auto-assigner. |
| **Décodage vidéo** | Processeur (recommandé, tous codecs), ou GPU NVIDIA / Intel / AMD pour soulager le processeur avec beaucoup de flux **H.264**. Seuls les décodeurs disponibles sur le poste sont proposés. |
| **Émettre en direct par libomt** | **Décoché par défaut** : l'écran est capturé et publié par FFmpeg (gdigrab), le chemin éprouvé. Coché, libomt émet sans processus intermédiaire — plus léger, mais l'image est noire sur certaines machines. |
| **Pilotage à distance** | Ouvre l'API HTTP locale, son port, et la restriction à ce poste. Voir [Piloter depuis un pupitre](#piloter-depuis-un-pupitre). |
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
| Image reçue noire depuis l'émission | Ouvrir le **journal de l'émission** : il indique la zone capturée, le nombre de récepteurs connectés, et signale `L'écran capturé est entièrement noir`. Si ce message apparaît, le problème est à la capture — l'écran est réellement noir, ou son contenu est rendu par le GPU (lecteur vidéo plein écran, jeu) et GDI ne peut pas le relire. Sinon, décocher **Émettre en direct par libomt** dans les Préférences pour comparer avec FFmpeg. |
| *Aucun ffplay avec support SRT* | Uniquement en lançant depuis les sources sans `bin/standard` : voir [Développement](developpement.md). |

## Fichiers

| | Emplacement |
|---|---|
| Configuration | `%APPDATA%\py-multiview\config.json` |
| Journal de l'application | `%APPDATA%\py-multiview\logs\py-multiview.log` (3 fichiers de 1 Mo en rotation) |

La configuration est écrite de façon atomique. Un fichier illisible n'est jamais écrasé : il est mis de côté sous le nom `config.corrupt-<date>.json` et l'application repart d'une configuration neuve. Chaque flux, chaque route et l'émission ont en plus leur propre journal en direct, ouvert depuis l'icône correspondante.
