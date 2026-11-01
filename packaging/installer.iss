; Inno Setup script for the NVIDIA GPU (CUDA) edition. NVIDIA only: it warns
; before installing on a PC without an NVIDIA graphics card.
; Installs per-user (no admin needed) to %LOCALAPPDATA%\Programs, so the app can
; keep downloaded models and settings next to itself. The output is split into
; parts under GitHub's 2 GB per-file limit: Setup.exe plus Setup-1.bin, -2.bin...
; Build:  set APP_VERSION=1.1.0  &&  ISCC packaging\installer.iss

#define AppName "ORemove"
#define AppVersion GetEnv("APP_VERSION")

[Setup]
; Same AppId as the earlier "YT Stem Extractor" releases, so this upgrades them in
; place (keeping downloaded models and settings) instead of installing twice.
AppId={{8F3C2A51-6B7E-4D2B-9C1E-5A4F7D3E2B10}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion} (NVIDIA GPU edition)
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
OutputBaseFilename=ORemove-NVIDIA-GPU-Setup
Compression=lzma2/fast
LZMAUseSeparateProcess=yes
LZMANumBlockThreads=4
DiskSpanning=yes
DiskSliceSize=1900000000
LicenseFile=LICENSE

[InstallDelete]
; The program was called "YT Stem Extractor" before; remove its old .exe on upgrade.
Type: files; Name: "{app}\YT Stem Extractor.exe"
Type: files; Name: "{autoprograms}\YT Stem Extractor.lnk"
Type: files; Name: "{autodesktop}\YT Stem Extractor.lnk"

[Messages]
WelcomeLabel2=This will install [name/ver] on your computer.%n%nTHIS EDITION REQUIRES AN NVIDIA GRAPHICS CARD (GeForce GTX 900 series or newer, with a current driver). AMD and Intel graphics are not supported. For a PC without an NVIDIA card, use the CPU edition (ORemove-Windows-CPU.zip) instead.%n%nIt is recommended that you close all other applications before continuing.

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

[Code]
{ True if Windows reports an NVIDIA display adapter. If the check itself
  fails, returns True so the install is never blocked by a broken query. }
function HasNvidiaGpu(): Boolean;
var
  Locator, Service, Adapters, Adapter: Variant;
  AdapterName: String;
  I: Integer;
begin
  Result := False;
  try
    Locator := CreateOleObject('WbemScripting.SWbemLocator');
    Service := Locator.ConnectServer('.', 'root\CIMV2');
    Adapters := Service.ExecQuery('SELECT Name FROM Win32_VideoController');
    for I := 0 to Adapters.Count - 1 do
    begin
      Adapter := Adapters.ItemIndex(I);
      AdapterName := Adapter.Name;
      if Pos('NVIDIA', Uppercase(AdapterName)) > 0 then
      begin
        Result := True;
        Exit;
      end;
    end;
  except
    Result := True;
  end;
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  { /SKIPGPUCHECK=1 skips the warning (used by the automated build test). }
  if ExpandConstant('{param:SKIPGPUCHECK|0}') = '1' then
    Exit;
  if not HasNvidiaGpu() then
    Result := MsgBox(
      'No NVIDIA graphics card was found on this PC.' + #13#10 + #13#10 +
      'This edition of ORemove only supports NVIDIA GPUs. AMD and Intel ' +
      'graphics are not supported. Without an NVIDIA card it runs on the processor, ' +
      'which is much slower, and the download is far larger than you need.' + #13#10 + #13#10 +
      'Use the CPU edition (ORemove-Windows-CPU.zip) instead.' + #13#10 + #13#10 +
      'Install this NVIDIA edition anyway?',
      mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
end;
