@echo off
rem 一键测试：服务端 + 共享JS（每次改动后先跑这个）
cd /d "%~dp0\.."
echo === [1/2] Python server tests ===
"C:\Users\86150\.conda\envs\bilibili_subtitle\python.exe" tests\test_server.py
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
