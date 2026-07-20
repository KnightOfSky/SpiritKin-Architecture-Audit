param(
    [string]$DeviceIp = $(if ($env:SPIRITKIN_ANDROID_ADB_IP) { $env:SPIRITKIN_ANDROID_ADB_IP } else { "100.118.62.77" }),
    [int]$KnownPort = $(if ($env:SPIRITKIN_ANDROID_ADB_PORT) { [int]$env:SPIRITKIN_ANDROID_ADB_PORT } else { 41055 }),
    [string]$AdbPath = $(if ($env:SPIRITKIN_ADB_PATH) { $env:SPIRITKIN_ADB_PATH } else { "" }),
    [string]$StatePath = "",
    [int]$ScanFrom = 30000,
    [int]$ScanTo = 49999,
    [int]$ConnectTimeoutMs = 180,
    [int]$IntervalSeconds = 30,
    [switch]$Watch
)

$ErrorActionPreference = "Stop"

function Resolve-AdbPath {
    param([string]$Candidate)
    if ($Candidate -and (Test-Path -LiteralPath $Candidate)) {
        return (Resolve-Path -LiteralPath $Candidate).Path
    }
    $fromPath = Get-Command adb -ErrorAction SilentlyContinue
    if ($fromPath) {
        return $fromPath.Source
    }
    $wingetPath = "C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Packages\Google.PlatformTools_Microsoft.Winget.Source_8wekyb3d8bbwe\platform-tools\adb.exe"
    if (Test-Path -LiteralPath $wingetPath) {
        return $wingetPath
    }
    throw "adb.exe was not found. Install Android platform-tools or set SPIRITKIN_ADB_PATH."
}

function Invoke-Adb {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & $script:ResolvedAdb @Arguments 2>&1
}

function Get-AdbDeviceRows {
    $rows = @()
    $output = Invoke-Adb devices -l
    foreach ($line in $output) {
        $text = [string]$line
        if ($text -match "^(\S+)\s+(\S+)(.*)$" -and $matches[1] -ne "List") {
            $rows += [pscustomobject]@{
                Serial = $matches[1]
                State = $matches[2]
                Detail = $matches[3].Trim()
            }
        }
    }
    return $rows
}

function Get-ConnectedDevice {
    param([string]$Ip)
    Get-AdbDeviceRows | Where-Object { $_.Serial -like "$Ip`:*" -and $_.State -eq "device" } | Select-Object -First 1
}

function Disconnect-StaleRows {
    param([string]$Ip)
    foreach ($row in (Get-AdbDeviceRows | Where-Object { $_.Serial -like "$Ip`:*" -and $_.State -ne "device" })) {
        Invoke-Adb disconnect $row.Serial | Out-Null
    }
}

function Load-LastPort {
    param([string]$Path, [string]$Ip, [int]$Fallback)
    if (-not (Test-Path -LiteralPath $Path)) {
        return $Fallback
    }
    try {
        $state = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
        if ([string]$state.device_ip -eq $Ip -and [int]$state.port -gt 0) {
            return [int]$state.port
        }
    } catch {
    }
    return $Fallback
}

function Save-LastPort {
    param([string]$Path, [string]$Ip, [int]$Port, [string]$Serial)
    $dir = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    [ordered]@{
        device_ip = $Ip
        port = $Port
        serial = $Serial
        updated_at = (Get-Date).ToString("o")
    } | ConvertTo-Json | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Test-TcpPortFast {
    param([string]$Ip, [int]$Port, [int]$TimeoutMs)
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $async = $client.BeginConnect($Ip, $Port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne($TimeoutMs, $false)) {
            return $false
        }
        $client.EndConnect($async)
        return $client.Connected
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Find-OpenPorts {
    param([string]$Ip, [int]$From, [int]$To, [int]$TimeoutMs)
    if ($From -gt $To) {
        return @()
    }

    $open = [System.Collections.Concurrent.ConcurrentBag[int]]::new()
    $pool = [runspacefactory]::CreateRunspacePool(1, 256)
    $pool.Open()
    $jobs = New-Object System.Collections.Generic.List[object]
    $script = {
        param($Ip, $Port, $TimeoutMs, $Open)
        $client = [System.Net.Sockets.TcpClient]::new()
        try {
            $async = $client.BeginConnect($Ip, $Port, $null, $null)
            if ($async.AsyncWaitHandle.WaitOne($TimeoutMs, $false)) {
                try {
                    $client.EndConnect($async)
                    if ($client.Connected) {
                        $Open.Add([int]$Port)
                    }
                } catch {
                }
            }
        } catch {
        } finally {
            $client.Close()
        }
    }

    try {
        foreach ($port in $From..$To) {
            $ps = [powershell]::Create()
            $ps.RunspacePool = $pool
            [void]$ps.AddScript($script).AddArgument($Ip).AddArgument($port).AddArgument($TimeoutMs).AddArgument($open)
            $handle = $ps.BeginInvoke()
            $jobs.Add([pscustomobject]@{ PowerShell = $ps; Handle = $handle })
        }
        foreach ($job in $jobs) {
            try {
                $job.PowerShell.EndInvoke($job.Handle)
            } catch {
            } finally {
                $job.PowerShell.Dispose()
            }
        }
    } finally {
        $pool.Close()
        $pool.Dispose()
    }

    return @($open.ToArray() | Sort-Object)
}

function Try-ConnectPort {
    param([string]$Ip, [int]$Port)
    if ($Port -le 0) {
        return $null
    }
    Invoke-Adb connect "${Ip}:$Port" | Out-Null
    Start-Sleep -Milliseconds 700
    $device = Get-ConnectedDevice -Ip $Ip
    if ($device -and $device.Serial -eq "${Ip}:$Port") {
        return $device
    }
    foreach ($row in (Get-AdbDeviceRows | Where-Object { $_.Serial -eq "${Ip}:$Port" -and $_.State -ne "device" })) {
        Invoke-Adb disconnect $row.Serial | Out-Null
    }
    return $null
}

function Ensure-AdbWirelessConnection {
    param([string]$Ip)

    Invoke-Adb start-server | Out-Null
    Disconnect-StaleRows -Ip $Ip

    $current = Get-ConnectedDevice -Ip $Ip
    if ($current) {
        return $current
    }

    $lastPort = Load-LastPort -Path $script:ResolvedStatePath -Ip $Ip -Fallback $KnownPort
    if (Test-TcpPortFast -Ip $Ip -Port $lastPort -TimeoutMs $ConnectTimeoutMs) {
        $connected = Try-ConnectPort -Ip $Ip -Port $lastPort
        if ($connected) {
            Save-LastPort -Path $script:ResolvedStatePath -Ip $Ip -Port $lastPort -Serial $connected.Serial
            return $connected
        }
    }

    $openPorts = Find-OpenPorts -Ip $Ip -From $ScanFrom -To $ScanTo -TimeoutMs $ConnectTimeoutMs
    foreach ($port in $openPorts) {
        $connected = Try-ConnectPort -Ip $Ip -Port $port
        if ($connected) {
            Save-LastPort -Path $script:ResolvedStatePath -Ip $Ip -Port $port -Serial $connected.Serial
            return $connected
        }
    }

    return $null
}

$script:ResolvedAdb = Resolve-AdbPath -Candidate $AdbPath
if (-not $StatePath) {
    $StatePath = Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..")).Path "state\mobile\adb-wireless.json"
}
$script:ResolvedStatePath = $StatePath

do {
    $device = Ensure-AdbWirelessConnection -Ip $DeviceIp
    if ($device) {
        Write-Host "ADB connected: $($device.Serial) $($device.Detail)"
    } else {
        Write-Warning "ADB wireless device not found at $DeviceIp. Keep Android Wireless debugging enabled."
    }

    if (-not $Watch) {
        if ($device) { exit 0 } else { exit 1 }
    }
    Start-Sleep -Seconds ([Math]::Max(5, $IntervalSeconds))
} while ($true)
