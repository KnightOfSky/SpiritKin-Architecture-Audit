param(
    [string]$DeviceIp = "100.118.62.77",
    [int]$KnownPort = 41055,
    [int]$IntervalSeconds = 30,
    [string]$TaskName = "SpiritKin Android Wireless ADB Reconnect"
)

$ErrorActionPreference = "Stop"

$scriptPath = Join-Path $PSScriptRoot "adb_wireless_reconnect.ps1"
if (-not (Test-Path -LiteralPath $scriptPath)) {
    throw "Missing script: $scriptPath"
}

$powerShellExe = (Get-Command powershell.exe -ErrorAction SilentlyContinue).Source
if (-not $powerShellExe) {
    $powerShellExe = (Get-Command pwsh.exe -ErrorAction Stop).Source
}

$arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$scriptPath`" -DeviceIp `"$DeviceIp`" -KnownPort $KnownPort -IntervalSeconds $IntervalSeconds -Watch"
$action = New-ScheduledTaskAction -Execute $powerShellExe -Argument $arguments -WorkingDirectory (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description "Keeps SpiritKin Android wireless ADB connected over Tailscale." -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Host "Registered and started scheduled task: $TaskName"
