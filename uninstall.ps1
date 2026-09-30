<#
.SYNOPSIS
  Removes the LLM Mascot shortcuts. Your settings, mascots and the .venv folder are left in place.
#>
$ErrorActionPreference = "Stop"
foreach ($folder in @([Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Startup"))) {
    $link = Join-Path $folder "LLM Mascot.lnk"
    if (Test-Path $link) {
        Remove-Item $link
        Write-Host "Removed $link"
    }
}
Write-Host "To remove everything else, delete this folder."
