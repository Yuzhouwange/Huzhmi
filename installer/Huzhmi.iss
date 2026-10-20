; Huzhmi 安装包（Inno Setup 6）
; 编译：python build_installer.py  或  ISCC.exe installer\Huzhmi.iss

#define MyAppName "Huzhmi"
#define MyAppVersion "1.0"
#define MyAppPublisher "Huzhmi"
#define MyAppExeName "Huzhmi.exe"
#define MyAppId "{{7C2E5B1A-3F4D-4A8B-9C6E-2D1F0A5B7C93}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\Huzhmi
DefaultGroupName=Huzhmi
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Huzhmi-Setup-{#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName=Huzhmi
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: unchecked

[Files]
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\data\tools\ffmpeg.exe"; DestDir: "{app}\data\tools"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion isreadme

[Icons]
Name: "{group}\Huzhmi"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 Huzhmi"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Huzhmi"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 Huzhmi"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\data"
