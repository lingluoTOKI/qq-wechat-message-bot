@echo off
chcp 936 >nul 2>&1
setlocal EnableDelayedExpansion
title BOT CONSOLE - AstrBot / SnowLuma 管理面板
cd /d "%~dp0"

set "HOST=101.132.104.41"
set "USER=root"
set "KEY=%USERPROFILE%\.ssh\id_ed25519"
set "PANEL_PORT=8765"
set "WAITEXE=%SystemRoot%\System32\timeout.exe"
set "LOGDIR=%~dp0logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%" >nul 2>&1

echo.
echo  ============================================================
echo   BOT CONSOLE   AstrBot x SnowLuma  管理控制台
echo  ============================================================
echo.

REM ---------- 1. 环境自检 ----------
where ssh >nul 2>&1
if errorlevel 1 goto NO_SSH
if not exist "%KEY%" goto NO_KEY

set "PYEXE="
python --version >nul 2>&1
if not errorlevel 1 set "PYEXE=python"
if not defined PYEXE (
    py -3 --version >nul 2>&1
    if not errorlevel 1 set "PYEXE=py -3"
)
if not defined PYEXE goto NO_PY

REM ---------- 2. 清理旧会话（按端口 PID 精确结束，防止残留） ----------
echo  [1/4] 清理旧会话...
call :KILL_PORTS
"%WAITEXE%" /t 1 /nobreak >nul
echo        完成。
echo.

REM ---------- 3. 启动 SSH 隧道（本窗口内后台运行） ----------
echo  [2/4] 建立 SSH 隧道 -^> %HOST% ...
start /b ssh -N -T -E "%LOGDIR%\tunnel.log" -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=accept-new -i "%KEY%" -L 6185:127.0.0.1:6185 -L 5099:127.0.0.1:5099 -L 6081:127.0.0.1:6081 -L 8080:127.0.0.1:8080 -L 32000:127.0.0.1:32000 %USER%@%HOST%

set /a TRIES=0
:WAIT_TUNNEL
set /a TRIES+=1
"%WAITEXE%" /t 1 /nobreak >nul
netstat -ano | findstr /C:"127.0.0.1:6185 " | findstr "LISTENING" >nul && goto TUNNEL_OK
if !TRIES! GEQ 25 goto TUNNEL_FAIL
goto WAIT_TUNNEL

:TUNNEL_OK
chcp 936 >nul 2>&1
echo        隧道已就绪 (6185 / 5099 / 6081 / 8080)。
goto TUNNEL_DONE

:TUNNEL_FAIL
chcp 936 >nul 2>&1
echo  [错误] 隧道 25 秒内未建立。ssh 日志如下：
echo  ------------------------------------------
if exist "%LOGDIR%\tunnel.log" type "%LOGDIR%\tunnel.log"
echo  ------------------------------------------
echo  常见原因：密钥不对 / 服务器不可达 / 端口被其他程序占用。
goto FAIL_END

:TUNNEL_DONE
echo.

REM ---------- 4. 启动本地管理面板（本窗口内后台运行） ----------
echo  [3/4] 启动本地管理面板 (127.0.0.1:%PANEL_PORT%) ...
start /b %PYEXE% "%~dp0panel_server.py" %PANEL_PORT% > "%LOGDIR%\panel.log" 2>&1

set /a PTRIES=0
:WAIT_PANEL
set /a PTRIES+=1
"%WAITEXE%" /t 1 /nobreak >nul
netstat -ano | findstr /C:"127.0.0.1:%PANEL_PORT% " | findstr "LISTENING" >nul && goto PANEL_OK
if !PTRIES! GEQ 15 goto PANEL_FAIL
goto WAIT_PANEL

:PANEL_OK
echo        面板已就绪。
goto PANEL_DONE

:PANEL_FAIL
chcp 936 >nul 2>&1
echo  [错误] 面板 15 秒内未启动。日志如下：
echo  ------------------------------------------
if exist "%LOGDIR%\panel.log" type "%LOGDIR%\panel.log"
echo  ------------------------------------------
goto FAIL_END

:PANEL_DONE

REM ---------- 5. 打开浏览器 ----------
echo  [4/4] 打开浏览器...
start "" "http://127.0.0.1:%PANEL_PORT%"
echo.

echo  ============================================================
echo   管理面板 : http://127.0.0.1:%PANEL_PORT%
echo   AstrBot  : http://127.0.0.1:6185
echo   SnowLuma : http://127.0.0.1:5099
echo   noVNC    : http://127.0.0.1:6081
echo  ============================================================
echo.
echo   【断开】按任意键，或直接关闭本窗口，
echo          即可自动断开 SSH 隧道并退出。
echo   【日志】%LOGDIR%
echo.

pause >nul

REM ---------- 6. 退出清理 ----------
echo.
echo  [退出] 正在断开 SSH 隧道并关闭面板...
call :KILL_PORTS
chcp 936 >nul 2>&1
echo  [退出] 完成。
"%WAITEXE%" /t 1 /nobreak >nul
endlocal
exit /b 0

REM ---------- 子程序：按监听端口结束残留进程 ----------
:KILL_PORTS
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /C:"127.0.0.1:6185 " ^| findstr "LISTENING"') do taskkill /F /PID %%a >nul 2>&1
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /C:"127.0.0.1:8765 " ^| findstr "LISTENING"') do taskkill /F /PID %%a >nul 2>&1
REM 32000 是本地 TTS 隧道的转发端口；若有残留隧道占着它，ssh 会因 ExitOnForwardFailure 直接退出
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /C:":32000 " ^| findstr "LISTENING"') do taskkill /F /PID %%a >nul 2>&1
goto :eof

:NO_SSH
echo  [错误] 未找到 ssh 客户端。
echo         请安装 "Windows OpenSSH 客户端"（设置 - 应用 - 可选功能）。
goto FAIL_END
:NO_KEY
echo  [错误] 未找到 SSH 私钥：%KEY%
echo         若密钥是 pem 文件，请修改本文件顶部的 KEY 变量。
goto FAIL_END
:NO_PY
echo  [错误] 未找到可用的 Python 3。
echo         请安装 Python 3 并勾选 "Add to PATH"。
goto FAIL_END

:FAIL_END
call :KILL_PORTS
chcp 936 >nul 2>&1
echo.
pause
endlocal
exit /b 1
