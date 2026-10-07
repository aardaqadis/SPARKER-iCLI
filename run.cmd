@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
set "SPARKER_EXIT=%ERRORLEVEL%"
if not "%SPARKER_EXIT%"=="0" if "%~1"=="" pause
exit /b %SPARKER_EXIT%
