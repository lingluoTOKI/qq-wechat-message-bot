@echo off
chcp 65001 >nul
title 本地 TTS 客户端（洛天依 v2Pro）

rem ============================================================
rem  一键启动：GPT-SoVITS v2Pro 洛天依模型 + 连接服务器 TTS 代理
rem  前提：GPT-SoVITS 的 api_v2.py 已在 127.0.0.1:9880 运行
rem  关闭本窗口即停止客户端（会自动回落 MiMo / edge-tts）
rem ============================================================

set GSV=F:\github\GPT-SoVITS-v2pro-20250604

rem 1) 先看 API 在不在，不在就顺手拉起来
powershell -NoProfile -Command "try{ (New-Object Net.Sockets.TcpClient).Connect('127.0.0.1',9880); $r=1 }catch{ $r=0 }; exit (1-$r)" >nul 2>&1
if errorlevel 1 (
    echo [信息] 未检测到 9880 端口，正在启动 GPT-SoVITS API...
    start "GPT-SoVITS API" "%GSV%\runtime\python.exe" "%GSV%\api_v2.py" -a 127.0.0.1 -p 9880 -c "%GSV%\GPT_SoVITS\configs\tts_infer.yaml"
    echo [信息] 等待模型加载（首次约 30-60 秒）...
    timeout /t 20 /nobreak >nul
) else (
    echo [信息] GPT-SoVITS API 已在运行
)

rem 2) 启动客户端（会自己加载权重并连服务器）
echo.
cd /d "%~dp0"
python -u local_tts_client.py

echo.
echo [结束] 客户端已退出。按任意键关闭。
pause >nul
