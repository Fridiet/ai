param(
    [string]$Python = "",
    [string]$DatabaseUrl = $env:KFIND_DATABASE_URL,
    [string]$GithubRepository = $env:KFIND_GITHUB_REPOSITORY,
    [string]$GithubTag = $env:KFIND_GITHUB_TAG,
    [string]$GithubAsset = $env:KFIND_GITHUB_ASSET,
    [string]$ArchiveSha256 = $env:KFIND_ARCHIVE_SHA256,
    [string]$DatabaseSha256 = $env:KFIND_DATABASE_SHA256,
    [string]$XlsxPath = "",
    [switch]$ForceData,
    [switch]$SkipDependencies,
    [switch]$SkipData
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

function Invoke-NativeCommand {
    param(
        [Parameter(Mandatory = $true)]
        [scriptblock]$Command,
        [Parameter(Mandatory = $true)]
        [string]$FailureMessage
    )

    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw $FailureMessage
    }
}

Push-Location $ProjectRoot
try {
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        Write-Host "[1/3] Creating the Python virtual environment."
        if ($Python) {
            Invoke-NativeCommand { & $Python -m venv .venv } "Failed to create the virtual environment."
        }
        elseif (Get-Command py -ErrorAction SilentlyContinue) {
            Invoke-NativeCommand { & py -3.10 -m venv .venv } "Failed to create the Python 3.10 virtual environment."
        }
        elseif (Get-Command python -ErrorAction SilentlyContinue) {
            Invoke-NativeCommand { & python -m venv .venv } "Failed to create the virtual environment."
        }
        else {
            throw "Python was not found. Install Python 3.10 or provide its path with -Python."
        }
    }
    else {
        Write-Host "[1/3] Reusing the existing Python virtual environment."
    }

    if (-not $SkipDependencies) {
        Write-Host "[2/3] Installing Python packages."
        Invoke-NativeCommand { & $VenvPython -m pip install --upgrade pip } "Failed to upgrade pip."
        Invoke-NativeCommand { & $VenvPython -m pip install -r requirements-dev.txt } "Failed to install Python packages."
    }
    else {
        Write-Host "[2/3] Skipping Python package installation."
    }

    if (-not $SkipData) {
        Write-Host "[3/3] Preparing the K-FIND product database."
        $BootstrapArgs = @("-m", "scripts.bootstrap_data")
        if ($DatabaseUrl) {
            $BootstrapArgs += @("--database-url", $DatabaseUrl)
        }
        if ($GithubRepository) {
            $BootstrapArgs += @("--github-repository", $GithubRepository)
        }
        if ($GithubTag) {
            $BootstrapArgs += @("--github-tag", $GithubTag)
        }
        if ($GithubAsset) {
            $BootstrapArgs += @("--github-asset", $GithubAsset)
        }
        if ($ArchiveSha256) {
            $BootstrapArgs += @("--archive-sha256", $ArchiveSha256)
        }
        if ($DatabaseSha256) {
            $BootstrapArgs += @("--database-sha256", $DatabaseSha256)
        }
        if ($XlsxPath) {
            $BootstrapArgs += @("--xlsx", $XlsxPath)
        }
        if ($ForceData) {
            $BootstrapArgs += "--force"
        }
        Invoke-NativeCommand { & $VenvPython @BootstrapArgs } "Failed to prepare the K-FIND database."
    }
    else {
        Write-Host "[3/3] Skipping K-FIND database setup."
    }

    Write-Host ""
    Write-Host "Setup completed. Start the server with:"
    Write-Host ".\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000"
}
finally {
    Pop-Location
}
