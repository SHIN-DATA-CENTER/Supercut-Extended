; Inno Setup script for Supercut Extended.
;
; Build it with `python tools/build_installer.py`, which fills in AppVersion from
; supercut_extended/__init__.py and points SourceDir at the PyInstaller output.
;
; Installs PER USER, into %LOCALAPPDATA%\Programs. That is not a stylistic choice:
; the in-app updater replaces files under the install directory with robocopy, and
; a Program Files install would need elevation to do it -- so self-update would fail
; for everyone who used the installer. Per-user also means no UAC prompt at install
; time. This is what VS Code and Discord do for the same reason.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\SupercutExtended"
#endif

#define AppName "Supercut Extended"
#define AppExe "SupercutExtended.exe"
#define AppCli "SupercutExtended-cli.exe"
#define AppPublisher "SHIN DATA CENTER"
#define AppUrl "https://github.com/SHIN-DATA-CENTER/Supercut-Extended"

[Setup]
; Stable across versions so an install upgrades in place instead of piling up
; separate entries in Apps & features.
AppId={{8E3F2A61-5B94-4E0D-9C77-2A1D6F4B8C33}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppUrl}
AppSupportURL={#AppUrl}/issues
AppUpdatesURL={#AppUrl}/releases
VersionInfoVersion={#AppVersion}

DefaultDirName={localappdata}\Programs\SupercutExtended
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=no
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

OutputBaseFilename=SupercutExtended-v{#AppVersion}-setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\assets\app.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}

; The updater relaunches the app after swapping files; if a copy is still running
; during install the files are locked, so offer to close it.
CloseApplications=yes
CloseApplicationsFilter=*.exe
RestartApplications=no

[Languages]
Name: "ja"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; The whole one-folder build, _internal and all. recursesubdirs is what carries
; _internal across -- without it the app installs but cannot start.
Source: "{#SourceDir}\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Written by the updater while applying an update. Anything else under {app} is
; left alone: uninstall should not take a user's own files with it.
Type: filesandordirs; Name: "{app}\_internal"
