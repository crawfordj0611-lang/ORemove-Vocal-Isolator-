; Inno Setup script for the NVIDIA GPU (CUDA) edition. NVIDIA only: it warns
; before installing on a PC without an NVIDIA graphics card.
; Installs per-user (no admin needed) to %LOCALAPPDATA%\Programs, so the app can
; keep downloaded models and settings next to itself. The output is split into
; parts under GitHub's 2 GB per-file limit: Setup.exe plus Setup-1.bin, -2.bin...
; Users only download Setup.exe: it downloads the .bin parts from the same
; GitHub release (checking each against the SHA-256 list in Setup-parts.txt,
; written by the build), unless they're already next to it.
; Build:  set APP_VERSION=1.1.0  &&  ISCC packaging\installer.iss

#define AppName "ORemove"
#define AppVersion GetEnv("APP_VERSION")
#define OutputName "ORemove-NVIDIA-GPU-Setup"
#define PartsUrl "https://github.com/crawfordj0611-lang/ORemove-Vocal-Isolator-/releases/download/v" + AppVersion + "/"

[Setup]
; Keep this AppId the same in every release, so a new version upgrades the
; installed one in place (keeping downloaded models and settings).
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
SetupIconFile=packaging\icon.ico
; Tell Windows to refresh its icon cache, so upgrades from versions that had
; the default Python icon show the ORemove icon straight away.
ChangesAssociations=yes
SourceDir=..
OutputDir=installer-out
OutputBaseFilename={#OutputName}
Compression=lzma2/ultra64
LZMAUseSeparateProcess=yes
LZMANumBlockThreads=4
DiskSpanning=yes
DiskSliceSize=1900000000
LicenseFile=LICENSE

[Messages]
WelcomeLabel2=This will install [name/ver] on your computer.%n%nTHIS EDITION REQUIRES AN NVIDIA GRAPHICS CARD (GeForce GTX 900 series or newer, with a current driver). AMD and Intel graphics are not supported. For a PC without an NVIDIA card, use the CPU edition (ORemove-Windows-CPU.zip) instead.%n%nIt is recommended that you close all other applications before continuing.

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"

[Files]
Source: "dist\{#AppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Microsoft's WebView2 installer, used only if the PC doesn't have the runtime
; the interface needs (it's built into Windows 11 and most Windows 10 PCs).
Source: "packaging\MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: not WebView2Installed

[Icons]
; Same AppUserModelID as the app sets for itself, so the taskbar shows ORemove's icon.
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppName}.exe"; IconFilename: "{app}\_internal\icon.ico"; AppUserModelID: "newmoneymadeit.ORemove"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppName}.exe"; IconFilename: "{app}\_internal\icon.ico"; AppUserModelID: "newmoneymadeit.ORemove"; Tasks: desktopicon

[Run]
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "Installing the Microsoft Edge WebView2 Runtime..."; Flags: waituntilterminated; Check: not WebView2Installed
Filename: "{app}\{#AppName}.exe"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Models downloaded on first use, yt-dlp updates and saved settings.
Type: filesandordirs; Name: "{app}\models"
Type: filesandordirs; Name: "{app}\yt-dlp"
Type: files; Name: "{app}\settings.json"
Type: files; Name: "{app}\history.json"

[Code]
var
  DownloadPage: TDownloadWizardPage;
  PartsReady: Boolean;
  DownloadedParts: TStringList;  { parts this setup downloaded, removed after installing }

{ Where the .bin parts are downloaded from. /PARTSURL=... overrides it (used by
  the automated build test, which serves them from the build machine). }
function PartsBaseUrl(): String;
begin
  Result := ExpandConstant('{param:PARTSURL}');
  if Result = '' then
    Result := '{#PartsUrl}';
end;

{ Make sure every .bin part is next to this setup, downloading (and verifying)
  any that are missing or don't match. Returns '' or an error message. }
function EnsureParts(Interactive: Boolean): String;
var
  Lines: TArrayOfString;
  Names, Hashes, Missing: TStringList;
  I, Sp1, Sp2: Integer;
  Line, Name, Hash, Local, Temp: String;
begin
  Result := '';
  if PartsReady then
    Exit;
  Names := TStringList.Create;
  Hashes := TStringList.Create;
  Missing := TStringList.Create;
  try
    { The list of parts, with their SHA-256, published next to the setup. }
    try
      DownloadTemporaryFile(PartsBaseUrl() + '{#OutputName}-parts.txt', 'parts.txt', '', nil);
    except
      { Offline, or a release that isn't public yet: fine if the parts are already here. }
      if FileExists(ExpandConstant('{src}\{#OutputName}-1.bin')) then
        PartsReady := True
      else
        Result := 'Setup couldn''t download the rest of ORemove (' + GetExceptionMessage + ').' + #13#10 + #13#10 +
          'Check your internet connection and run setup again. Or download every ' +
          '{#OutputName} .bin file from the ORemove release page into the same folder as this setup.';
      Exit;
    end;
    if not LoadStringsFromFile(ExpandConstant('{tmp}\parts.txt'), Lines) then
    begin
      Result := 'Setup couldn''t read the list of parts to download.';
      Exit;
    end;
    for I := 0 to GetArrayLength(Lines) - 1 do
    begin
      { Each line: <name> <size> <sha256> }
      Line := Trim(Lines[I]);
      Sp1 := Pos(' ', Line);
      if Sp1 = 0 then
        Continue;
      Name := Copy(Line, 1, Sp1 - 1);
      Line := Trim(Copy(Line, Sp1 + 1, Length(Line)));
      Sp2 := Pos(' ', Line);
      Hash := Lowercase(Trim(Copy(Line, Sp2 + 1, Length(Line))));
      Names.Add(Name);
      Hashes.Add(Hash);
      Local := ExpandConstant('{src}\') + Name;
      if not (FileExists(Local) and (Lowercase(GetSHA256OfFile(Local)) = Hash)) then
        Missing.Add(IntToStr(Names.Count - 1));
    end;
    if Names.Count = 0 then
    begin
      Result := 'The list of parts to download is empty.';
      Exit;
    end;
    if Missing.Count > 0 then
    begin
      try
        if Interactive then
        begin
          DownloadPage.Clear;
          for I := 0 to Missing.Count - 1 do
            DownloadPage.Add(PartsBaseUrl() + Names[StrToInt(Missing[I])], Names[StrToInt(Missing[I])], Hashes[StrToInt(Missing[I])]);
          DownloadPage.Show;
          try
            DownloadPage.Download;
          finally
            DownloadPage.Hide;
          end;
        end
        else
          for I := 0 to Missing.Count - 1 do
            DownloadTemporaryFile(PartsBaseUrl() + Names[StrToInt(Missing[I])], Names[StrToInt(Missing[I])], Hashes[StrToInt(Missing[I])], nil);
      except
        Result := 'Setup couldn''t download the rest of ORemove (' + GetExceptionMessage + ').' + #13#10 + #13#10 +
          'Check your internet connection and run setup again.';
        Exit;
      end;
      { Setup reads the parts from its own folder: move them there. }
      for I := 0 to Missing.Count - 1 do
      begin
        Name := Names[StrToInt(Missing[I])];
        Temp := ExpandConstant('{tmp}\') + Name;
        Local := ExpandConstant('{src}\') + Name;
        if FileExists(Local) then
          DeleteFile(Local);
        if not RenameFile(Temp, Local) then
          if not FileCopy(Temp, Local, False) then
          begin
            Result := 'Setup couldn''t save its download to ' + ExpandConstant('{src}') + '.' + #13#10 +
              'Move ' + '{#OutputName}.exe to a folder you can write to (such as Downloads) and run it again.';
            Exit;
          end;
        DeleteFile(Temp);
        DownloadedParts.Add(Local);
      end;
    end;
    PartsReady := True;
  finally
    Names.Free;
    Hashes.Free;
    Missing.Free;
  end;
end;

procedure InitializeWizard();
begin
  DownloadPage := CreateDownloadPage(SetupMessage(msgWizardPreparing),
    'Downloading ORemove (about 2.8 GB). This only happens once per version.', nil);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Error: String;
begin
  Result := True;
  if CurPageID = wpReady then
  begin
    Error := EnsureParts(True);
    if Error <> '' then
    begin
      SuppressibleMsgBox(Error, mbError, MB_OK, IDOK);
      Result := False;
    end;
  end;
end;

{ Silent installs don't show the Ready page, so download here instead. }
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := EnsureParts(not WizardSilent);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  I: Integer;
begin
  { The downloaded parts aren't needed once installed: free the space. }
  if CurStep = ssDone then
    for I := 0 to DownloadedParts.Count - 1 do
      DeleteFile(DownloadedParts[I]);
end;

{ True if the Microsoft Edge WebView2 Runtime is installed, machine-wide or
  for the current user. }
function WebView2Installed(): Boolean;
var
  Version: String;
begin
  Result :=
    (RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0'))
    or (RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0'));
end;

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
  DownloadedParts := TStringList.Create;
  { /SKIPGPUCHECK=1 skips the warning (used by the automated build test). }
  if ExpandConstant('{param:SKIPGPUCHECK|0}') = '1' then
    Exit;
  if not HasNvidiaGpu() then
    { SuppressibleMsgBox (not MsgBox) so a silent install with /SUPPRESSMSGBOXES
      gets the default answer, No, instead of waiting for a click. }
    Result := SuppressibleMsgBox(
      'No NVIDIA graphics card was found on this PC.' + #13#10 + #13#10 +
      'This edition of ORemove only supports NVIDIA GPUs. AMD and Intel ' +
      'graphics are not supported. Without an NVIDIA card it runs on the processor, ' +
      'which is much slower, and the download is far larger than you need.' + #13#10 + #13#10 +
      'Use the CPU edition (ORemove-Windows-CPU.zip) instead.' + #13#10 + #13#10 +
      'Install this NVIDIA edition anyway?',
      mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES;
end;
