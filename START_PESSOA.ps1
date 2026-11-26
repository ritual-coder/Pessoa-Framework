# Pessoa Startup Script for Windows (PowerShell). The same steps as START_PESSOA.sh.
#
# Claude Desktop config on Windows (see README, "MCP Server Configuration"):
#   "command": "powershell",
#   "args": ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "C:\\path\\to\\Pessoa-Framework\\START_PESSOA.ps1"]
#
# Everything before the server starts goes to stderr: when an MCP client launches
# this script, stdout is the protocol stream and any stray line corrupts it.

# Not "Stop": Windows PowerShell 5.1 turns any stderr line of a native program (pip
# prints warnings there) into a terminating error. Exit codes are checked instead.
$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot

function Say($text) { [Console]::Error.WriteLine($text) }

# Run a native command with its output sent to stderr; stop if it fails.
function Run-Quiet($exe, [string[]]$arguments) {
    & $exe @arguments 2>&1 | ForEach-Object { [Console]::Error.WriteLine("$_") }
    if ($LASTEXITCODE -ne 0) { throw "'$exe $($arguments -join ' ')' failed with exit code $LASTEXITCODE" }
}

# 0. Locate a suitable interpreter: Python 3.10+ (the mcp package needs it).
# Override with:  $env:PYTHON_BIN = "C:\path\to\python.exe"
function Find-Python {
    # The leading comma keeps a one-item list a list: PowerShell unrolls it otherwise.
    if ($env:PYTHON_BIN) { return ,@($env:PYTHON_BIN) }
    $candidates = @(@("py", "-3.13"), @("py", "-3.12"), @("py", "-3.11"), @("py", "-3.10"), @("python"), @("python3"))
    foreach ($c in $candidates) {
        if (-not (Get-Command $c[0] -ErrorAction SilentlyContinue)) { continue }
        $rest = @($c | Select-Object -Skip 1)
        & $c[0] @rest -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { return ,$c }
    }
    return $null
}

# The venv's interpreter: Scripts\python.exe on Windows, bin/python3 elsewhere (PowerShell 7 on macOS/Linux).
$onWindows = ($PSVersionTable.PSVersion.Major -lt 6) -or $IsWindows
$venvPython = if ($onWindows) { Join-Path "venv" "Scripts\python.exe" } else { Join-Path "venv" "bin/python3" }

# 1. Setup Environment
if (-not (Test-Path "venv")) {
    $py = Find-Python
    if (-not $py) {
        Say "Error: no Python 3.10+ interpreter found."
        Say "Install one from python.org, or set `$env:PYTHON_BIN to its python.exe and re-run."
        exit 1
    }
    $pyArgs = @($py | Select-Object -Skip 1)
    Say "Creating virtual environment with $($py -join ' ')..."
    Run-Quiet $py[0] ($pyArgs + @("-m", "venv", "venv"))
    Run-Quiet $venvPython @("-m", "pip", "install", "--upgrade", "pip")
    Run-Quiet $venvPython @("-m", "pip", "install", "-r", "requirements.txt")
}

# An existing venv can predate requirements.txt (v1.1.0 installed mcp 1.x, which
# lacks the mcp 2.x server API): reinstall whenever the server's imports fail.
& $venvPython -c "import mcp.server.mcpserver, yaml, pypdf" 2>$null
if ($LASTEXITCODE -ne 0) {
    Say "Dependencies are out of date; updating from requirements.txt..."
    Run-Quiet $venvPython @("-m", "pip", "install", "-r", "requirements.txt")
}

# 2. Start MCP Bridge
Say "Starting Pessoa Framework Bridge..."
& $venvPython (Join-Path "core" "base_server.py")
exit $LASTEXITCODE
