@echo off
cd /d "%~dp0"
node scripts/local.cjs start
if errorlevel 1 pause
