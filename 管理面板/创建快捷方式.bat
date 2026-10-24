@echo off
chcp 936 >nul 2>&1
setlocal
title 创建管理面板快捷方式

cd /d "%~dp0"

set "TARGET=%~dp0启动管理面板.bat"
set "ICON=%~dp0panel.ico"

REM 生成图标（若不存在，用 Python 画一个最小化的 memphis 风格图标）
if not exist "%ICON%" (
  python --version >nul 2>&1
  if not errorlevel 1 python "%~dp0make_icon.py" "%ICON%" >nul 2>&1
  if not exist "%ICON%" (
    py -3 --version >nul 2>&1
    if not errorlevel 1 py -3 "%~dp0make_icon.py" "%ICON%" >nul 2>&1
  )
)

set "PSCMD=$ws = New-Object -ComObject WScript.Shell; $sc = $ws.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\BOT 管理面板.lnk'); $sc.TargetPath = '%TARGET%'; $sc.WorkingDirectory = '%~dp0'; $sc.Description = 'AstrBot + SnowLuma 服务器管理面板（一键开启 SSH 隧道）'; $sc.IconLocation = '%ICON%,0'; $sc.Save(); Write-Host '[OK] 桌面快捷方式已创建'"

powershell -NoProfile -ExecutionPolicy Bypass -Command "%PSCMD%"

if errorlevel 1 (
  echo  [警告] 快捷方式创建失败，可手动右键 启动管理面板.bat 发送到桌面快捷方式。
) else (
  echo.
  echo  完成。双击桌面上的 "BOT 管理面板" 即可一键启动。
)

endlocal
