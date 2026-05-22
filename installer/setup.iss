; A股K线智能分析系统 - Windows安装脚本
; 使用Inno Setup编译生成EXE安装包

#define MyAppName "A股K线智能分析系统"
#define MyAppVersion "2.0"
#define MyAppPublisher "yzhanSimilarKline"
#define MyAppURL "https://github.com/mantoufan/yzhanSimilarKline"
#define MyAppExeName "streamlit_app.py"

[Setup]
; 应用信息
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
; 输出设置
OutputDir=Output
OutputBaseFilename=A股K线智能分析系统_安装版
SetupIconFile=..\logo.png
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
; 权限
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog

; 安装界面
SetupLogging=yes
DisableWelcomePage=no
DisableDirPage=no
DisableProgramGroupPage=no
DisableReadyPage=no
DisableFinishedPage=no

; 显示进度
ShowLanguageDialog=no
LanguageDetectionMethod=none

LicenseFile=..\用户手册.md
InfoBeforeFile=安装前说明.txt
InfoAfterFile=安装后说明.txt

; 磁盘空间检查 (最小需要空间: 500MB)
ExtraDiskSpaceRequired=524288000

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce
Name: "quicklaunchicon"; Description: "{cm:CreateQuickLaunchIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
; 主程序文件
Source: "..\streamlit_app.py"; DestDir: "{app}"; Flags: ignoreversion; AfterInstall: AfterInstallProgress
Source: "..\tdx_reader.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\.env.template"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\watchlist.json"; DestDir: "{app}"; Flags: skipifsourcenotight; Flags: ignoreversion

; 配置文件
Source: "..\.env"; DestDir: "{app}"; Flags: skipifsourcenotight ignoreversion

; 缓存数据目录 - 包含用户的数据
Source: "..\cache\*"; DestDir: "{app}\cache"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\securities_cache\*"; DestDir: "{app}\securities_cache"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\search_cache\*"; DestDir: "{app}\search_cache"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\technical_cache\*"; DestDir: "{app}\technical_cache"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\weekly_cache\*"; DestDir: "{app}\weekly_cache"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\monthly_cache\*"; DestDir: "{app}\monthly_cache"; Flags: ignoreversion recursesubdirs createallsubdirs

; 示例数据(如果有)
Source: "..\strategy\*"; DestDir: "{app}\strategy"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcenotight

; 文档
Source: "..\用户手册.md"; DestDir: "{app}"; Flags: ignoreversion skipifsourcenotight
Source: "..\readme.md"; DestDir: "{app}"; Flags: ignoreversion skipifsourcenotight

; 图标
Source: "..\logo.png"; DestDir: "{app}"; Flags: ignoreversion skipifsourcenotight
Source: "..\软件图标.jpg"; DestDir: "{app}"; Flags: ignoreversion skipifsourcenotight

; 安装脚本和启动器
Source: "installer_script.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "uninstall.bat"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
Name: "{app}\venv"; Permissions: users-modify
Name: "{app}\logs"; Permissions: users-modify

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\启动程序.exe"; IconFilename: "{app}\logo.ico"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\启动程序.exe"; Tasks: desktopicon; IconFilename: "{app}\logo.ico"

[Run]
; 安装完成后直接运行环境安装程序
Filename: "{app}\installer_script.py"; Description: "安装Python环境和依赖"; Flags: shellexec postinstall waituntilidle

[Registry]
; 注册应用程序
Root: HKCU; Subkey: "Software\{#MyAppName}"; ValueType: string; ValueName: "InstallPath"; ValueData: "{app}"
Root: HKCU; Subkey: "Software\{#MyAppName}"; ValueType: string; ValueName: "Version"; ValueData: "{#MyAppVersion}"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\venv"
Type: filesandordirs; Name: "{app}\__pycache__"
Type: filesandordirs; Name: "{app}\logs"
Type: files; Name: "{app}\*.log"

[Code]
var
  ProgressPage: TOutputProgressWizardPage;

procedure AfterInstallProgress;
begin
  ProgressPage.SetProgress(0, 100);
  ProgressPage.SetText('正在安装主程序文件...', '');
  Sleep(500);
  ProgressPage.SetProgress(20, 100);
end;

procedure InitializeWizard;
begin
  ProgressPage := CreateOutputProgressPage('安装进度', '正在安装A股K线智能分析系统...');
  ProgressPage.Show;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  ifCurStep = ssPostInstall then
  begin
    ProgressPage.Hide;end;
end;

function InitializeSetup: Boolean;
var
  PythonVersion: String;
  ResultCode: Integer;
begin
  Result := True;
  
  // 检查Python是否已安装
  if not RegQueryStringValue(HKCU, 'Software\Python\PythonCore', 'InstallPath', PythonVersion) then
  begin
    if MsgBox('未检测到Python环境。'#13#13'是否希望安装程序自动下载并安装Python 3.11？'#13#13+
              '选择"是"将自动下载Python安装包'#13+
              '选择"否"需要您手动安装Python后重新运行安装程序', 
              mbConfirmation, MB_YESNO) = IDYES then
    begin
      // 下载Python安装包
      if DownloadFile('https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe', 
                      ExpandConstant('{tmp}\python_installer.exe')) then
      begin
        MsgBox('正在安装Python 3.11...', mbInformation, MB_OK);
        Exec(ExpandConstant('{tmp}\python_installer.exe'), 
             '/quiet InstallAllUsers=1 PrependPath=1 Include_test=0', 
             '', SW_SHOW, ewWaitUntilTerminated, ResultCode);
        MsgBox('Python安装完成！', mbInformation, MB_OK);
      end
      else
      begin
        MsgBox('Python下载失败，请手动安装Python 3.9或更高版本', mbError, MB_OK);
        Result := False;
      end;
    end
    else
    begin
      MsgBox('请先安装Python 3.9或更高版本后重新运行安装程序', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    if MsgBox('是否删除所有用户数据（包括缓存数据）？', mbConfirmation, MB_YESNO) = IDYES then
    begin
      DelTree(ExpandConstant('{app}'), True, True, True);
    end;
  end;
end;