[CmdletBinding()]
param([string]$TaskName = "SocialTrendCreativeUpdater")

$ErrorActionPreference = "Stop"
$WatcherPath = Join-Path $PSScriptRoot "update-watcher.ps1"
$PowerShellPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$CurrentUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$WatcherPath`""

$action = New-ScheduledTaskAction -Execute $PowerShellPath -Argument $Arguments
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $CurrentUser
$principal = New-ScheduledTaskPrincipal -UserId $CurrentUser -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description "Social Trend Creative web update watcher" -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Host "Installed and started scheduled task: $TaskName"
