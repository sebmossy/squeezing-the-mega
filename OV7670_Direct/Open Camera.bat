@echo off
cd /d "%~dp0"
py camera_direct.py --view
if errorlevel 1 pause
