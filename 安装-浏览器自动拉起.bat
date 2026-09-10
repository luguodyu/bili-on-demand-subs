@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
title 安装浏览器自动拉起 - B站字幕服务（开发布局）

rem ============================================================
rem  为「开发布局」安装 Native Messaging 原生宿主，让插件能自动拉起本地服务。
rem
rem  与便携包的区别：不复制 Python 环境，而是用目录 junction engine\
rem  指向 conda 环境。宿主源码按便携布局找 engine\python.exe，
rem  因此 tools\native_host.cs 无需任何改动。
rem
rem  本脚本做四件事：
rem    1. 建 engine\ junction -> conda 环境（已存在则跳过）
rem    2. 用 csc.exe 编译 tools\native_host.cs -> subtitle-native.exe
rem    3. 生成 native-manifest.json
rem    4. 写注册表 HKCU\...\<浏览器>\NativeMessagingHosts\com.dsh.bilisub_server
rem
rem  用法： 安装-浏览器自动拉起.bat            交互确认
rem         安装-浏览器自动拉起.bat /y         跳过确认
rem         安装-浏览器自动拉起.bat /y /chrome 注册到 Chrome（默认 Edge）
rem
rem  关于插件 ID：无法从目录路径可靠推导（实测 Chrome 会对路径做规范化），
rem  所以本脚本按「已注册的 ID -> 交互输入」取值，不猜测。
rem ============================================================

rem 以本脚本所在目录为仓库根（脚本放在仓库根目录下）
cd /d "%~dp0"
set "ROOT=%CD%"
if not exist "%ROOT%\subtitle-server.py" (
  echo 错误：本脚本需放在仓库根目录（与 subtitle-server.py 同级）下运行
  pause
  exit /b 1
)

rem ---- 可配置项（换机器时改这里，或用环境变量覆盖）----
if not defined CONDA_ENV set "CONDA_ENV=C:\Users\86150\.conda\envs\bilibili_subtitle"
set "HOST_NAME=com.dsh.bilisub_server"
set "AUTO=0"
set "BROWSER=edge"
for %%A in (%*) do (
  if /i "%%~A"=="/y"      set "AUTO=1"
  if /i "%%~A"=="/chrome" set "BROWSER=chrome"
)

if /i "%BROWSER%"=="chrome" (
  set "KEY=HKCU\Software\Google\Chrome\NativeMessagingHosts\%HOST_NAME%"
) else (
  set "KEY=HKCU\Software\Microsoft\Edge\NativeMessagingHosts\%HOST_NAME%"
)

echo ================================================================
echo  安装「浏览器自动拉起」（开发布局）
echo ================================================================
echo   仓库目录  : %ROOT%
echo   conda 环境 : %CONDA_ENV%
echo   宿主名称  : %HOST_NAME%
echo   浏览器    : %BROWSER%
echo.

if !AUTO!==0 (
  set /p GO=确认继续？^(Y/N^) 
  if /i not "!GO!"=="Y" ( echo 已取消。 & pause & exit /b 0 )
)
echo.

rem ---------- 1) engine junction ----------
echo [1/4] 准备 engine 目录
if exist "%ROOT%\engine\python.exe" (
  echo       已存在，跳过
) else (
  if exist "%ROOT%\engine" (
    echo       错误：%ROOT%\engine 存在但不是有效环境，请先手工处理
    goto :fail
  )
  if not exist "%CONDA_ENV%\python.exe" (
    echo       错误：找不到 %CONDA_ENV%\python.exe
    echo       请修改本脚本顶部 CONDA_ENV，或设置同名环境变量
    goto :fail
  )
  mklink /J "%ROOT%\engine" "%CONDA_ENV%" >nul 2>&1
  if not exist "%ROOT%\engine\python.exe" (
    echo       错误：junction 创建失败，请确认目标在本地 NTFS 卷上
    goto :fail
  )
  echo       已建 junction -^> %CONDA_ENV%
)
if not exist "%ROOT%\subtitle-server.py" (
  echo       错误：找不到 subtitle-server.py，宿主需要它与 exe 同级
  goto :fail
)
echo.

rem ---------- 2) 编译宿主 ----------
echo [2/4] 编译原生宿主
set "CSC="
for %%C in (
  "%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
  "%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe"
) do if not defined CSC if exist %%C set "CSC=%%~C"
if not defined CSC (
  echo       错误：找不到 csc.exe（需要 .NET Framework 4.x）
  goto :fail
)
"%CSC%" /nologo /out:"%ROOT%\subtitle-native.exe" "%ROOT%\tools\native_host.cs" >nul
if not exist "%ROOT%\subtitle-native.exe" (
  echo       错误：编译失败
  goto :fail
)
echo       已编译 subtitle-native.exe
echo.

rem ---------- 3) 生成 manifest ----------
echo [3/4] 生成 native-manifest.json
set "EXT_ID="
rem 优先复用注册表里已有的 ID（读取该 manifest 的 allowed_origins），
rem 这样重装/换仓库路径时无需再手工输入，也避免输错导致宿主不被调用。
set "REG_MANIFEST="
for /f "tokens=2,*" %%A in ('reg query "!KEY!" /ve 2^>nul ^| findstr /i "REG_SZ"') do set "REG_MANIFEST=%%B"
if defined REG_MANIFEST (
  for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "try{(Get-Content -LiteralPath '!REG_MANIFEST!' -Raw -Encoding UTF8 | ConvertFrom-Json).allowed_origins[0] -replace '^chrome-extension://','' -replace '/$',''}catch{''}"`) do set "EXT_ID=%%I"
)
if defined EXT_ID echo       复用已注册的插件 ID: !EXT_ID!
if not defined EXT_ID (
  echo.
  echo       未能从注册表取得插件 ID，请手动提供：
  echo       打开 edge://extensions，找到本插件卡片，复制其 ID（32 位小写字母）
  set /p EXT_ID=插件 ID： 
)
if "!EXT_ID!"=="" ( echo       错误：插件 ID 为空 & goto :fail )

set "EXE_JS=%ROOT%\subtitle-native.exe"
set "EXE_JS=%EXE_JS:\=/%"
> "%ROOT%\native-manifest.json" echo {"name":"%HOST_NAME%","description":"B站字幕服务启动器","path":"%EXE_JS%","type":"stdio","allowed_origins":["chrome-extension://!EXT_ID!/"]}
if not exist "%ROOT%\native-manifest.json" ( echo       错误：manifest 写入失败 & goto :fail )
echo       已写入 native-manifest.json
echo.

rem ---------- 4) 注册表 ----------
echo [4/4] 写入注册表
reg add "!KEY!" /ve /d "%ROOT%\native-manifest.json" /f >nul
if errorlevel 1 ( echo       错误：注册表写入失败 & goto :fail )
echo       已注册到 !KEY!
echo.

echo ================================================================
echo  安装完成
echo ================================================================
echo   以后在 B 站视频页点「生成字幕」会自动拉起本地服务
echo   首次需等模型加载约 10~20 秒
echo.
echo   重要：请到 edge://extensions 点一次插件的「重新加载」，
echo         并确认插件 ID 与上面写入的一致，否则宿主不会被调用。
echo.
echo   卸载：运行 卸载-浏览器自动拉起.bat
echo ================================================================
pause
exit /b 0

:fail
echo.
echo 安装未完成。
pause
exit /b 1
