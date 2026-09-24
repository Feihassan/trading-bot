# Makes scripts/watchdog.ps1 start at logon (the MT5 terminal needs a
# logged-in desktop session anyway) via a shortcut in the user's Startup
# folder, and starts it now. Re-run after moving the repo. Remove by
# deleting "Trading Bot Watchdog.lnk" from shell:startup.

$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
$Watchdog = Join-Path $PSScriptRoot 'watchdog.ps1'
$Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Watchdog`""

$shortcutPath = Join-Path ([Environment]::GetFolderPath('Startup')) 'Trading Bot Watchdog.lnk'
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcutPath)
$shortcut.TargetPath = (Get-Command powershell.exe).Source
$shortcut.Arguments = $Arguments
$shortcut.WorkingDirectory = $Root
$shortcut.WindowStyle = 7   # minimized
$shortcut.Save()

# Don't start a second copy if one is already running.
$running = Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" |
    Where-Object { $_.CommandLine -match [regex]::Escape($Watchdog) }
if (-not $running) {
    Start-Process powershell.exe -ArgumentList $Arguments -WorkingDirectory $Root -WindowStyle Hidden
}
Write-Host "Installed $shortcutPath and started the watchdog. Log: logs\watchdog.log"
