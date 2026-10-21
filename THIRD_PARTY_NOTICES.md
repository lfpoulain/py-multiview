# Composants tiers

py-multiview distribue les composants suivants, chacun sous sa propre licence. Ce sont des
programmes séparés, lancés comme processus indépendants.

| Composant | Emplacement | Licence | Source |
|---|---|---|---|
| FFmpeg (build OMT) | `bin/omt/` | LGPL 2.1+ / GPL selon configuration | https://ffmpeg.org |
| libomt, libomtnet, libvmx | `bin/omt/` | MIT | https://github.com/openmediatransport |
| SDL2 et dépendances MinGW (zlib, bzip2, liblzma, libiconv, winpthreads) | `bin/omt/` | zlib / BSD / LGPL / MIT | projets respectifs |
| FFmpeg « essentials » de gyan.dev | `bin/standard/` | GPL v3 | https://www.gyan.dev/ffmpeg/builds/ — sources : https://github.com/FFmpeg/FFmpeg |
| Qt 6 via PySide6 | bundle PyInstaller | LGPL v3 | https://www.qt.io / https://pypi.org/project/PySide6/ |
| screeninfo | bundle PyInstaller | MIT | https://github.com/rr-/screeninfo |
| PyAudioWPatch (PortAudio) | bundle PyInstaller | MIT | https://github.com/s0d3s/PyAudioWPatch |
| Python | bundle PyInstaller | PSF License | https://www.python.org |

Le texte de licence de FFmpeg standard est copié dans `bin/standard/LICENSE.txt` lors de son
installation par `scripts/fetch-ffmpeg.ps1`. Les binaires FFmpeg ne sont pas modifiés ; leurs
sources correspondantes sont disponibles aux adresses ci-dessus.
