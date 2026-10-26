@echo off
chcp 65001 >nul
cd /d "%~dp0"
if errorlevel 1 (
  echo Cannot access the project folder. Please keep this window.
  pause
  exit /b 1
)
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0local-start.ps1"
set "studyExit=%errorlevel%"
if not "%studyExit%"=="0" echo Startup did not complete. See the message above.
pause
exit /b %studyExit%
