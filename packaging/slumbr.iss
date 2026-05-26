; Inno Setup script — wraps the PyInstaller onedir (dist/Slumbr) into a
; single Windows installer. Build with:
;     "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\slumbr.iss
; (Install Inno Setup from https://jrsoftware.org/isinfo.php first.)
; Output: packaging\dist-installer\slumbr-setup-cpu.exe

#define AppName "Slumbr"
#define AppVersion "0.2.0"
#define AppPublisher "Sleepy Productions"

[Setup]
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\Slumbr
DefaultGroupName=Slumbr
OutputDir=dist-installer
OutputBaseFilename=slumbr-setup-cpu
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Per-user install — no admin prompt, friendlier for non-technical users.
PrivilegesRequired=lowest
SetupIconFile=..\slumbr\assets\icon.ico
WizardStyle=modern

[Tasks]
Name: "startupicon"; Description: "Start Slumbr automatically when I log in"; Flags: unchecked

[Files]
; The entire PyInstaller onedir.
Source: "..\dist\Slumbr\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Slumbr"; Filename: "{app}\Slumbr.exe"
Name: "{group}\Uninstall Slumbr"; Filename: "{uninstallexe}"
Name: "{userstartup}\Slumbr"; Filename: "{app}\Slumbr.exe"; Tasks: startupicon

[Run]
Filename: "{app}\Slumbr.exe"; Description: "Launch Slumbr now"; Flags: nowait postinstall skipifsilent
