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
- [Réglages](#réglages)
- [Réseau et pare-feu](#réseau-et-pare-feu)
- [Dépannage](#dépannage)
- [Fichiers](#fichiers)

## Installation

Télécharger l'une des deux versions depuis la [dernière release](https://github.com/lfpoulain/py-multiview/releases/latest) :

- **Installeur** (`py-multiview-setup.exe`) : installe l'application pour tous les utilisateurs ou seulement pour soi, ajoute un raccourci au menu Démarrer et propose de créer les règles du pare-feu Windows (recommandé).
- **Portable** (`py-multiview-portable.zip`) : dézipper n'importe où, par exemple sur une clé USB, puis lancer `py-multiview.exe`.

Tout est inclus, FFmpeg compris. Une seule instance peut tourner à la fois : relancer l'application ramène simplement la fenêtre existante au premier plan, même si la première est encore en train de démarrer.

**La croix réduit la fenêtre** au lieu de quitter, pour qu'un clic par réflexe ne coupe pas une émission. Pour quitter vraiment : **Ctrl+Q**, ou clic droit sur l'icône py-multiview près de l'horloge puis **Quitter** (une confirmation est demandée si quelque chose tourne). Ce comportement se désactive dans **Réglages → Général → Fenêtre**.

### Mises à jour

Au lancement, puis toutes les 6 heures, l'application regarde s'il existe une release plus récente sur GitHub. Si oui, un bouton **Mise à jour X.Y.Z** apparaît en bas de la navigation ; **Réglages → Général → Mises à jour → Rechercher maintenant** fait la même vérification à la demande.

La fenêtre de mise à jour montre les nouveautés, puis **Installer et redémarrer** télécharge la nouvelle version (empreinte SHA-256 vérifiée), ferme l'application et la relance une fois à jour. Les flux et l'émission sont coupés quelques secondes.

- **Installeur** : le nouvel installeur s'exécute sans assistant ; Windows peut demander une autorisation.
- **Portable** : les fichiers du dossier sont remplacés sur place. Si le dossier n'est pas modifiable, seule la page de la release est proposée.

**Ignorer cette version** masque le bouton jusqu'à la version suivante. Rien n'est jamais installé sans clic.

## L'interface

La fenêtre se lit de gauche à droite : une **barre de navigation** fixe, puis la page choisie, surmontée d'une **barre de titre**.

| Zone | Rôle |
|---|---|
| **Navigation** | À gauche, toujours visible. **Direct** : *Flux* et *Écrans*. **Services** : *Émission OMT*, *Retour* et *Routage*. *Réglages* en bas. Chaque entrée porte un badge d'état (`2/4` flux en direct, *En direct*, *Erreur*…), pour voir ce qui tourne sans changer de page. En haut : version et adresse IP du poste (celle à donner à la régie) ; en bas : l'état des builds FFmpeg (clic pour le diagnostic). |
| **Barre de titre** | Le nom de la page, le nombre de flux en direct et le bouton **Démarrer / Arrêter la réception**, présent sur toutes les pages. |
| **Flux · Mosaïque** | Les sorties avec leur aperçu, leur état et leur destination. Cliquer sur un aperçu pour l’agrandir. Chaque flux a ses commandes de lecture et son journal. |
| **Flux · Configuration** | Nom, source, écran et affichage de chaque flux. **Ajouter un flux** ouvre cette vue. La dernière vue choisie est mémorisée. |
| **Écrans** | Écrans détectés avec leur résolution et les flux qu'ils affichent. Double-clic (ou **Renommer**) pour renommer. **Identifier** affiche le nom de chaque écran en grand sur l'écran lui-même. On y trouve aussi **Exclure l'écran principal** et **Auto-assigner**, qui donne un écran libre à chaque flux et crée les flux manquants. |
| **Émission OMT** | Publication d'un écran du poste sur le réseau. |
| **Retour** | Écoute du son d'une source OMT sur ce poste, et petite fenêtre vidéo flottante. |
| **Routage** | Les routes multicast, leur état et leurs réglages. |
| **Réglages** | Réglages de l'application, rangés en onglets. Voir [Réglages](#réglages). |
| **Barre d'état** | Messages de l'application. |

Raccourcis : **Ctrl+1** à **Ctrl+6** ouvrent les pages dans l'ordre de la navigation, **Ctrl+N** ajoute un flux, **Ctrl+Q** quitte. L'application rouvre sur la dernière page consultée.

Dans les pages Émission OMT, Retour et Routage, chaque service a une **carte d'état** en tête : son état, son **journal** et son bouton principal (Émettre, Écouter, Ouvrir, Démarrer). Quand quelque chose ne va pas, la raison s'affiche dans la carte, en **rouge** pour une erreur, en **orange** quand le service attend quelque chose. Les réglages suivent, une ligne par réglage : ce qu'il fait à gauche, le contrôle à droite.

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

1. Page **Flux** : **Ajouter un flux** (Ctrl+N). Un port libre lui est attribué (9001, 9002…) et la vue **Configuration** s’ouvre.
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

1. Dans la vue **Configuration**, choisir la source **OMT** sur la carte.
2. **Rechercher** liste les sources annoncées sur le réseau, au format `MACHINE (Nom)`. Double-clic pour en choisir une, ou saisir son nom à la main.
3. Choisir l'écran puis **Démarrer**.

Les flux OMT sont décodés par libomt : l'option de décodage GPU ne les concerne pas.

## Un flux sur plusieurs écrans : le routage multicast

Une **route** reçoit un flux SRT une seule fois et le rediffuse en UDP multicast, sans réencodage. Plusieurs cartes peuvent alors afficher le même flux.

1. Page **Routage** → **Nouvelle route**.
2. Régler le **port SRT entrant** (celui vers lequel la régie envoie) et, si besoin, le **groupe** et le **port multicast** (239.10.10.10:1234 par défaut). TTL 1 limite la diffusion au réseau local.
3. Sur chaque carte concernée, choisir la source **Route** puis la route.

La route démarre automatiquement avec le premier flux qui l'utilise. Elle peut aussi être lancée seule depuis la page Routage. Arrêter une route arrête les flux qui la lisent.

## Émettre un écran en OMT

Page **Émission OMT** :

| Réglage | |
|---|---|
| **Écran** | Écran du poste à capturer, curseur compris. |
| **Nom** | Nom publié sur le réseau ; les récepteurs le voient sous la forme `MACHINE (Nom)`. |
| **Cadence** | 1 à 60 images par seconde. |
| **Format** | UYVY 4:2:2 (recommandé), BGRA 4:4:4 (couleurs complètes, plus de débit) ou YUV 10 bits. |
| **Audio** | Le bouton ouvre le choix du son, rangé en trois familles avec une recherche : **Son du PC** (tout ce que joue le PC, ou une sortie précise), **Un programme** (ce programme seul, ou tout le PC sauf lui), **Une entrée** (micro, carte son, câble virtuel, bus vMix Audio). **Émettre sans son** coupe l'audio. |
| **Horloge de sortie** | Carte *Avancé*. Activée par défaut. Avec FFmpeg, libomt cadence la sortie. Désactivée, FFmpeg transmet ses propres horodatages. |

L'audio est envoyé en 48 kHz stéréo. Changer un réglage pendant l'émission la relance aussitôt avec les nouveaux paramètres.

Pendant l'émission, une **barre de niveau** sous le choix du son montre ce qui part réellement : si elle bouge, la régie reçoit du son.

**L'image ne dépend jamais du son.** Si la source audio ne peut pas être capturée (périphérique débranché, programme fermé, refus de Windows), l'émission part quand même, la carte d'état affiche *⚠ Émission sans son* avec la raison, et la capture est retentée toutes les deux secondes : le son revient seul dès que la source est là.

### N'émettre que le son d'un programme

**Son de &lt;programme&gt;** ne prend que ce que joue ce logiciel : le navigateur, un lecteur vidéo, un jeu — sans les notifications Windows ni le reste du PC.

**Son du PC sauf &lt;programme&gt;** fait l'inverse, et sert surtout à une chose : si tu écoutes le retour audio sur le même poste pendant que tu émets le son du PC, le retour repart dans l'émission et boucle. Choisir *Son du PC sauf py-multiview* supprime la boucle.

La liste propose les programmes qui ont une fenêtre ouverte. Quelques points à connaître :

- on capture un **programme entier**, pas un onglet : tout le son du navigateur part ensemble ;
- un programme découpé en plusieurs processus (un navigateur) est capturé en entier, Windows suivant l'arborescence ;
- si le programme n'est pas lancé, l'image part sans son et le son arrive dès qu'il démarre ; s'il est relancé en cours d'émission, il est repris automatiquement ;
- avec *Tout le PC sauf lui*, si le programme ne tourne pas, c'est tout le son du PC qui est envoyé ;
- il faut **Windows 10 build 20348 ou plus récent**. Sur une version antérieure, l'émission le signale et il faut passer par un câble virtuel.

**Format** et **Horloge de sortie** règlent l'émission par FFmpeg, qui est le chemin par défaut. Avec **Émettre en direct par libomt** (carte *Avancé*), l'application cadence le son et la bibliothèque encode l'image.

Pour le son d'un programme, l'émetteur prévoit 100 ms au démarrage. Il envoie une trame de 20 ms à la fois et garde 80 ms en réserve pour absorber les arrivées irrégulières de Windows. La dérive de l'horloge de capture est compensée progressivement pendant l'émission.

## Écouter le retour du master

Page **Retour**, la carte **Retour audio** lit le son d'une source OMT sur la sortie audio du poste, **sans ouvrir de fenêtre et sans recevoir l'image** : l'application ne demande à la source que ses trames audio, donc la vidéo ne traverse même pas le réseau. De quoi garder le master de la régie à l'oreille pendant qu'on travaille, sans mobiliser un écran ni de bande passante.

1. Saisir le nom de la source, ou cliquer sur **Rechercher** pour la choisir parmi celles découvertes sur le réseau.
2. Régler le **Volume** (0 à 100 %).
3. Cliquer sur **Écouter**.

Le volume se règle sans interrompre le son ; changer de source relance l'écoute.

### Fenêtre flottante

La carte **Fenêtre de retour** ouvre une petite fenêtre vidéo posée sur le bureau, toujours au-dessus des autres, pour surveiller un retour sans lui consacrer un écran entier.

| Réglage | |
|---|---|
| **Source** | Laisser vide pour reprendre la source de l'écoute, ou en choisir une autre avec **Rechercher**. |
| **Taille** | Dimensions de la fenêtre en pixels. |
| **Son** | Volume propre à la fenêtre, à 0 % par défaut pour ne pas doubler l'écoute. |
| **Toujours au-dessus** | Garde la fenêtre visible par-dessus les autres applications. |

La fenêtre se déplace à la souris. Fermer la fenêtre depuis l'application ou la croix revient au même. Si la source disparaît, l'écoute se relance toute seule dès qu'elle revient. Le retour est indépendant de la réception : on peut écouter le master sans afficher le moindre flux.

## Piloter depuis un pupitre

**Réglages → Pilotage à distance** ouvre une API HTTP sur le réseau local : py-livedeck, Companion, un boîtier domotique ou un simple `curl` peuvent alors démarrer un flux, lancer l'émission, régler le retour audio ou ouvrir la fenêtre flottante.

Activer *Ouvrir l'API locale*, désactiver *Ce poste uniquement* pour y accéder depuis une autre machine. L'adresse à recopier dans le pupitre est affichée juste en dessous.

py-multiview parle la même **API Stream Deck** qu'orchestream-v2 (`/api/deck`) : il décrit lui-même ses touches — un flux, son état, sa couleur, grisé avec la raison s'il ne peut pas démarrer — et les met à jour en direct. Un nouveau flux ou une nouvelle route apparaît dans le pupitre sans rien y régler.

Exemple de bouton simple, action **Requête HTTP / curl** : méthode `POST`, URL `http://<ip-du-poste>:5090/sender/toggle`, corps vide.

Les routes complètes, le format des réponses et les codes d'erreur sont dans [l'API de pilotage local](api.md).

Le **jeton** est facultatif. Vide, l'API est ouverte à tout le réseau, comme les autres services pilotés par py-livedeck : à n'ouvrir que sur un réseau de confiance. Rempli (bouton *Générer*), chaque requête doit le porter dans l'en-tête `Authorization: Bearer <jeton>`. L'API est fermée par défaut.

## Réglages

Tout est enregistré et appliqué dès qu'on le change : il n'y a pas de bouton *Enregistrer*. Les réglages propres à une page (écran principal, moteur d'émission) restent sur cette page.

| Onglet | Réglage | |
|---|---|---|
| **Général** | **Au lancement** | Démarre automatiquement la réception et/ou l'émission. Les sources ou écrans absents au démarrage sont repris dès qu'ils arrivent. |
| | **La croix réduit la fenêtre** | Au lieu de quitter (activé par défaut). |
| | **Apparence** | Thème **clair** (par défaut) ou **sombre**, appliqué au prochain démarrage. |
| | **Mises à jour** | Recherche automatique des nouvelles versions, et vérification à la demande. Voir [Mises à jour](#mises-à-jour). |
| **Réception** | **Décodage vidéo** | Processeur (recommandé, tous codecs), ou GPU NVIDIA / Intel / AMD pour soulager le processeur avec beaucoup de flux **H.264**. Seuls les décodeurs disponibles sur le poste sont proposés. |
| **Pilotage à distance** | **API locale** | Ouverture, port, restriction à ce poste et jeton facultatif. Voir [Piloter depuis un pupitre](#piloter-depuis-un-pupitre). |
| **Système** | **Moteur FFmpeg** | Diagnostic des builds FFmpeg détectés et de leurs capacités ; **Re-détecter** après en avoir changé. |
| | **Fichiers** | Accès aux dossiers de configuration et de journaux. |
| | **Réinitialiser** | Efface toute la configuration. |

Sur les autres pages :

| Page | Réglage | |
|---|---|---|
| **Écrans** | **Exclure l'écran principal** | Réserve l'écran de travail : il n'est pas proposé dans les cartes ni utilisé par Auto-assigner. |
| **Émission OMT** | **Émettre en direct par libomt** | **Désactivé par défaut** : l'écran est capturé et publié par FFmpeg (gdigrab), le chemin éprouvé. Activé, libomt émet sans processus intermédiaire — plus léger, mais l'image est noire sur certaines machines. |

## Réseau et pare-feu

| Usage | Protocole et ports |
|---|---|
| Réception SRT et routes | UDP entrant sur les ports configurés (9001… par défaut) |
| Lecture multicast | UDP, groupe et port de la route (239.10.10.10:1234 par défaut) |
| OMT, réception et émission | TCP 6400-6600, découverte mDNS (UDP 5353) |

- **Installeur** : l'option pare-feu crée les règles nécessaires.
- **Portable** : accepter la demande de Windows au premier démarrage d'un flux, ou autoriser manuellement `ffplay.exe` et `ffmpeg.exe` du dossier `_internal\bin\standard` et `_internal\bin\omt`.
- **Plusieurs cartes réseau** (VPN, Hyper-V, Wi-Fi + Ethernet) : le multicast utilise l'interface de l'adresse IP affichée en haut de la navigation, c'est-à-dire celle de la route par défaut.

## Dépannage

| Symptôme | Piste |
|---|---|
| Flux bloqué *en attente* | La régie envoie-t-elle en **caller** vers l'IP et le port de la carte ? Le pare-feu laisse-t-il passer l'UDP ? Le journal du flux (icône sur la carte) indique ce que reçoit le lecteur. |
| *Port SRT déjà utilisé* | Deux flux, ou un flux et une route, écoutent le même port : en changer un. |
| *Écran assigné absent* | L'écran est débranché ou en veille ; le flux reprendra seul à son retour. |
| *L'écran principal est exclu* | Le flux vise l'écran principal alors que la page Écrans l'exclut. |
| Image saccadée avec beaucoup de flux | Essayer le décodage GPU (flux H.264) et le mode **Adapter**, qui ne coûte rien au processeur. |
| Source OMT introuvable | Même réseau / VLAN, mDNS autorisé, TCP 6400-6600 ouverts des deux côtés. |
| Pas de son dans l'émission | Regarder la **barre de niveau** sous le choix du son et le message de la carte Émission : il dit pourquoi la source n'est pas capturée. Avec *Tout ce que joue le PC*, le son doit sortir sur la sortie audio par défaut de Windows ; sinon choisir la sortie précise dans **Son du PC**. |
| Émission *en attente* | L'image n'est pas encore partie : ouvrir le **journal de l'émission**. Avec une entrée DirectShow (vMix Audio…), FFmpeg attend que le périphérique livre du son. |
| Retour audio muet | La source OMT porte-t-elle bien de l'audio ? Vérifier le **Volume** et la sortie audio par défaut de Windows. Le journal du retour audio liste les flux reçus. |
| Image reçue noire depuis l'émission | Ouvrir le **journal de l'émission** : il indique la zone capturée, le nombre de récepteurs connectés, et signale `L'écran capturé est entièrement noir`. Si ce message apparaît, le problème est à la capture — l'écran est réellement noir, ou son contenu est rendu par le GPU (lecteur vidéo plein écran, jeu) et GDI ne peut pas le relire. Sinon, désactiver **Émettre en direct par libomt** (Émission OMT → Avancé) pour comparer avec FFmpeg. |
| *Aucun ffplay avec support SRT* | Uniquement en lançant depuis les sources sans `bin/standard` : voir [Développement](developpement.md). |

## Fichiers

| | Emplacement |
|---|---|
| Configuration | `%APPDATA%\py-multiview\config.json` |
| Journal de l'application | `%APPDATA%\py-multiview\logs\py-multiview.log` (3 fichiers de 1 Mo en rotation) |

La configuration est écrite de façon atomique. Un fichier illisible n'est jamais écrasé : il est mis de côté sous le nom `config.corrupt-<date>.json` et l'application repart d'une configuration neuve. Chaque flux, chaque route et l'émission ont en plus leur propre journal en direct, ouvert depuis l'icône correspondante.
