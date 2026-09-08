@echo off
rem 一键测试：服务端 + 共享JS（每次改动后先跑这个）
cd /d "%~dp0\.."
echo === [1/2] Python server tests ===
rem 优先使用本机 conda 开发环境，找不到则用 PATH 上的 python（需能 import faster-whisper 等依赖）
set "PY=python"
if exist "%USERPROFILE%\.conda\envs\bilibili_subtitle\python.exe" set "PY=%USERPROFILE%\.conda\envs\bilibili_subtitle\python.exe"
"%PY%" tests\test_server.py
if errorlevel 1 goto :fail
echo === [2/2] JS shared tests ===
node tests\test_shared.js
if errorlevel 1 goto :fail
echo.
echo ALL TESTS PASSED
pause
exit /b 0
:fail
echo.
echo TESTS FAILED
pause
exit /b 1
