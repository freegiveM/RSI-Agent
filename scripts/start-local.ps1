param(
  [switch]$SkipRedisCheck,
  [switch]$StartWorker
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
  Write-Host "Created .env from .env.example. Fill in credentials, then run this script again."
  exit 1
}

$mode = (Select-String -Path ".env" -Pattern '^DEPLOYMENT_MODE=' | ForEach-Object { $_.Line.Split('=', 2)[1] })
if (-not $mode) { $mode = "local" }

python -m pip install -e ".[server]" | Out-Host
if ($mode -eq "local") {
  if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker Desktop is required for DEPLOYMENT_MODE=local. Install Docker or set DEPLOYMENT_MODE=remote."
  }
  docker compose up -d redis | Out-Host
}
if (-not $SkipRedisCheck) {
  python -c "from rsi_agent.config import AppConfig; c=AppConfig.from_env(); import redis; redis.Redis.from_url(c.redis_url, protocol=c.redis_protocol, socket_connect_timeout=3, socket_timeout=5).ping(); print('Redis connection: OK')"
}
if ($StartWorker) {
  Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$PWD'; python -m rsi_agent.run_worker"
}
python -m rsi_agent.run_api
