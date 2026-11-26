# Builds dist\multi-ia.exe, the double-click assistant (experimental, read-only).
# Requires: python -m pip install -e ".[build]"
$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    python -m PyInstaller --onefile --console --clean --noconfirm --name multi-ia `
        --paths src --exclude-module PySide6 --specpath build src/multi_ia/launcher.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller falhou com o codigo $LASTEXITCODE" }
    Write-Output "Gerado: $(Resolve-Path dist\multi-ia.exe)"
}
finally {
    Pop-Location
}
