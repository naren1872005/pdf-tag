$ErrorActionPreference = "Stop"
Write-Host "Creating Virtual Environment 'venv'..."
python -m venv venv

Write-Host "Activating Virtual Environment..."
$env:Path = "$PWD\venv\Scripts;$env:Path"

Write-Host "Upgrading pip..."
python -m pip install --upgrade pip

Write-Host "Installing dependencies from requirements.txt..."
pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { Write-Host "pip install FAILED"; exit 1 }

Write-Host "Removing old build and dist folders..."
if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
if (Test-Path "dist14") { Remove-Item -Recurse -Force "dist14" }

Write-Host "Building executable freshly using PyInstaller..."
pyinstaller --clean -y --distpath dist14 TagPilot.spec
if ($LASTEXITCODE -ne 0) { Write-Host "PyInstaller FAILED"; exit 1 }
if (-not (Test-Path "dist14\tagpilot\tagpilot.exe")) { Write-Host "tagpilot.exe missing"; exit 1 }

Write-Host "Build complete! Executable should be in the 'dist14' folder."
