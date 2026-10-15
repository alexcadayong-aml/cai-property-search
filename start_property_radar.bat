@echo off
title Property Radar Server (Port 8765)
echo ======================================================================
echo    Starting Property Radar Web App...
echo    Access URL: http://localhost:8765
echo ======================================================================
cd /d "%~dp0"
py property_scanner.py --serve --port 8765
if errorlevel 1 (
    echo.
    echo Server stopped with error.
    pause
)
