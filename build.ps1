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
# The frozen sidecar is CPU-only by design. The optional GPU stack (torch,
# torch-directml, openai-whisper) may be present in the venv for local "gpu"-
# provider use, but it must NOT be bundled (it's ~GBs and the "gpu" provider
# runs via the dev venv, not the frozen exe). directml.py imports it lazily, so
# excluding it is safe: a frozen install just uses the CPU faster-whisper path.
& $venvPy -m PyInstaller --onefile --name murmur-sidecar `
    --distpath (Join-Path $sidecar 'dist') `
    --workpath (Join-Path $sidecar 'build_pyi') `
    --specpath (Join-Path $sidecar 'build_pyi') `
    --collect-all faster_whisper --collect-all ctranslate2 `
    --collect-all onnxruntime --collect-all tokenizers `
    --exclude-module torch --exclude-module torchvision `
    --exclude-module torch_directml --exclude-module whisper `
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
# Privacy: Rust bakes absolute source paths (cargo registry, workspace) into the
# binary for panic locations — these live under the build user's home and would
# leak the Windows username in a public release. Remap the home prefix to a
# generic placeholder so the shipped exe carries no username. We read it from
# $env:USERPROFILE (never hardcode it) so THIS script stays free of personal
# info too. Release-only: keeps debug rebuilds fast (RUSTFLAGS changes bust the
# build cache).
if ($Release) {
    $env:RUSTFLAGS = "--remap-path-prefix=$env:USERPROFILE=C:\Users\user"
}
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
