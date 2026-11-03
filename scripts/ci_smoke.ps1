# GeoModel3D local CI smoke script
# Usage: powershell -ExecutionPolicy Bypass -File scripts\ci_smoke.ps1
# Steps: compileall -> pytest -> benchmark -> frozen artifact self-test (if present)
param(
    [switch]$SkipBench,
    [switch]$SkipPackaging
)

$ErrorActionPreference = "Stop"
$env:QT_QPA_PLATFORM = "offscreen"
$env:GEOMODEL3D_NO_VTK = "1"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Step($name, [scriptblock]$action) {
    Write-Host "==> $name" -ForegroundColor Cyan
    & $action
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAILED: $name (exit $LASTEXITCODE)" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

Step "compileall" { python -m compileall -q geomodel3d tests }
Step "pytest" { python -m pytest -q }

if (-not $SkipBench) {
    Step "benchmark (200k cells)" { python -m geomodel3d.algorithms.benchmark --resolution 100 100 20 }
}

if (-not $SkipPackaging) {
    $exe = Join-Path $root "dist\GeoModel3D\GeoModel3D.exe"
    if (Test-Path $exe) {
        Write-Host "==> frozen artifact self-test" -ForegroundColor Cyan
        $env:GEOMODEL3D_SELF_TEST = "1"
        $process = Start-Process -FilePath $exe -PassThru
        if (-not $process.WaitForExit(120000)) {
            Stop-Process -Id $process.Id -Force
            Write-Host "FAILED: self-test timeout" -ForegroundColor Red
            exit 1
        }
        if ($process.ExitCode -ne 0) {
            Write-Host "FAILED: self-test exit=$($process.ExitCode)" -ForegroundColor Red
            exit $process.ExitCode
        }
        Remove-Item Env:\GEOMODEL3D_SELF_TEST -ErrorAction SilentlyContinue
        Write-Host "frozen artifact self-test passed (exit 0)" -ForegroundColor Green
    } else {
        Write-Host "skip packaging self-test (dist\GeoModel3D\GeoModel3D.exe not found)" -ForegroundColor Yellow
    }
}

Write-Host "all smoke checks passed" -ForegroundColor Green
