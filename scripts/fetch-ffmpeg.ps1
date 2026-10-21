<#
.SYNOPSIS
    Installe le FFmpeg "standard" (SRT + décodeurs GPU) dans bin/standard.

.DESCRIPTION
    Le FFmpeg OMT versionné dans bin/omt n'a pas le protocole SRT. La réception
    SRT et le routage utilisent donc un second build, épinglé dans
    scripts/ffmpeg-standard.json (gyan.dev "essentials", GPL).

.PARAMETER Source
    Dossier contenant déjà ffmpeg.exe et ffplay.exe (ex. une installation locale)
    à copier au lieu de télécharger.

.PARAMETER Force
    Réinstalle même si bin/standard est déjà rempli.

.EXAMPLE
    .\scripts\fetch-ffmpeg.ps1
    .\scripts\fetch-ffmpeg.ps1 -Source "C:\ffmpeg\bin"
#>
param(
    [string]$Source,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # Invoke-WebRequest is much faster without the progress bar

$root = Split-Path $PSScriptRoot -Parent
$dest = Join-Path $root "bin\standard"
$pin = Get-Content (Join-Path $PSScriptRoot "ffmpeg-standard.json") -Raw | ConvertFrom-Json

if (-not $Force -and (Test-Path "$dest\ffmpeg.exe") -and (Test-Path "$dest\ffplay.exe")) {
    Write-Host "FFmpeg standard déjà présent dans bin\standard (utilise -Force pour le remplacer)."
    exit 0
}
New-Item -ItemType Directory -Force $dest | Out-Null

if ($Source) {
    foreach ($exe in "ffmpeg.exe", "ffplay.exe") {
        if (-not (Test-Path (Join-Path $Source $exe))) { throw "$exe introuvable dans $Source" }
        Copy-Item (Join-Path $Source $exe) $dest -Force
    }
    Write-Host "FFmpeg copié depuis $Source vers bin\standard."
    exit 0
}

$work = Join-Path ([IO.Path]::GetTempPath()) ("py-multiview-ffmpeg-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force $work | Out-Null
try {
    $zip = Join-Path $work "ffmpeg.zip"
    Write-Host "Téléchargement de FFmpeg $($pin.version) : $($pin.url)"
    Invoke-WebRequest -Uri $pin.url -OutFile $zip -UseBasicParsing

    $hash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($pin.sha256) {
        if ($hash -ne $pin.sha256.ToLowerInvariant()) {
            throw "Empreinte SHA256 inattendue : $hash (attendu $($pin.sha256))"
        }
        Write-Host "SHA256 vérifié."
    } else {
        Write-Warning "Aucune empreinte épinglée. SHA256 obtenu : $hash (à reporter dans scripts\ffmpeg-standard.json)"
    }

    Expand-Archive -Path $zip -DestinationPath $work -Force
    $bin = Get-ChildItem $work -Recurse -Filter ffplay.exe | Select-Object -First 1
    if (-not $bin) { throw "ffplay.exe absent de l'archive" }
    Copy-Item (Join-Path $bin.DirectoryName "ffmpeg.exe") $dest -Force
    Copy-Item (Join-Path $bin.DirectoryName "ffplay.exe") $dest -Force
    $license = Get-ChildItem (Split-Path $bin.DirectoryName -Parent) -Filter "LICENSE*" | Select-Object -First 1
    if ($license) { Copy-Item $license.FullName (Join-Path $dest "LICENSE.txt") -Force }
    Set-Content -Path (Join-Path $dest "VERSION.txt") -Value "FFmpeg $($pin.version) - $($pin.url)" -Encoding utf8
    Write-Host "FFmpeg $($pin.version) installé dans bin\standard."
} finally {
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}
