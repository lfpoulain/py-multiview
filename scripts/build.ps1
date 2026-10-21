<#
.SYNOPSIS
    Construit py-multiview pour Windows : dossier PyInstaller, zip portable et installeur.

.DESCRIPTION
    Résultat dans dist\ :
      py-multiview\               application (dossier)
      py-multiview-portable.zip   version portable
      py-multiview-setup.exe      installeur (si Inno Setup 6 est installé)

.PARAMETER Version
    Version à graver dans le build (la CI passe major.minor.<n° de build>).
    Par défaut : celle de py_multiview/__init__.py.

.PARAMETER Python
    Interpréteur à utiliser. Par défaut : .venv si présent, sinon python du PATH.

.PARAMETER FfmpegSource
    Dossier d'un FFmpeg standard déjà installé, copié au lieu d'être téléchargé.

.EXAMPLE
    .\scripts\build.ps1
    .\scripts\build.ps1 -FfmpegSource "C:\ffmpeg\bin"
#>
param(
    [string]$Version,
    [string]$Python,
    [string]$FfmpegSource,
    [switch]$NoInstaller
)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Invoke-Checked {
    param([string]$Exe, [string[]]$Arguments)
    # Native tools log to stderr; under Windows PowerShell 5.1 + "Stop" that would abort the build.
    $ErrorActionPreference = "Continue"
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Exe $($Arguments -join ' ') a échoué (code $LASTEXITCODE)" }
}

if (-not $Python) {
    $venv = Join-Path $root ".venv\Scripts\python.exe"
    $Python = if (Test-Path $venv) { $venv } else { "python" }
}

$init = Join-Path $root "py_multiview\__init__.py"
if ($Version) {
    $text = [IO.File]::ReadAllText($init) -replace '__version__ = "[^"]+"', "__version__ = `"$Version`""
    [IO.File]::WriteAllText($init, $text, [Text.UTF8Encoding]::new($false))  # no BOM
}
$Version = (Select-String -Path $init -Pattern '__version__ = "(.+)"').Matches[0].Groups[1].Value
Write-Host "py-multiview $Version"

Step "Dépendances"
Invoke-Checked $Python @("-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", "requirements.txt", "-r", "requirements-dev.txt")

Step "FFmpeg standard"
$fetch = @{}
if ($FfmpegSource) { $fetch.Source = $FfmpegSource }
& (Join-Path $PSScriptRoot "fetch-ffmpeg.ps1") @fetch

Step "PyInstaller"
foreach ($dir in "build", "dist") {
    if (Test-Path $dir) { Remove-Item -Recurse -Force $dir }
}
Invoke-Checked $Python @("-m", "PyInstaller", "--noconfirm", "--clean", "scripts\py-multiview.spec")

Step "Zip portable"
Compress-Archive -Path "dist\py-multiview" -DestinationPath "dist\py-multiview-portable.zip" -CompressionLevel Optimal

if (-not $NoInstaller) {
    $iscc = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($iscc) {
        Step "Installeur"
        Invoke-Checked $iscc @("/Q", "/DAppVersion=$Version", "scripts\installer.iss")
    } else {
        Write-Warning "Inno Setup 6 introuvable : installeur non généré (https://jrsoftware.org/isinfo.php)."
    }
}

Step "Terminé"
Get-ChildItem dist -File | Format-Table Name, @{ Name = "Mo"; Expression = { [math]::Round($_.Length / 1MB, 1) } }
