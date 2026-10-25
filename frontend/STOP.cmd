@echo off
cd /d "%~dp0"
node scripts/local.cjs stop
if errorlevel 1 pause
