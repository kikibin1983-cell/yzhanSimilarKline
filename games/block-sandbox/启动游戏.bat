@echo off
setlocal
cd /d "%~dp0"
where node >nul 2>&1
if errorlevel 1 (
  echo Node.js 20 or newer is required. See README.md.
  pause
  exit /b 1
)
if not exist "vendor\three.module.js" (
  echo Local game resources are missing. Restore the complete game folder.
  pause
  exit /b 1
)
node "%~dp0server.mjs" --open
if errorlevel 1 pause
