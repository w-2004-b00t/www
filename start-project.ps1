$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$venvPython = Join-Path $backend "venv\Scripts\python.exe"
$requirements = Join-Path $backend "requirements.txt"
$viteCache = Join-Path $frontend "node_modules\.vite"
$backendOutLog = Join-Path $root "backend-start.out.log"
$backendErrLog = Join-Path $root "backend-start.err.log"
$frontendOutLog = Join-Path $root "frontend-start.out.log"
$frontendErrLog = Join-Path $root "frontend-start.err.log"
$backendHealthUrl = "http://127.0.0.1:8001/api/health"
$backendKnowledgeGraphUrl = "http://127.0.0.1:8001/api/knowledge/graph?course_id=course_data_structure"

function Get-ListeningProcessIds([int] $port) {
  @(Get-NetTCPConnection -State Listen -LocalAddress 127.0.0.1 -LocalPort $port -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique)
}

function Test-BackendHealth {
  try {
    $response = Invoke-WebRequest -UseBasicParsing -Uri $backendHealthUrl -TimeoutSec 8
    if ($response.StatusCode -ne 200) {
      return $false
    }
    try {
      $payload = $response.Content | ConvertFrom-Json
      return $payload.code -eq 0
    } catch {
      return $true
    }
  } catch {
    return $false
  }
}

function Test-KnowledgeGraphRoute {
  try {
    $response = Invoke-WebRequest -UseBasicParsing -Uri $backendKnowledgeGraphUrl -TimeoutSec 10
    return $response.StatusCode -eq 200
  } catch {
    return $false
  }
}

function Wait-BackendHealth([int] $timeoutSeconds) {
  $deadline = (Get-Date).AddSeconds($timeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    if (Test-BackendHealth) {
      return $true
    }
    Start-Sleep -Seconds 1
  }
  return $false
}

function Show-BackendStartupFailure {
  Write-Host "Backend did not become healthy. Check logs:"
  Write-Host "  $backendOutLog"
  Write-Host "  $backendErrLog"
  if (Test-Path -LiteralPath $backendErrLog) {
    Write-Host "Last backend errors:"
    Get-Content -LiteralPath $backendErrLog -Tail 40
  }
}

if (Test-Path -LiteralPath $venvPython) {
  $python = $venvPython
  Write-Host "Using backend virtual environment: $python"

  $previousErrorActionPreference = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  & $python -c "import uvicorn, imageio_ffmpeg" *> $null
  $dependencyCheckExitCode = $LASTEXITCODE
  $ErrorActionPreference = $previousErrorActionPreference

  if ($dependencyCheckExitCode -ne 0) {
    Write-Host "Installing backend dependencies from $requirements ..."
    & $python -m pip install -r $requirements
    if ($LASTEXITCODE -ne 0) {
      throw "Failed to install backend dependencies."
    }
  }
} else {
  $python = "python"
  Write-Host "backend\venv was not found. Falling back to system Python."
}

$backendPids = Get-ListeningProcessIds 8001
if ($backendPids.Count -gt 0) {
  Write-Host "Backend port 8001 is already in use by PID(s): $($backendPids -join ', ')"
  if (-not (Wait-BackendHealth 45)) {
    throw "Port 8001 is occupied, but $backendHealthUrl is not healthy. Stop the occupying process or change the port."
  }
  if (-not (Test-KnowledgeGraphRoute)) {
    throw "Port 8001 is running an outdated backend without $backendKnowledgeGraphUrl. Restart that backend process, then run this script again."
  }
  Write-Host "Reusing healthy backend on http://127.0.0.1:8001 ..."
} else {
  Remove-Item -LiteralPath $backendOutLog, $backendErrLog -Force -ErrorAction SilentlyContinue
  Write-Host "Starting EduAgent Studio backend on http://127.0.0.1:8001 ..."
  Start-Process -FilePath $python -ArgumentList @(
    "-m", "uvicorn", "app.main:app",
    "--app-dir", $backend,
    "--host", "127.0.0.1",
    "--port", "8001"
  ) -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput $backendOutLog -RedirectStandardError $backendErrLog

  if (-not (Wait-BackendHealth 30)) {
    Show-BackendStartupFailure
    throw "Backend startup failed or timed out."
  }
  if (-not (Test-KnowledgeGraphRoute)) {
    Show-BackendStartupFailure
    throw "Backend started, but the knowledge graph route is unavailable: $backendKnowledgeGraphUrl"
  }
}

$frontendPids = Get-ListeningProcessIds 5175
if ($frontendPids.Count -gt 0) {
  Write-Host "Frontend port 5175 is already in use by PID(s): $($frontendPids -join ', ')"
  Write-Host "Reusing existing frontend on http://127.0.0.1:5175 ..."
} else {
  Remove-Item -LiteralPath $frontendOutLog, $frontendErrLog -Force -ErrorAction SilentlyContinue
  if (Test-Path -LiteralPath $viteCache) {
  Write-Host "Clearing Vite dependency cache: $viteCache"
  Remove-Item -LiteralPath $viteCache -Recurse -Force
  }

  Write-Host "Starting EduAgent Studio frontend on http://127.0.0.1:5175 ..."
  Start-Process -FilePath powershell -ArgumentList @(
    "-NoProfile",
    "-Command",
    "`$env:VITE_API_BASE_URL='http://127.0.0.1:8001/api'; npm run dev -- --host 127.0.0.1 --port 5175 --strictPort --force"
  ) -WorkingDirectory $frontend -WindowStyle Hidden -RedirectStandardOutput $frontendOutLog -RedirectStandardError $frontendErrLog
}

Start-Sleep -Seconds 4
Write-Host "Frontend: http://127.0.0.1:5175/"
Write-Host "Demo:     http://127.0.0.1:5175/demo/flow"
Write-Host "Backend:  $backendHealthUrl"
