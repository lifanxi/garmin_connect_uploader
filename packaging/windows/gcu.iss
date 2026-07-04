#define AppName "Garmin Connect Uploader"
#ifndef AppVersion
#define AppVersion "0.1.0"
#endif
#ifndef SourceDir
#define SourceDir "..\..\dist\GarminConnectUploader"
#endif
#ifndef OutputDir
#define OutputDir "..\..\dist\installer"
#endif
#ifndef IconFile
#define IconFile "..\..\assets\icons\gcu-icon.ico"
#endif
#define AppUserModelID "LiFanxi.GarminConnectUploader"

[Setup]
AppId={{7D06156D-CC8B-4325-A31F-E0979D3E0F3D}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Li Fanxi
DefaultDirName={localappdata}\Programs\Garmin Connect Uploader
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename=GarminConnectUploader-{#AppVersion}-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\assets\icons\gcu-icon.ico
SetupIconFile={#IconFile}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Garmin Connect Uploader"; Filename: "{app}\GarminConnectUploader.exe"; WorkingDir: "{app}"; IconFilename: "{app}\assets\icons\gcu-icon.ico"; AppUserModelID: "{#AppUserModelID}"
Name: "{group}\GCU Command Prompt"; Filename: "{cmd}"; Parameters: "/K ""cd /d {app} && echo gcu.exe is available in this directory"""; WorkingDir: "{app}"
Name: "{autodesktop}\Garmin Connect Uploader"; Filename: "{app}\GarminConnectUploader.exe"; WorkingDir: "{app}"; IconFilename: "{app}\assets\icons\gcu-icon.ico"; Tasks: desktopicon; AppUserModelID: "{#AppUserModelID}"

[Run]
Filename: "{app}\GarminConnectUploader.exe"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
