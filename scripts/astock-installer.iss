; Windows 当前用户安装包；由构建命令传入已经验收的完整包目录。
#ifndef SourceDir
  #error SourceDir is required
#endif
#ifndef BuildStamp
  #error BuildStamp is required
#endif
#ifndef ArtifactDir
  #error ArtifactDir is required
#endif

[Setup]
AppId={{C60FB6A5-74B8-4F97-A950-668F9F37E7E6}
AppName=A股看盘工作台
AppVersion=2026.10.21
AppVerName=A股看盘工作台 {#BuildStamp}
AppPublisher=Astock
DefaultDirName={localappdata}\Programs\AstockKanpan
DefaultGroupName=A股看盘工作台
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile={#SourceDir}\icons\installer-icon.ico
UninstallDisplayIcon={app}\AstockKanpan.exe
OutputDir={#ArtifactDir}
OutputBaseFilename=AstockKanpan-Setup-x64-{#BuildStamp}
Compression=lzma2/normal
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
AllowNoIcons=yes
CloseApplications=yes
RestartApplications=no
Uninstallable=not IsVerification
CreateUninstallRegKey=not IsVerification

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; Check: not IsVerification

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\A股看盘工作台"; Filename: "{app}\AstockKanpan.exe"; WorkingDir: "{app}"; IconFilename: "{app}\AstockKanpan.exe"; AppUserModelID: "Astock.Kanpan"; Check: not IsVerification
Name: "{autodesktop}\A股看盘工作台"; Filename: "{app}\AstockKanpan.exe"; WorkingDir: "{app}"; IconFilename: "{app}\AstockKanpan.exe"; AppUserModelID: "Astock.Kanpan"; Tasks: desktopicon; Check: not IsVerification

[Run]
Filename: "{app}\AstockKanpan.exe"; Description: "启动 A股看盘工作台"; Flags: nowait postinstall skipifsilent; Check: not IsVerification

[Code]
// 验收模式只释放文件到显式指定的隔离目录，不创建快捷方式、卸载器或注册表项。
function IsVerification: Boolean;
begin
  Result := ExpandConstant('{param:VERIFYONLY|0}') = '1';
end;
