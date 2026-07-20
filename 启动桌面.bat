@echo off
chcp 65001 >nul
rem SpiritKin 一键启动器：双击即拉起全栈（命令网关 / 事件桥 / 前端 / WPF 桌面）。
rem 只是 scripts\start_desktop_console.py 的包装，不改启动器本体（禁改区）。
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 python，请先安装或加入 PATH。
    pause
    exit /b 1
)

rem 复用上次会话 token：后台服务可能仍在运行，换新 token 会导致桌面与网关对不上。
rem 没有历史记录时留空，由启动器自动生成本次会话 token。
set "SPIRITKIN_LAST_TOKEN="
for /f "usebackq delims=" %%t in (`python -c "import json,pathlib;p=pathlib.Path('state/run/desktop_console.json');print(json.loads(p.read_text(encoding='utf-8')).get('session_token','') if p.exists() else '')" 2^>nul`) do set "SPIRITKIN_LAST_TOKEN=%%t"

if defined SPIRITKIN_LAST_TOKEN (
    python scripts\start_desktop_console.py --token "%SPIRITKIN_LAST_TOKEN%" --open-mode wpf --restart-wpf
) else (
    python scripts\start_desktop_console.py --open-mode wpf --restart-wpf
)

if errorlevel 1 (
    echo.
    echo [错误] 启动失败，请把上方日志发给助手排查。
    pause
)
