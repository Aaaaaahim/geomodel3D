# Create an isolated GemPy 3.x environment for GeoModel3D bridging.
# Usage: powershell -ExecutionPolicy Bypass -File packaging\install_gempy_env.ps1
#
# The main app runs on Python 3.9 while GemPy 3.x requires Python >=3.10,
# so GemPy is installed into a separate conda env and referenced via
# the GEOMODEL3D_GEMPY_PYTHON environment variable.

param(
    [string]$EnvName = "geomodel3d-gempy3",
    [string]$IndexUrl = "https://pypi.tuna.tsinghua.edu.cn/simple"
)

$ErrorActionPreference = "Stop"

Write-Host "==> creating conda env $EnvName (Python 3.11)" -ForegroundColor Cyan
conda create -n $EnvName python=3.11 pip -y

$python = "$((conda info --base))\envs\$EnvName\python.exe"
Write-Host "==> interpreter: $python" -ForegroundColor Cyan

Write-Host "==> installing GemPy 3.x (numpy backend, no torch)" -ForegroundColor Cyan
# 分两次安装：--no-deps 只作用于 gempy，避免把 pandas/pydantic/scipy 的依赖也跳过
& $python -m pip install "gempy==2026.0.3" --no-deps -i $IndexUrl
& $python -m pip install "gempy_engine==2026.0.3.post1" "pandas>=2.2" scipy pooch python-dotenv "numpy<2.3" pydantic -i $IndexUrl

Write-Host "==> verifying import" -ForegroundColor Cyan
& $python -c "import gempy; print('gempy', gempy.__version__)"

Write-Host ""
Write-Host "Done. Set the environment variable (or configure it system-wide):" -ForegroundColor Green
Write-Host "  `$env:GEOMODEL3D_GEMPY_PYTHON = `"$python`"" -ForegroundColor Yellow
Write-Host "Then start GeoModel3D; quick modeling will use the real GemPy engine." -ForegroundColor Green
