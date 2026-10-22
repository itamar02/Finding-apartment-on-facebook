@echo off
cd /d "%~dp0"
title ApartmentBot app
rem Already running (e.g. started by hand before the logon task)? Then there is nothing to do.
netstat -ano | findstr /r /c:":8787 .*LISTENING" >nul && exit 0
rem Runs the server and starts it again if it ever stops; the output goes to app_server.log (git-ignored).
:again
echo [%date% %time%] starting >> app_server.log
python -u app_server.py >> app_server.log 2>&1
echo [%date% %time%] stopped with exit code %ERRORLEVEL%, restarting in 5 seconds >> app_server.log
timeout /t 5 /nobreak >nul
goto again
