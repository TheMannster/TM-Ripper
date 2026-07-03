; Inno Setup script for TM Ripper
; Builds a proper Windows installer (Setup.exe) with Start Menu +
; optional Desktop shortcuts, an uninstaller, and bundled ffmpeg.
;
; Compile with:  ISCC installer.iss   (or run build_installer.bat)

#define MyAppName "TM Ripper"
#define MyAppVersion "1.1.5"
#define MyAppPublisher "TheMannster"
#define MyAppExeName "TM Ripper.exe"

[Setup]
AppId={{9F3B6E1A-2C4D-4E7B-9A1F-5D6C8B2E7A10}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=installer_output
OutputBaseFilename=TMRipper-Setup
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
; Install per-user by default so no admin prompt is required.
PrivilegesRequiredOverridesAllowed=dialog
; Auto-close a running copy during updates so files can be replaced.
CloseApplications=yes
CloseApplicationsFilter=*.exe
AppMutex=TMRipperRunningMutex

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "vendor\ffmpeg\ffmpeg.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "vendor\ffmpeg\ffprobe.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion isreadme

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
