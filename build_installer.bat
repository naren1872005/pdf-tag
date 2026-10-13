@echo off
cd /d "%~dp0"
echo Building TagPilot exe (dist14) and installer - progress in build_installer.log
call :main > "%~dp0build_installer.log" 2>&1
echo EXITCODE %ERRORLEVEL% >> "%~dp0build_installer.log"
echo Done. See build_installer.log
exit /b

:main
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_exe.ps1"
if errorlevel 1 (echo EXE BUILD FAILED & exit /b 1)
set "ISCC="
if exist "%ProgramFiles%\Inno Setup 7\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 7\ISCC.exe"
if exist "%ProgramFiles(x86)%\Inno Setup 7\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 7\ISCC.exe"
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC (echo ISCC.exe NOT FOUND - open setup.iss in Inno Setup and press Compile & exit /b 2)
echo Compiling installer with "%ISCC%"
"%ISCC%" "%~dp0setup.iss"
exit /b %ERRORLEVEL%
