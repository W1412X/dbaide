# Build DBAide distributable on Windows (PowerShell).
# Usage:
#   .\scripts\build_package.ps1 gui
#   .\scripts\build_package.ps1 cli
#   .\scripts\build_package.ps1 wheel

param(
    [ValidateSet("gui", "cli", "wheel")]
    [string]$Target = "gui"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

Write-Host "==> DBAide packaging ($Target) on Windows"

python -m pip install -q -e ".[gui,dev]"

New-Item -ItemType Directory -Force -Path dist | Out-Null

switch ($Target) {
    "gui" {
        python -m PyInstaller packaging/pyinstaller/dbaide-gui.spec --noconfirm --clean
        Write-Host ""
        Write-Host "GUI bundle: $Root\dist\DBAide\"
        Write-Host "Run: dist\DBAide\DBAide.exe"
        Write-Host "Optional: Compress-Archive -Path dist\DBAide -DestinationPath dist\DBAide-Windows.zip"
    }
    "cli" {
        python -m PyInstaller packaging/pyinstaller/dbaide-cli.spec --noconfirm --clean
        Write-Host ""
        Write-Host "CLI binary: $Root\dist\dbaide.exe"
    }
    "wheel" {
        python -m pip install -q build
        python -m build --outdir dist
        Write-Host ""
        Write-Host "Wheel/sdist in $Root\dist\"
    }
}
