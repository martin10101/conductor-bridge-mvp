# test-mcp.ps1 - Simple end-to-end check for Conductor Bridge MCP + Gemini CLI

param(
    [string]$Model = "",
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptRoot

$python = Join-Path $ProjectRoot ".venv\\Scripts\\python.exe"
if (-not (Test-Path $python)) {
    throw "Python venv not found at $python. Run .\\scripts\\venv.ps1 first."
}

if ($Model) {
    $env:CONDUCTOR_BRIDGE_GEMINI_MODEL = $Model
    Write-Host "Using Gemini model: $env:CONDUCTOR_BRIDGE_GEMINI_MODEL"
} else {
    Write-Host "Using Gemini model: (default)"
}

Write-Host ""
Write-Host "1) Starting Conductor Bridge MCP server..."

$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    Write-Host "   Server already listening on port $Port (PID: $($listener.OwningProcess))."
} else {
    $logs = Join-Path $ProjectRoot "logs"
    New-Item -ItemType Directory -Force -Path $logs | Out-Null
    $stdout = Join-Path $logs "conductor-bridge.stdout.log"
    $stderr = Join-Path $logs "conductor-bridge.stderr.log"

    $proc = Start-Process `
        -FilePath $python `
        -WorkingDirectory $ProjectRoot `
        -ArgumentList @("-m", "conductor_bridge.server", "--http", "--port", "$Port") `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -PassThru `
        -WindowStyle Hidden

    Write-Host "   Started (PID: $($proc.Id))."
}

Write-Host ""
Write-Host "2) Waiting for /health..."
for ($i = 0; $i -lt 30; $i++) {
    try {
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/health" -UseBasicParsing -TimeoutSec 1
        if ($resp.StatusCode -eq 200) { break }
    } catch {}
    Start-Sleep -Milliseconds 500
}

Write-Host "   Health: OK"

Write-Host ""
Write-Host "3) Calling MCP tools (initialize -> tools/list -> get_status -> generate_plan)..."

$init = @{
    jsonrpc = "2.0"
    id      = 1
    method  = "initialize"
    params  = @{
        protocolVersion = "2024-11-05"
        capabilities    = @{}
        clientInfo      = @{ name = "test-mcp.ps1"; version = "0" }
    }
} | ConvertTo-Json -Depth 10

$initResp = Invoke-WebRequest -Method Post -Uri "http://127.0.0.1:$Port/mcp" -ContentType "application/json" -Body $init -UseBasicParsing
$session = $initResp.Headers["Mcp-Session-Id"]
Write-Host "   Session: $session"

# Notification: must get HTTP 202
Invoke-WebRequest -Method Post -Uri "http://127.0.0.1:$Port/mcp" -ContentType "application/json" -Headers @{ "Mcp-Session-Id" = $session } -Body '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}' -UseBasicParsing | Out-Null

$toolsList = '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
$toolsResp = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$Port/mcp" -ContentType "application/json" -Headers @{ "Mcp-Session-Id" = $session } -Body $toolsList
Write-Host ("   Tools found: " + ($toolsResp.result.tools.Count))

$statusReq = '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"get_status","arguments":{}}}'
$statusResp = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$Port/mcp" -ContentType "application/json" -Headers @{ "Mcp-Session-Id" = $session } -Body $statusReq
Write-Host "   get_status: OK"

$task = "Create a tiny plan to add a file hello.txt that contains the word HELLO."
$planArgs = @{
    task_description = $task
    context          = "This is an automated end-to-end test."
}
if ($Model) {
    $planArgs.model = $Model
}

$planReq = @{
    jsonrpc = "2.0"
    id      = 4
    method  = "tools/call"
    params  = @{
        name      = "generate_plan"
        arguments = $planArgs
    }
} | ConvertTo-Json -Depth 10

Invoke-WebRequest -Method Post -Uri "http://127.0.0.1:$Port/mcp" -ContentType "application/json" -Headers @{ "Mcp-Session-Id" = $session } -Body $planReq -UseBasicParsing | Out-Null
Write-Host "   generate_plan: OK (wrote plan.md)"

$artifacts = Join-Path $ProjectRoot "state\artifacts"
Write-Host ""
Write-Host "Done."
Write-Host "Open this folder to see the files:"
Write-Host "  $artifacts"
Write-Host ""
Write-Host "Tip: To verify the model name works in Gemini CLI directly:"
Write-Host "  gemini --output-format json -m <model-name> 'Respond with exactly: OK'"
