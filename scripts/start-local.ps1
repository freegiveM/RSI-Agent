param(
  [switch]$SkipRedisCheck
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
  Write-Host "Created .env from .env.example. Fill in credentials, then run this script again."
  exit 1
}

python -m pip install -e ".[server]" | Out-Host
if (-not $SkipRedisCheck) {
  python -c "from rsi_agent.config import AppConfig; c=AppConfig.from_env(); import redis; redis.Redis.from_url(c.redis_url, protocol=c.redis_protocol, socket_connect_timeout=3, socket_timeout=5).ping(); print('Redis connection: OK')"
}
python -m rsi_agent.run_api
