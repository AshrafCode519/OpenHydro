; Inno Setup Script for OpenHydro - Modern Dynamic Download Manager
; Generates a professional Windows Setup Installer just like IDM

#define MyAppName "OpenHydro"
#define MyAppVersion "1.2.3"
#define MyAppPublisher "OpenHydro Open Source Team"
#define MyAppURL "https://github.com/AshrafCode519/OpenHydro"
#define MyAppExeName "OpenHydro.exe"

[Setup]
; Basic Application Identity
AppId={{D8F9A5C3-B427-4E90-B882-995F4A801DC2}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}

; Destination Folder (User-writable or Program Files)
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

; Output Settings
OutputDir=dist_installer
OutputBaseFilename=OpenHydro_Setup_v{#MyAppVersion}
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\assets\icon.ico

; Modern Compression (Highest ratio)
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
WizardSizePercent=105

[Languages]
Name: "arabic"; MessagesFile: "compiler:Languages\Arabic.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "startup"; Description: "تشغيل البرنامج تلقائياً مع بدء تشغيل الويندوز (في الخلفية بجانب الساعة)"; GroupDescription: "{cm:AdditionalIcons}"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "OpenHydro"; ValueData: """{app}\{#MyAppExeName}"" --headless"; Flags: uninsdeletevalue; Tasks: startup

[Files]
; Copy all bundled binaries and assets from PyInstaller dist folder
Source: "dist\OpenHydro\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "assets\icon.ico"; DestDir: "{app}\assets"; Flags: ignoreversion
Source: "assets\icon.png"; DestDir: "{app}\assets"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\assets\icon.ico"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\assets\icon.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

