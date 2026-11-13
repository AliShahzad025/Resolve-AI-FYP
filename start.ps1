# Starts Resolve AI for local development: backend, frontend, and the fixed ngrok tunnel.
# Usage (from the project folder):  .\start.ps1
# Skip the tunnel when Shopify connect/webhooks are not needed:  .\start.ps1 -NoTunnel

param([switch]$NoTunnel)

$root = $PSScriptRoot
$ngrokUrl = 'https://unhelpful-pug-unreal.ngrok-free.dev'

Start-Process powershell -ArgumentList '-NoExit', '-Command', "cd '$root\backend'; .\.venv\Scripts\Activate.ps1; python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000"
Start-Process powershell -ArgumentList '-NoExit', '-Command', "cd '$root\frontend'; npm run dev"

if (-not $NoTunnel) {
    Start-Process powershell -ArgumentList '-NoExit', '-Command', "ngrok http 8000 --url $ngrokUrl"
}

Write-Host 'Backend:  http://127.0.0.1:8000/docs'
Write-Host 'Frontend: http://127.0.0.1:5173'
if (-not $NoTunnel) { Write-Host "Public:   $ngrokUrl" }
