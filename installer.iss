; Inno Setup Script for DjaelYTPlaylistDWNLD
; Builds a clean, standard Windows installer (no self-extracting temp dropper heuristics)

#define MyAppName "DjaelYTPlaylistDWNLD"
#define MyAppVersion "3.1.0"
#define MyAppPublisher "djael-ml"
#define MyAppURL "https://github.com/djael-ml/DjaelYTPlaylistDWNLD"
#define MyAppExeName "DjaelYTPlaylistDWNLD.exe"

[Setup]
AppId={{8D6B4F7A-9C3E-4E91-9A2B-1E4C6F7D8A10}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=DjaelYTPlaylistDWNLD-Setup-{#MyAppVersion}
SetupIconFile=logo.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\DjaelYTPlaylistDWNLD\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
