@echo off
cd /d "%~dp0"
title ApartmentBot app
python -u app_server.py
echo.
echo The app server stopped with exit code %ERRORLEVEL%.
pause
