; TagPilot installer (Inno Setup 6)
#define MyAppName "TagPilot"
#define MyAppVersion "1.0"
#define MyAppExe "tagpilot.exe"
#define BuildDir "dist17\tagpilot"

[Setup]
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
; Program Files (64-bit). The app never writes here: projects go to
; Documents\TagPilot\workspace, settings/logs to %LOCALAPPDATA%\TagPilot.
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExe}
UninstallDisplayName={#MyAppName}
SetupIconFile=icon.ico
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
; close a running TagPilot before files are replaced (upgrades)
CloseApplications=yes
RestartApplications=no
OutputDir=dist17_setup
OutputBaseFilename=TagPilot_Installer

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
; the whole PyInstaller folder (tagpilot.exe + _internal, which includes base_library.zip -
; the Python core: never exclude *.zip). Only the test-run folders Logs and workspace
; at the top of the build folder are left out.
Source: "{#BuildDir}\*"; DestDir: "{app}"; Excludes: "\Logs,\workspace"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; WorkingDir: "{app}"; AppUserModelID: "TagPilot"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; WorkingDir: "{app}"; AppUserModelID: "TagPilot"; Tasks: desktopicon

[Run]
; start as the normal (non-admin) user, exactly like a Start-menu launch
Filename: "{app}\{#MyAppExe}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent runasoriginaluser

[UninstallDelete]
; only files the program could have created inside its own folder (if ever run as admin)
Type: filesandordirs; Name: "{app}\Logs"
Type: filesandordirs; Name: "{app}\workspace"
