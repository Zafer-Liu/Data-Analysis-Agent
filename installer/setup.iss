; Business Analytics Agent — PyInstaller onedir wrapper (P4)
; Prefer packaging/build_windows.ps1. The fallback path supports manual IDE
; compilation after the documented package-dist onedir build.

#define AppName "Business Analytics Agent"
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AppPublisher "Zafer Liu"
#define AppURL "https://github.com/Zafer-Liu/Data-Analysis-Agent"
#define AppExeName "BusinessAnalyticsAgent.exe"
#ifndef OnedirSource
  #define OnedirSource "..\build\windows-package\pyinstaller-dist\BusinessAnalyticsAgent"
#endif
#ifndef InstallerOutputDir
  #define InstallerOutputDir "..\build\windows-package\installer"
#endif

[Setup]
AppId={{8F3A2C1D-4B5E-4F6A-9D7C-2E8F1A3B5C9D}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
DefaultDirName={localappdata}\Programs\BusinessAnalyticsAgent
DefaultGroupName={#AppName}
DisableDirPage=no
AllowNoIcons=yes
OutputDir={#InstallerOutputDir}
OutputBaseFilename=BusinessAnalyticsAgent-Windows-x64
SetupIconFile=icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
UninstallDisplayIcon={app}\{#AppExeName}
CloseApplications=yes
RestartApplications=no
UsePreviousAppDir=yes
UsePreviousTasks=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; Exactly one source rule: the audited PyInstaller onedir produced by P3.
Source: "{#OnedirSource}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "立即启动 {#AppName}"; Flags: nowait postinstall skipifsilent
