# Développement

[← Retour au README](../README.md)

## Lancer depuis les sources

Prérequis : Windows 10/11, Python 3.10 ou plus récent (3.12 recommandé), PowerShell.

```powershell
git clone https://github.com/lfpoulain/py-multiview.git
cd py-multiview
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt -r requirements-dev.txt
.\scripts\fetch-ffmpeg.ps1
.\.venv\Scripts\python -m py_multiview
```

`bin/omt/` (FFmpeg + libomt) est versionné. `bin/standard/` ne l'est pas. `fetch-ffmpeg.ps1` télécharge le build épinglé dans [`scripts/ffmpeg-standard.json`](../scripts/ffmpeg-standard.json), ou copie un FFmpeg déjà installé :

```powershell
.\scripts\fetch-ffmpeg.ps1 -Source "C:\ffmpeg\bin"
```

Sans `bin/standard/`, un `ffmpeg` / `ffplay` du `PATH` est utilisé s'il supporte SRT.

## Tests et lint

```powershell
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\python -m ruff check .
```

Les tests n'ont besoin ni de FFmpeg ni d'écran. Les commandes FFmpeg sont construites par des fonctions pures de `ffmpeg.py`, et le moteur est testé avec un superviseur factice qui enregistre ce qui devrait tourner au lieu de lancer des processus.

## Build

```powershell
.\scripts\build.ps1
```

| Résultat dans `dist/` | |
|---|---|
| `py-multiview/` | Application en dossier : démarre instantanément, contrairement à un exe unique qui extrairait FFmpeg à chaque lancement |
| `py-multiview-portable.zip` | Ce dossier zippé |
| `py-multiview-setup.exe` | Installeur, si [Inno Setup 6](https://jrsoftware.org/isinfo.php) est installé |

| Option | |
|---|---|
| `-Version 0.3.42` | Version gravée dans l'exe et l'installeur (par défaut : celle de `py_multiview/__init__.py`) |
| `-Python <chemin>` | Interpréteur à utiliser (par défaut `.venv`, sinon `python`) |
| `-FfmpegSource <dossier>` | FFmpeg standard à copier au lieu de le télécharger |
| `-NoInstaller` | Ne pas générer l'installeur |

Fichiers de packaging : [`scripts/py-multiview.spec`](../scripts/py-multiview.spec) (PyInstaller) et [`scripts/installer.iss`](../scripts/installer.iss) (Inno Setup).

## Releases

Il n'y a rien à faire à la main. Le workflow [Build & release](../.github/workflows/release.yml) :

| Événement | Action |
|---|---|
| Pull request | Lint, tests et build. Rien n'est publié. |
| Push sur `main` | Lint, tests, build, puis publication de la release **`vX.Y.N`**, marquée *latest*. |

`X.Y` vient de `__version__` dans [`py_multiview/__init__.py`](../py_multiview/__init__.py) ; `N` est le numéro de build GitHub. Pour passer à une nouvelle version mineure ou majeure, changer `X.Y` dans ce fichier. Les notes de release sont générées à partir des commits, avec les liens de téléchargement en tête.

Les noms des fichiers publiés sont fixes (`py-multiview-setup.exe`, `py-multiview-portable.zip`). Les liens `releases/latest/download/…` du README pointent donc toujours vers la dernière version.

## Mettre à jour FFmpeg

- **Standard** : modifier `scripts/ffmpeg-standard.json` (version, URL, `sha256`). Quand l'empreinte est vide, le script affiche celle qu'il a obtenue, à reporter dans le fichier. Le cache de la CI est indexé sur ce fichier.
- **OMT** : remplacer le contenu de `bin/omt/` par un build FFmpeg compilé avec `--enable-libomt` : `ffmpeg.exe`, `ffplay.exe` et leurs DLL (`libomt`, `libomtnet`, `libvmx`, `SDL2`…).

## Conventions

- Interface et messages en français ; code, commentaires et commits techniques en anglais ou en français, selon le fichier.
- L'interface ne lance aucun processus : elle passe par `Engine`.
- Toute nouvelle option de configuration est normalisée dans `config.py`, avec une valeur par défaut sûre.
- Les lignes de commande FFmpeg se construisent dans `ffmpeg.py` et sont couvertes par un test.
