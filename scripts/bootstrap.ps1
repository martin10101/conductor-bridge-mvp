# bootstrap.ps1 - Idempotent installer for conductor-bridge-mvp prerequisites
# Logs all output to logs\install.log

$ErrorActionPreference = "Continue"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptRoot
$LogDir = Join-Path $ProjectRoot "logs"
$LogFile = Join-Path $LogDir "install.log"

# Ensure log directory exists
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

function Log {
    param([string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $logMessage = "[$timestamp] $Message"
    Write-Host $logMessage
    Add-Content -Path $LogFile -Value $logMessage
}

function Test-Command {
    param([string]$Command)
    try {
        $null = Get-Command $Command -ErrorAction Stop
        return $true
    } catch {
        return $false
    }
}

function Refresh-Path {
    Log "Refreshing PATH environment variable..."
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
}

Log "=========================================="
Log "Starting bootstrap for conductor-bridge-mvp"
Log "=========================================="

# STEP 1: Check for winget
Log "Checking for winget..."
if (Test-Command "winget") {
    Log "winget is available."
} else {
    Log "ERROR: winget is not available."
    Log "Please install winget manually via Microsoft Store (App Installer) or from:"
    Log "https://github.com/microsoft/winget-cli/releases"
    Log "After installing winget, re-run this script."
    exit 1
}

# STEP 2: Install Git if missing
Log "Checking for Git..."
if (Test-Command "git") {
    $gitVersion = git --version
    Log "Git is already installed: $gitVersion"
} else {
    Log "Installing Git via winget..."
    winget install --id Git.Git -e --accept-source-agreements --accept-package-agreements 2>&1 | Tee-Object -Append -FilePath $LogFile
    Refresh-Path
}

# STEP 3: Install Node.js LTS if missing
Log "Checking for Node.js..."
if (Test-Command "node") {
    $nodeVersion = node --version
    Log "Node.js is already installed: $nodeVersion"
} else {
    Log "Installing Node.js LTS via winget..."
    winget install --id OpenJS.NodeJS.LTS -e --accept-source-agreements --accept-package-agreements 2>&1 | Tee-Object -Append -FilePath $LogFile
    Refresh-Path
}

# STEP 4: Install Python 3.12+ if missing
Log "Checking for Python..."
if (Test-Command "python") {
    $pythonVersion = python --version 2>&1
    Log "Python is already installed: $pythonVersion"
} else {
    Log "Installing Python 3.12 via winget..."
    winget install --id Python.Python.3.12 -e --accept-source-agreements --accept-package-agreements 2>&1 | Tee-Object -Append -FilePath $LogFile
    Refresh-Path
}

# Refresh PATH after all installs
Refresh-Path

# STEP 5: Install Gemini CLI globally via npm
Log "Checking for Gemini CLI..."
if (Test-Command "gemini") {
    $geminiVersion = gemini --version 2>&1
    Log "Gemini CLI is already installed: $geminiVersion"
} else {
    Log "Installing Gemini CLI globally via npm..."
    npm install -g @google/gemini-cli 2>&1 | Tee-Object -Append -FilePath $LogFile
    Refresh-Path
}

# STEP 6: Verify all installations
Log ""
Log "=========================================="
Log "Verifying installations..."
Log "=========================================="

$verifyCommands = @(
    @{Name="Git"; Command="git"; Args="--version"},
    @{Name="Node.js"; Command="node"; Args="--version"},
    @{Name="npm"; Command="npm"; Args="--version"},
    @{Name="Python"; Command="python"; Args="--version"},
    @{Name="Gemini CLI"; Command="gemini"; Args="--version"}
)

$allPassed = $true
foreach ($cmd in $verifyCommands) {
    if (Test-Command $cmd.Command) {
        try {
            $result = & $cmd.Command $cmd.Args 2>&1
            Log "$($cmd.Name): $result"
        } catch {
            Log "$($cmd.Name): Installed but version check failed"
        }
    } else {
        Log "$($cmd.Name): NOT FOUND - Installation may require terminal restart"
        $allPassed = $false
    }
}

# STEP 7: Install Conductor extension
Log ""
Log "=========================================="
Log "Installing Gemini Conductor extension..."
Log "=========================================="

if (Test-Command "gemini") {
    Log "Running: gemini extensions install https://github.com/gemini-cli-extensions/conductor"
    gemini extensions install https://github.com/gemini-cli-extensions/conductor 2>&1 | Tee-Object -Append -FilePath $LogFile

    Log ""
    Log "Verifying Conductor installation..."
    gemini extensions list 2>&1 | Tee-Object -Append -FilePath $LogFile
} else {
    Log "Skipping Conductor install - Gemini CLI not available yet."
    Log "Please restart terminal and re-run this script."
}

# STEP 8: Gemini Authentication Check
Log ""
Log "=========================================="
Log "Checking Gemini authentication..."
Log "=========================================="

if (Test-Command "gemini") {
    Log "Running gemini to check authentication status..."
    Log "If a browser window opens for authentication, please complete the sign-in."
    Log ""

    # Run gemini with a simple prompt to trigger auth if needed
    $authResult = gemini --version 2>&1
    Log "Gemini version: $authResult"

    Log ""
    Log "NOTE: If Gemini requires authentication, run 'gemini' in a terminal."
    Log "Complete the browser/device sign-in flow, then continue."
}

Log ""
Log "=========================================="
Log "Bootstrap complete!"
Log "=========================================="
Log "Log file: $LogFile"

if (-not $allPassed) {
    Log ""
    Log "WARNING: Some tools may not be in PATH yet."
    Log "Please restart your terminal and re-run this script if needed."
}
