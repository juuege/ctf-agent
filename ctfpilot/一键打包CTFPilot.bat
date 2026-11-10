@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion
title CTFPilot 技能一键打包

echo.
echo ============================================================
echo   CTFPilot 技能标准包 - 一键打包
echo   skill + 引擎源码 + 状态看板 + 打包器本身
echo ============================================================
echo.

rem ---------- 1. 定位 skill ----------
set "SKILL_DIR="
for /d %%U in ("%USERPROFILE%\.config\TeleAgent\users\*") do (
  if not defined SKILL_DIR if exist "%%~fU\skills\ctfpilot\SKILL.md" set "SKILL_DIR=%%~fU\skills\ctfpilot"
)
if not defined SKILL_DIR if exist "%~dp0ctfpilot\SKILL.md" set "SKILL_DIR=%~dp0ctfpilot"
if not defined SKILL_DIR (
  echo [!!] 未找到 ctfpilot skill 目录。
  echo      已查找: %USERPROFILE%\.config\TeleAgent\users\*\skills\ctfpilot
  echo      请确认 TeleAgent 已安装该技能，或把本脚本放到 skill 同级目录。
  goto :fail
)
echo [OK] skill:  "%SKILL_DIR%"

rem ---------- 2. 定位引擎 ----------
set "ENGINE_DIR="
if exist "%~dp0CTFPilot-v0.1\ctfpilot.py" set "ENGINE_DIR=%~dp0CTFPilot-v0.1"
if not defined ENGINE_DIR if exist "%USERPROFILE%\.local\share\TeleAgent\TeleAgent的工作空间\CTFPilot-v0.1\ctfpilot.py" set "ENGINE_DIR=%USERPROFILE%\.local\share\TeleAgent\TeleAgent的工作空间\CTFPilot-v0.1"
if not defined ENGINE_DIR for /d %%D in ("%USERPROFILE%\.local\share\TeleAgent\*\CTFPilot*") do (
  if not defined ENGINE_DIR if exist "%%~fD\ctfpilot.py" set "ENGINE_DIR=%%~fD"
)
if not defined ENGINE_DIR for /d %%D in ("D:\CTFPilot*") do (
  if not defined ENGINE_DIR if exist "%%~fD\ctfpilot.py" set "ENGINE_DIR=%%~fD"
)
if defined ENGINE_DIR (
  echo [OK] 引擎:  "%ENGINE_DIR%"
) else (
  echo [!!] 未找到 CTFPilot 引擎，将只打包 skill 与看板。
)

rem ---------- 3. 定位看板 HTML ----------
set "HTML="
if exist "%~dp0CTFPilot-技能看板.html" set "HTML=%~dp0CTFPilot-技能看板.html"
if not defined HTML for %%F in ("%~dp0*.html") do (
  if not defined HTML set "HTML=%%~fF"
)
if defined HTML (
  echo [OK] 看板:  "%HTML%"
) else (
  echo [!!] 未找到看板 HTML，包内将缺少看板。
)

rem ---------- 4. 准备时间戳与暂存目录 ----------
for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"`) do set "STAMP=%%i"
if not defined STAMP set "STAMP=%RANDOM%"
set "PKG_NAME=CTFPilot-Skill-Package_%STAMP%"
set "STAGE_ROOT=%TEMP%\CTFPilot_pack_%RANDOM%%RANDOM%"
set "STAGE=%STAGE_ROOT%\%PKG_NAME%"
set "ZIP=%~dp0%PKG_NAME%.zip"

if exist "%ZIP%" (
  echo [!!] 目标 ZIP 已存在，请稍后重试。
  goto :fail
)
mkdir "%STAGE%\skill" >nul 2>nul
if not exist "%STAGE%\skill" (
  echo [!!] 无法创建暂存目录: %STAGE%
  goto :fail
)
echo [..] 暂存:  %STAGE%

rem ---------- 5. 复制 skill ----------
robocopy "%SKILL_DIR%" "%STAGE%\skill\ctfpilot" /E /XD __pycache__ /XF *.pyc /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
  echo [!!] 复制 skill 失败。
  goto :fail
)
echo [OK] 已收集 skill 目录

rem ---------- 6. 复制引擎源码，排除缓存/轨迹库/题库 ----------
if defined ENGINE_DIR (
  robocopy "%ENGINE_DIR%" "%STAGE%\CTFPilot-v0.1" /E /XD __pycache__ challs_incoming .temp /XF *.pyc trajectories.sqlite _ctfpilot_trace.json /NFL /NDL /NJH /NJS /NP >nul
  if errorlevel 8 (
    echo [!!] 复制引擎源码失败。
    goto :fail
  )
  echo [OK] 已收集引擎源码，已排除 __pycache__ / 轨迹库 / 题库
)

rem ---------- 7. 复制看板与打包器本身 ----------
if defined HTML copy /y "%HTML%" "%STAGE%\CTFPilot-技能看板.html" >nul
copy /y "%~f0" "%STAGE%\一键打包CTFPilot.bat" >nul
echo [OK] 已收集看板与打包器

rem ---------- 8. 生成说明文件 ----------
set "README=%STAGE%\打包说明.txt"
echo CTFPilot 技能标准包> "%README%"
echo 生成时间: %STAMP%>> "%README%"
echo.>> "%README%"
echo 包内结构:>> "%README%"
echo   skill\ctfpilot          技能主文件 SKILL.md 与 references>> "%README%"
echo   CTFPilot-v0.1          引擎源码，已排除缓存、轨迹库、题库原始数据>> "%README%"
echo   CTFPilot-技能看板.html  技能状态看板>> "%README%"
echo   一键打包CTFPilot.bat    本打包器，可在解压目录重复运行>> "%README%"
echo.>> "%README%"
echo 使用步骤:>> "%README%"
echo   1. 安装 Python 3.10 或更高版本>> "%README%"
echo   2. 将 CTFPilot-v0.1 放到任意目录，建议放到 TeleAgent 工作空间根目录>> "%README%"
echo   3. 设置环境变量 CTFPILOT_PYTHON 为 python.exe 的完整路径>> "%README%"
echo   4. 在引擎目录运行: python -X utf8 ctfpilot.py selftest>> "%README%"
echo   5. 技能安装: 将 skill\ctfpilot 复制到 TeleAgent 的 skills 目录>> "%README%"
echo   6. 双击 CTFPilot-技能看板.html 查看技能状态与能力矩阵>> "%README%"
echo.>> "%README%"
echo 说明:>> "%README%"
echo   - 标准包不含 memory\trajectories.sqlite 本地记忆库>> "%README%"
echo   - 标准包不含 challs_incoming 内的题目原始数据>> "%README%"
echo   - 本包不含任何 LLM 后端，全部本机离线运行>> "%README%"
echo   - 仅用于授权比赛与本地靶场，沙箱默认离线>> "%README%"
echo [OK] 已生成打包说明

rem ---------- 9. 生成 manifest.json ----------
powershell -NoProfile -ExecutionPolicy Bypass -Command "function Get-Sha($p){ $s=[System.Security.Cryptography.SHA256]::Create(); $f=[System.IO.File]::OpenRead($p); try { ([System.BitConverter]::ToString($s.ComputeHash($f))).Replace('-','').ToLower() } finally { $f.Close(); $s.Dispose() } }; Push-Location -LiteralPath '%STAGE%'; $items=Get-ChildItem -Recurse -File | ForEach-Object { [pscustomobject]@{ path=(Resolve-Path -LiteralPath $_.FullName -Relative).Replace('.\','').Replace('\','/'); size=$_.Length; sha256=(Get-Sha $_.FullName) } }; Pop-Location; $json=$items | ConvertTo-Json -Depth 4; [System.IO.File]::WriteAllText((Join-Path '%STAGE%' 'manifest.json'), $json, (New-Object System.Text.UTF8Encoding($false)))" >nul
if not exist "%STAGE%\manifest.json" (
  echo [!!] manifest.json 生成失败。
  goto :fail
)
echo [OK] 已生成 manifest.json

rem ---------- 10. 压缩 ----------
echo [..] 正在压缩...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Add-Type -AssemblyName System.IO.Compression.FileSystem; [System.IO.Compression.ZipFile]::CreateFromDirectory('%STAGE%', '%ZIP%', [System.IO.Compression.CompressionLevel]::Optimal, $true)" >nul
if not exist "%ZIP%" (
  echo [!!] ZIP 创建失败。
  goto :fail
)

rem ---------- 11. 校验包内关键文件 ----------
set "VERIFY=0"
for /f "usebackq delims=" %%V in (`powershell -NoProfile -Command "Add-Type -AssemblyName System.IO.Compression.FileSystem; $z=[System.IO.Compression.ZipFile]::OpenRead('%ZIP%'); $n=@($z.Entries.FullName -replace '\\','/'); $need=@('skill/ctfpilot/SKILL.md','CTFPilot-技能看板.html','一键打包CTFPilot.bat','CTFPilot-v0.1/ctfpilot.py'); $miss=@(); foreach($x in $need){ $hit=$false; foreach($e in $n){ if($e -eq $x -or $e.EndsWith('/'+$x)){ $hit=$true; break } }; if(-not $hit){ $miss += $x } }; if($miss.Count){ Write-Output ('MISSING ' + ($miss -join ',')) } else { Write-Output ('OK ' + $z.Entries.Count) }; $z.Dispose()"`) do set "VERIFY=%%V"
echo [..] 校验: %VERIFY%
echo %VERIFY% | findstr /b /c:"OK " >nul
if errorlevel 1 (
  echo [!!] 包内关键文件校验失败: %VERIFY%
  goto :fail
)

rem ---------- 12. 汇总 ----------
for %%Z in ("%ZIP%") do set "ZIP_SIZE=%%~zZ"
set /a ZIP_KB=%ZIP_SIZE% / 1024
echo.
echo ============================================================
echo   打包完成
echo   ZIP : %ZIP%
echo   大小: %ZIP_KB% KB
echo   校验: %VERIFY%
echo ============================================================
echo.
echo 解压后请阅读 打包说明.txt，设置 CTFPILOT_PYTHON 后运行 selftest 验证。
echo.

rem ---------- 13. 清理暂存 ----------
rmdir /s /q "%STAGE_ROOT%" >nul 2>nul
if /i not "%~1"=="/quiet" pause
endlocal
exit /b 0

:fail
echo.
echo 打包未完成，请根据上方提示排查。
if defined STAGE_ROOT if exist "%STAGE_ROOT%" rmdir /s /q "%STAGE_ROOT%" >nul 2>nul
if /i not "%~1"=="/quiet" pause
endlocal
exit /b 1