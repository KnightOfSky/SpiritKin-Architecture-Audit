# 桌面程序运行监控脚本
# 用途：启动桌面程序并实时监控日志文件中的异常

$ErrorActionPreference = "Continue"
$logDir = "d:\SpiritKinAI\state\logs"
$logFile = "$logDir\desktop_unhandled.log"

# 确保日志目录存在
if (!(Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

# 清空旧日志（可选）
if (Test-Path $logFile) {
    Clear-Content $logFile
}

Write-Host "=== 启动桌面程序 ===" -ForegroundColor Green
Write-Host "日志监控: $logFile" -ForegroundColor Cyan

# 启动桌面程序（后台）
$desktopProcess = Start-Process -FilePath "d:\SpiritKinAI\desktop\SpiritKinDesktop\bin\Debug\net8.0-windows10.0.17763.0\SpiritKinDesktop.exe" -PassThru

Write-Host "进程 ID: $($desktopProcess.Id)" -ForegroundColor Yellow
Write-Host "按 Ctrl+C 停止监控并关闭桌面程序" -ForegroundColor Yellow
Write-Host ""

# 实时监控日志文件
$lastPosition = 0
$checkInterval = 2  # 每2秒检查一次

try {
    while (!$desktopProcess.HasExited) {
        Start-Sleep -Seconds $checkInterval

        if (Test-Path $logFile) {
            $content = Get-Content $logFile -Raw -ErrorAction SilentlyContinue
            if ($content -and $content.Length -gt $lastPosition) {
                $newContent = $content.Substring($lastPosition)
                if ($newContent.Trim()) {
                    Write-Host "=== 捕获到异常 ===" -ForegroundColor Red
                    Write-Host $newContent -ForegroundColor Red
                    Write-Host "===================" -ForegroundColor Red
                }
                $lastPosition = $content.Length
            }
        }
    }

    Write-Host ""
    Write-Host "桌面程序已退出 (退出码: $($desktopProcess.ExitCode))" -ForegroundColor Yellow

} catch {
    Write-Host "监控中断: $_" -ForegroundColor Red
} finally {
    # 清理：如果进程还在运行，强制终止
    if (!$desktopProcess.HasExited) {
        Write-Host "正在关闭桌面程序..." -ForegroundColor Yellow
        $desktopProcess.Kill()
        $desktopProcess.WaitForExit(5000)
    }
}

Write-Host ""
Write-Host "=== 最终日志内容 ===" -ForegroundColor Cyan
if (Test-Path $logFile) {
    Get-Content $logFile | Select-Object -Last 50
} else {
    Write-Host "无异常日志" -ForegroundColor Green
}
