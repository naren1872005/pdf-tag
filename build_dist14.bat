@echo off
cd /d "%~dp0"
echo Building TagPilot dist14 - output is written to build_dist14.log
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_exe.ps1" > "%~dp0build_dist14.log" 2>&1
echo EXITCODE %ERRORLEVEL% >> "%~dp0build_dist14.log"
echo Done. See build_dist14.log
