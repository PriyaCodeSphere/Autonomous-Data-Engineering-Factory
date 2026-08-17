# One-command bootstrap + launch for the Autonomous Data Engineering Factory.
#
#   .\start.ps1              # sets up venv, installs deps, generates data, starts both servers
#   .\start.ps1 -SkipInstall # skip pip install (faster restart)
#   .\start.ps1 -SkipData    # skip regenerating fake data
#
# Two processes will launch:
#   - Mock DealerSalesCRM source on http://localhost:8001
#   - Agent backend + demo UI on http://localhost:8000
#
# Opens the demo in your default browser once the backend is ready.

[CmdletBinding()]
param(
  [switch]$SkipInstall,
  [switch]$SkipData
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

# --- 1. Ensure Python venv ---
$venvPy = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
  Write-Host "[setup] creating .venv ..." -ForegroundColor Cyan
  python -m venv .venv
}

if (-not $SkipInstall) {
  Write-Host "[setup] installing requirements ..." -ForegroundColor Cyan
  & $venvPy -m pip install --upgrade pip | Out-Null
  & $venvPy -m pip install -r requirements.txt
}

# --- 2. Generate mock data ---
if (-not $SkipData) {
  $dataDir = Join-Path $root "data"
  if (-not (Test-Path (Join-Path $dataDir "orders.json"))) {
    Write-Host "[data] generating DealerSalesCRM fake data (seed=42) ..." -ForegroundColor Cyan
    & $venvPy (Join-Path $root "mock_source\generate_data.py")
  } else {
    Write-Host "[data] using existing generated data (pass -SkipData:$false to regenerate)" -ForegroundColor DarkGray
  }
}

# --- 3. Launch mock source + backend as background jobs ---
Write-Host "[run] starting mock DealerSalesCRM on :8001 ..." -ForegroundColor Green
$mock = Start-Process -PassThru -NoNewWindow -FilePath $venvPy `
  -ArgumentList "-m","uvicorn","mock_source.main:app","--port","8001","--host","127.0.0.1" `
  -WorkingDirectory $root `
  -RedirectStandardOutput (Join-Path $root "mock_source.log") `
  -RedirectStandardError  (Join-Path $root "mock_source.err")

Start-Sleep -Seconds 1

Write-Host "[run] starting agent backend on :8000 ..." -ForegroundColor Green
$backend = Start-Process -PassThru -NoNewWindow -FilePath $venvPy `
  -ArgumentList "-m","uvicorn","backend.main:app","--port","8000","--host","127.0.0.1" `
  -WorkingDirectory $root `
  -RedirectStandardOutput (Join-Path $root "backend.log") `
  -RedirectStandardError  (Join-Path $root "backend.err")

Write-Host ""
Write-Host "  mock source  http://localhost:8001" -ForegroundColor Yellow
Write-Host "  demo UI      http://localhost:8000" -ForegroundColor Yellow
Write-Host ""
Write-Host "Waiting for backend to be ready ..." -ForegroundColor DarkGray

$ready = $false
for ($i = 0; $i -lt 30; $i++) {
  Start-Sleep -Milliseconds 500
  try {
    $r = Invoke-WebRequest -Uri "http://localhost:8000/api/status" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
    if ($r.StatusCode -eq 200) { $ready = $true; break }
  } catch { }
}

if ($ready) {
  Write-Host "[ok] backend up · opening browser ..." -ForegroundColor Green
  Start-Process "http://localhost:8000/"
} else {
  Write-Host "[warn] backend not responding after 15s. Check backend.err" -ForegroundColor Red
}

Write-Host ""
Write-Host "Press Ctrl+C to stop both processes (or run: Stop-Process $($mock.Id),$($backend.Id))" -ForegroundColor DarkGray
Write-Host "PIDs: mock=$($mock.Id) backend=$($backend.Id)" -ForegroundColor DarkGray

# Keep the script foreground; forward Ctrl+C to children
try {
  Wait-Process -Id $backend.Id
} finally {
  Write-Host "[stop] shutting down ..." -ForegroundColor DarkGray
  Get-Process -Id $mock.Id     -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
  Get-Process -Id $backend.Id  -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}
