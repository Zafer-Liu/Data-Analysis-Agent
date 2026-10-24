; Business Analytics Agent — PyInstaller onedir wrapper (P4)
; Prefer packaging/build_windows.ps1. The fallback path supports manual IDE
; compilation after the documented package-dist onedir build.

#define AppName "Business Analytics Agent"
#ifndef AppVersion
  #define AppVersion "1.2.0"
#endif
#define AppPublisher "Zafer Liu"
#define AppURL "https://github.com/Zafer-Liu/Data-Analysis-Agent"
#define AppExeName "BusinessAnalyticsAgent.exe"
#ifndef OnedirSource
  #define OnedirSource "..\build\w\pyinstaller-dist\BusinessAnalyticsAgent"
#endif
#ifndef InstallerOutputDir
  #define InstallerOutputDir "..\build\w\installer"
#endif
#ifndef IconFilePath
  #define IconFilePath "icon.ico"
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
SetupIconFile={#IconFilePath}
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

[Code]
const
  WebView2ClientId = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  WebView2BootstrapperUrl = 'https://go.microsoft.com/fwlink/p/?LinkId=2124703';

function IsUsableWebView2Version(const Version: String): Boolean;
begin
  Result := (Version <> '') and (Version <> '0.0.0.0');
end;

function IsWebView2RuntimeInstalled: Boolean;
var
  Version: String;
  ClientKey: String;
begin
  ClientKey := 'Software\Microsoft\EdgeUpdate\Clients\' + WebView2ClientId;
  Result :=
    (RegQueryStringValue(HKLM32, ClientKey, 'pv', Version) and
      IsUsableWebView2Version(Version)) or
    (RegQueryStringValue(HKCU, ClientKey, 'pv', Version) and
      IsUsableWebView2Version(Version));
end;

function OnWebView2DownloadProgress(
  const Url, FileName: String; const Progress, ProgressMax: Int64): Boolean;
begin
  if ProgressMax > 0 then
    WizardForm.StatusLabel.Caption := Format(
      '正在下载 Microsoft WebView2 运行时（%d%%）...', [Progress * 100 div ProgressMax])
  else
    WizardForm.StatusLabel.Caption := '正在下载 Microsoft WebView2 运行时...';
  Result := True;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Bootstrapper: String;
  ResultCode: Integer;
begin
  Result := '';
  if IsWebView2RuntimeInstalled then
    Exit;

  try
    DownloadTemporaryFile(
      WebView2BootstrapperUrl,
      'MicrosoftEdgeWebview2Setup.exe',
      '',
      @OnWebView2DownloadProgress);
    Bootstrapper := ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe');
    if (not Exec(Bootstrapper, '/silent /install', '', SW_HIDE,
      ewWaitUntilTerminated, ResultCode)) or (ResultCode <> 0) then
      Result := Format(
        'Microsoft WebView2 运行时安装失败（退出代码 %d）。请检查网络后重试。', [ResultCode]);
  except
    Result := '无法下载 Microsoft WebView2 运行时：' + GetExceptionMessage;
  end;
end;
