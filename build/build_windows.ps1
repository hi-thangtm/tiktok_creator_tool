$ErrorActionPreference = "Stop"

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptRoot "..")
$SpecPath = Join-Path $ProjectRoot "TikTokCreatorTool.windows.spec"
$DistPath = Join-Path $ProjectRoot "dist\windows"
$WorkPath = Join-Path $ProjectRoot "build\pyinstaller\windows"
$ExpectedExe = Join-Path $DistPath "TikTokCreatorTool\TikTokCreatorTool.exe"

Set-Location $ProjectRoot

Write-Host "Project root: $ProjectRoot"
Write-Host "Spec: $SpecPath"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python was not found on PATH."
}

python --version

python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller is not installed. Install dependencies before running this script."
}

if (-not (Test-Path $SpecPath)) {
    throw "Windows PyInstaller spec not found: $SpecPath"
}

if (Test-Path $DistPath) {
    Write-Host "Removing old Windows dist: $DistPath"
    Remove-Item -Recurse -Force $DistPath
}

if (Test-Path $WorkPath) {
    Write-Host "Removing old Windows workpath: $WorkPath"
    Remove-Item -Recurse -Force $WorkPath
}

python -m PyInstaller `
    --noconfirm `
    --clean `
    --distpath "$DistPath" `
    --workpath "$WorkPath" `
    "$SpecPath"

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE."
}

if (-not (Test-Path $ExpectedExe)) {
    throw "Expected executable not found: $ExpectedExe"
}

Write-Host "Windows build complete."
Write-Host "Output EXE: $ExpectedExe"
