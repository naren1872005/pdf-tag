@echo off
cd /d "%~dp0"
echo Compiling the TagPilot installer from dist14 - output in compile_installer.log
call :main > "%~dp0compile_installer.log" 2>&1
echo EXITCODE %ERRORLEVEL% >> "%~dp0compile_installer.log"
echo Done. See compile_installer.log
exit /b

:main
if not exist "%~dp0dist14\tagpilot\tagpilot.exe" (echo dist14 not built - run build_installer.bat & exit /b 1)
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
