@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
title 卸载浏览器自动拉起 - B站字幕服务（开发布局）

rem ============================================================
rem  卸载「浏览器自动拉起」：清除注册表项与生成的宿主/清单文件。
rem
rem  默认保留 engine\ junction（开发时仍需用它跑服务）。
rem  若确认不再需要，加 /engine 参数一并删除 junction。
rem
rem  用法： 卸载-浏览器自动拉起.bat
rem         卸载-浏览器自动拉起.bat /y
rem         卸载-浏览器自动拉起.bat /y /engine
rem ============================================================

rem 以本脚本所在目录为仓库根（脚本放在仓库根目录下）
cd /d "%~dp0"
set "ROOT=%CD%"
if not exist "%ROOT%\subtitle-server.py" (
  echo 错误：本脚本需放在仓库根目录（与 subtitle-server.py 同级）下运行
  pause
  exit /b 1
)

set "HOST_NAME=com.dsh.bilisub_server"
set "AUTO=0"
set "DEL_ENGINE=0"
for %%A in (%*) do (
  if /i "%%~A"=="/y"       set "AUTO=1"
  if /i "%%~A"=="/engine"  set "DEL_ENGINE=1"
)

echo ================================================================
echo  卸载「浏览器自动拉起」（开发布局）
echo ================================================================
echo   将清除以下浏览器的注册项（存在才清）：
echo     Chrome / Edge 的 NativeMessagingHosts\%HOST_NAME%
echo   并删除生成的：subtitle-native.exe、native-manifest.json
if !DEL_ENGINE!==1 echo   以及 engine\ junction（按参数要求）
echo.

if !AUTO!==0 (
  set /p GO=确认继续？^(Y/N^) 
  if /i not "!GO!"=="Y" ( echo 已取消。 & pause & exit /b 0 )
)
echo.

echo [1/3] 清理注册表
for %%K in (
  "HKCU\Software\Microsoft\Edge\NativeMessagingHosts\%HOST_NAME%"
  "HKCU\Software\Google\Chrome\NativeMessagingHosts\%HOST_NAME%"
) do (
  reg query %%K >nul 2>&1
  if not errorlevel 1 (
    reg delete %%K /f >nul 2>&1
    if errorlevel 1 ( echo       失败：%%K ) else ( echo       已删除 %%K )
  ) else (
    echo       不存在，跳过 %%K
  )
)
echo.

echo [2/3] 删除生成的宿主与清单
if exist "%ROOT%\subtitle-native.exe" (
  tasklist /fi "imagename eq subtitle-native.exe" 2>nul | findstr /i "subtitle-native.exe" >nul
  if not errorlevel 1 (
    echo       宿主进程仍在运行（通常是浏览器还开着），exe 被占用无法删除
    echo       请先关闭 Edge/Chrome，或结束 subtitle-native.exe 后重跑本脚本
    echo       本次保留 subtitle-native.exe
  ) else (
    del /f /q "%ROOT%\subtitle-native.exe" >nul 2>&1
    if exist "%ROOT%\subtitle-native.exe" ( echo       失败：subtitle-native.exe 被占用 ) else ( echo       已删除 subtitle-native.exe )
  )
) else ( echo       subtitle-native.exe 不存在，跳过 )
if exist "%ROOT%\native-manifest.json" (
  del /f /q "%ROOT%\native-manifest.json" >nul 2>&1
  if exist "%ROOT%\native-manifest.json" ( echo       失败：native-manifest.json ) else ( echo       已删除 native-manifest.json )
) else ( echo       native-manifest.json 不存在，跳过 )
echo.

echo [3/3] engine 目录
if !DEL_ENGINE!==1 (
  if exist "%ROOT%\engine" (
    rmdir "%ROOT%\engine" >nul 2>&1
    if exist "%ROOT%\engine" ( echo       失败：engine 删除失败（可能非 junction 或仍被占用） ) else ( echo       已删除 engine junction )
  ) else ( echo       engine 不存在，跳过 )
) else (
  echo       保留 engine\（开发时仍需用它跑服务；如需删除请加 /engine 重跑）
)
echo.

echo ================================================================
echo  卸载完成
echo ================================================================
echo   服务仍可手动启动：双击 启动字幕服务.bat
echo   需要恢复自动拉起时，重跑 安装-浏览器自动拉起.bat
echo ================================================================
pause
exit /b 0
