; Inno Setup script for the GPU (CUDA) build.
; Installs per-user (no admin needed) to %LOCALAPPDATA%\Programs, so the app can
; keep downloaded models and settings next to itself. The output is split into
; parts under GitHub's 2 GB per-file limit: Setup.exe plus Setup-1.bin, -2.bin...
; Build:  set APP_VERSION=1.1.0  &&  ISCC packaging\installer.iss

#define AppName "YT Stem Extractor"
#define AppVersion GetEnv("APP_VERSION")

[Setup]
AppId={{8F3C2A51-6B7E-4D2B-9C1E-5A4F7D3E2B10}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion} (GPU)
AppPublisher=newmoneymadeit
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
UninstallDisplayIcon={app}\{#AppName}.exe
SourceDir=..
OutputDir=installer-out
OutputBaseFilename=YT-Stem-Extractor-GPU-Setup
Compression=lzma2/fast
LZMAUseSeparateProcess=yes
LZMANumBlockThreads=4
DiskSpanning=yes
DiskSliceSize=1900000000

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"

[Files]
Source: "dist\{#AppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppName}.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppName}.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppName}.exe"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Models downloaded on first use and saved settings.
Type: filesandordirs; Name: "{app}\models"
Type: files; Name: "{app}\settings.json"
