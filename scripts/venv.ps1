# venv.ps1 - Create and setup Python virtual environment

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptRoot
$VenvPath = Join-Path $ProjectRoot ".venv"

Write-Host "=========================================="
Write-Host "Setting up Python virtual environment"
Write-Host "=========================================="
Write-Host "Project root: $ProjectRoot"
Write-Host "Venv path: $VenvPath"

# Change to project root
Set-Location $ProjectRoot

# Create venv if it doesn't exist
if (-not (Test-Path $VenvPath)) {
    Write-Host "`nCreating virtual environment..."
    python -m venv $VenvPath
} else {
    Write-Host "`nVirtual environment already exists."
}

# Activate venv
Write-Host "`nActivating virtual environment..."
$ActivateScript = Join-Path $VenvPath "Scripts\Activate.ps1"
if (Test-Path $ActivateScript) {
    & $ActivateScript
} else {
    Write-Host "ERROR: Activate script not found at $ActivateScript"
    exit 1
}

# Upgrade pip
Write-Host "`nUpgrading pip..."
python -m pip install --upgrade pip

# Install package in editable mode
Write-Host "`nInstalling conductor-bridge in editable mode..."
pip install -e .

# Install dev dependencies
Write-Host "`nInstalling dev dependencies..."
pip install pytest pytest-asyncio

Write-Host "`n=========================================="
Write-Host "Virtual environment setup complete!"
Write-Host "=========================================="
Write-Host ""
Write-Host "To activate manually, run:"
Write-Host "  .\.venv\Scripts\Activate.ps1"
Write-Host ""
Write-Host "To start the MCP server:"
Write-Host "  python -m conductor_bridge.server --http --port 8765"
Write-Host ""
Write-Host "To run the cycle runner:"
Write-Host "  python -m conductor_bridge.runner --implementer simulate --cycles 3"
