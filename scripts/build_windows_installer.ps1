param(
    [string]$Version = "0.1.0",
    [string]$Python = "python",
    [string]$InnoSetupCompiler = "",
    [string]$SignTool = "",
    [switch]$Sign,
    [string]$CertThumbprint = "",
    [string]$CertSubject = "",
    [string]$PfxPath = "",
    [string]$PfxPassword = "",
    [string]$TimestampUrl = "http://timestamp.digicert.com"
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

$Venv = Join-Path $Root ".packaging-venv"
$PythonExe = Join-Path $Venv "Scripts\python.exe"

if (-not (Test-Path $PythonExe)) {
    & $Python -m venv $Venv
}

& $PythonExe -m pip install --upgrade pip
& $PythonExe -m pip install -r requirements.txt -r requirements-build.txt

if (Test-Path "build") {
    Remove-Item "build" -Recurse -Force
}
if (Test-Path "dist") {
    Remove-Item "dist" -Recurse -Force
}

& $PythonExe -m PyInstaller --clean --noconfirm "packaging\windows\gcu_windows.spec"

function Find-SignTool {
    if ($SignTool -and (Test-Path $SignTool)) {
        return $SignTool
    }

    $KitsRoot = "${env:ProgramFiles(x86)}\Windows Kits\10\bin"
    if (Test-Path $KitsRoot) {
        $Candidate = Get-ChildItem -Path $KitsRoot -Recurse -Filter "signtool.exe" -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match "\\x64\\signtool\.exe$" } |
            Sort-Object FullName -Descending |
            Select-Object -First 1
        if ($Candidate) {
            return $Candidate.FullName
        }
    }

    return ""
}

function Invoke-CodeSign {
    param([Parameter(Mandatory=$true)][string]$Path)

    $ResolvedSignTool = Find-SignTool
    if (-not $ResolvedSignTool) {
        throw "SignTool was not found. Install Windows SDK or pass -SignTool <path-to-signtool.exe>."
    }
    if (-not (Test-Path $Path)) {
        throw "Cannot sign missing file: $Path"
    }

    $Args = @("sign", "/fd", "sha256", "/td", "sha256", "/tr", $TimestampUrl)
    if ($PfxPath) {
        $Args += @("/f", $PfxPath)
        if ($PfxPassword) {
            $Args += @("/p", $PfxPassword)
        }
    } elseif ($CertThumbprint) {
        $Args += @("/sha1", $CertThumbprint)
    } elseif ($CertSubject) {
        $Args += @("/n", $CertSubject)
    } else {
        $Args += @("/a")
    }
    $Args += $Path

    & $ResolvedSignTool @Args
    if ($LASTEXITCODE -ne 0) {
        throw "SignTool failed for $Path"
    }
}

$ShouldSign = $Sign -or $SignTool -or $CertThumbprint -or $CertSubject -or $PfxPath
if ($ShouldSign) {
    Invoke-CodeSign (Join-Path $Root "dist\GarminConnectUploader\GarminConnectUploader.exe")
    Invoke-CodeSign (Join-Path $Root "dist\GarminConnectUploader\gcu.exe")
}

if (-not $InnoSetupCompiler) {
    $Candidates = @(
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
    )
    foreach ($Candidate in $Candidates) {
        if ($Candidate -and (Test-Path $Candidate)) {
            $InnoSetupCompiler = $Candidate
            break
        }
    }
}

if (-not $InnoSetupCompiler -or -not (Test-Path $InnoSetupCompiler)) {
    throw "Inno Setup 6 compiler was not found. Install it with: winget install JRSoftware.InnoSetup"
}

$InstallerOut = Join-Path $Root "dist\installer"
New-Item -ItemType Directory -Force -Path $InstallerOut | Out-Null

& $InnoSetupCompiler `
    "/DAppVersion=$Version" `
    "/DSourceDir=$(Join-Path $Root 'dist\GarminConnectUploader')" `
    "/DOutputDir=$InstallerOut" `
    "packaging\windows\gcu.iss"

if ($ShouldSign) {
    Invoke-CodeSign (Join-Path $InstallerOut "GarminConnectUploader-$Version-Setup.exe")
}

Write-Host ""
Write-Host "Windows installer created in: $InstallerOut"
