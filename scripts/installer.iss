; Inno Setup script — compiled by scripts/build.ps1:
;   ISCC.exe /DAppVersion=0.2.0 scripts\installer.iss
; Input: dist\py-multiview\ (PyInstaller folder build). Output: dist\py-multiview-setup.exe

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "py-multiview"
#define AppExe "py-multiview.exe"
#define AppPublisher "LFPoulain"
#define AppUrl "https://github.com/lfpoulain/py-multiview"

[Setup]
AppId={{6C1E3B9A-4F52-4C7E-9B1D-5A2F7E0C8D41}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppUrl}
AppSupportURL={#AppUrl}/issues
AppUpdatesURL={#AppUrl}/releases
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=py-multiview-setup
SetupIconFile=..\img\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
LicenseFile=..\LICENSE

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "firewall"; Description: "Autoriser la réception SRT / OMT / multicast dans le pare-feu Windows"; GroupDescription: "Réseau :"; Check: IsAdminInstallMode

[Files]
Source: "..\dist\py-multiview\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""py-multiview - ffplay"" dir=in action=allow program=""{app}\_internal\bin\standard\ffplay.exe"" enable=yes"; Flags: runhidden; Tasks: firewall
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""py-multiview - ffmpeg"" dir=in action=allow program=""{app}\_internal\bin\standard\ffmpeg.exe"" enable=yes"; Flags: runhidden; Tasks: firewall
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""py-multiview - ffplay OMT"" dir=in action=allow program=""{app}\_internal\bin\omt\ffplay.exe"" enable=yes"; Flags: runhidden; Tasks: firewall
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""py-multiview - ffmpeg OMT"" dir=in action=allow program=""{app}\_internal\bin\omt\ffmpeg.exe"" enable=yes"; Flags: runhidden; Tasks: firewall
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""py-multiview - ffplay"""; Flags: runhidden; RunOnceId: "fw1"
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""py-multiview - ffmpeg"""; Flags: runhidden; RunOnceId: "fw2"
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""py-multiview - ffplay OMT"""; Flags: runhidden; RunOnceId: "fw3"
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""py-multiview - ffmpeg OMT"""; Flags: runhidden; RunOnceId: "fw4"
