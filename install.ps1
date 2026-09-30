<#
.SYNOPSIS
  Installs LLM Mascot: a private Python environment, the dependencies and a desktop shortcut.

.PARAMETER Startup
  Also start the mascot when Windows starts.
.PARAMETER NoShortcut
  Do not create the desktop shortcut.
.PARAMETER DownloadModel
  Download the speech-recognition model now (about 150 MB) instead of at the first dictation.
.PARAMETER NoLaunch
  Do not start the mascot at the end.
#>
param(
    [switch]$Startup,
    [switch]$NoShortcut,
    [switch]$DownloadModel,
    [switch]$NoLaunch
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

function Write-Step($text) { Write-Host ""; Write-Host "==> $text" -ForegroundColor Cyan }

function Find-Python {
    foreach ($candidate in @(@("py", "-3"), @("python"))) {
        $exe = $candidate[0]
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        $extra = @($candidate | Select-Object -Skip 1)
        try {
            $ok = & $exe @extra -c "import sys; print(int(sys.version_info >= (3, 10)))" 2>$null
            if ($ok -eq "1") { return ,@($exe) + $extra }
        } catch { }
    }
    return $null
}

Write-Step "Looking for Python 3.10 or newer"
$python = Find-Python
if (-not $python) {
    Write-Host "Python 3.10+ was not found." -ForegroundColor Yellow
    Write-Host "Install it with:  winget install Python.Python.3.12   (or from https://www.python.org/downloads/)"
    Write-Host "Then run install.bat again."
    exit 1
}
Write-Host ("Using: " + ($python -join " "))

Write-Step "Creating the virtual environment (.venv)"
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    & $python[0] @($python | Select-Object -Skip 1) -m venv (Join-Path $root ".venv")
}

Write-Step "Installing dependencies (this can take a few minutes the first time)"
& $venvPython -m pip install --upgrade pip --quiet
& $venvPython -m pip install -r (Join-Path $root "requirements.txt")

New-Item -ItemType Directory -Force -Path (Join-Path $root "mascots") | Out-Null

if ($DownloadModel) {
    Write-Step "Downloading the speech model"
    & $venvPython (Join-Path $root "tools\prefetch_model.py")
}

$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
$icon = Join-Path $root "assets\mascot.ico"
$shell = New-Object -ComObject WScript.Shell

function New-Shortcut($folder) {
    $link = $shell.CreateShortcut((Join-Path $folder "LLM Mascot.lnk"))
    $link.TargetPath = $pythonw
    $link.Arguments = "`"$(Join-Path $root 'rover.py')`""
    $link.WorkingDirectory = $root
    if (Test-Path $icon) { $link.IconLocation = $icon }
    $link.Description = "Floating voice and usage companion for your LLM tools"
    $link.Save()
    return $link.FullName
}

if (-not $NoShortcut) {
    Write-Step "Creating the desktop shortcut"
    Write-Host (New-Shortcut ([Environment]::GetFolderPath("Desktop")))
}
if ($Startup) {
    Write-Step "Starting with Windows"
    Write-Host (New-Shortcut ([Environment]::GetFolderPath("Startup")))
}

Write-Host ""
Write-Host "Done. Dictation: click the mascot or press Ctrl+Alt+R." -ForegroundColor Green
Write-Host "The speech model (about 150 MB) downloads the first time you dictate."

if (-not $NoLaunch) {
    Start-Process -FilePath $pythonw -ArgumentList "`"$(Join-Path $root 'rover.py')`"" -WorkingDirectory $root
}
