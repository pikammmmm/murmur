# murmur build script.
#
# Freezes the Python sidecar into a standalone exe (so end users need no Python)
# and builds the Tauri app, placing the frozen sidecar next to the built exe so
# the shell's resolve_launch() finds it.
#
#   .\build.ps1            # debug build (fast)
#   .\build.ps1 -Release   # release build + MSI installer
param([switch]$Release)
$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot
$sidecar = Join-Path $root 'sidecar'
$venvPy = Join-Path $sidecar '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) {
    throw "sidecar venv not found at $venvPy - create it: python -m venv sidecar\.venv then sidecar\.venv\Scripts\pip install -r sidecar\requirements.txt"
}

Write-Host '== Freezing Python sidecar ==' -ForegroundColor Cyan
& $venvPy -m pip install --quiet pyinstaller
& $venvPy -m PyInstaller --onefile --name murmur-sidecar `
    --distpath (Join-Path $sidecar 'dist') `
    --workpath (Join-Path $sidecar 'build_pyi') `
    --specpath (Join-Path $sidecar 'build_pyi') `
    --collect-all faster_whisper --collect-all ctranslate2 `
    --collect-all onnxruntime --collect-all tokenizers `
    (Join-Path $sidecar 'main.py')

$frozen = Join-Path $sidecar 'dist\murmur-sidecar.exe'
if (-not (Test-Path $frozen)) { throw 'sidecar freeze failed' }
Write-Host "frozen sidecar: $frozen" -ForegroundColor Green

# Stage the frozen sidecar inside src-tauri so the bundler embeds it as a
# resource (tauri.conf.json -> bundle.resources). Without this the MSI would
# install the shell with no sidecar.
$staged = Join-Path $root 'src-tauri\binaries'
New-Item -ItemType Directory -Force -Path $staged | Out-Null
Copy-Item $frozen (Join-Path $staged 'murmur-sidecar.exe') -Force

Write-Host '== Building Tauri app ==' -ForegroundColor Cyan
Push-Location (Join-Path $root 'src-tauri')
try {
    if ($Release) { cargo tauri build } else { cargo build }
} finally {
    Pop-Location
}

$target = if ($Release) { 'release' } else { 'debug' }
$exeDir = Join-Path $root "src-tauri\target\$target"
Copy-Item $frozen (Join-Path $exeDir 'murmur-sidecar.exe') -Force
Write-Host "Done. Launch: $exeDir\murmur.exe" -ForegroundColor Green
if ($Release) {
    Write-Host "Installer (MSI): $root\src-tauri\target\release\bundle\msi" -ForegroundColor Green
}
