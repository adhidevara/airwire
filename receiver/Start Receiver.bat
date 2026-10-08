@echo off
rem Double-click to start the Airwire Receiver window.
cd /d "%~dp0"
python -m airwire %*
if errorlevel 1 pause
