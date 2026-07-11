@echo off
setlocal

:: The plugin subfolder name — must match what RenderDoc loads.
set EXT_NAME=ResourcesExporter
set TARGET_DIR=%APPDATA%\qrenderdoc\extensions\%EXT_NAME%
set SRC_DIR=%~dp0%EXT_NAME%

echo Installing %EXT_NAME%
echo   from : %SRC_DIR%
echo   to   : %TARGET_DIR%
echo.

if exist "%TARGET_DIR%" (
    rmdir /s /q "%TARGET_DIR%"
)

xcopy /E /I /Y "%SRC_DIR%" "%TARGET_DIR%\"

echo.
echo Done. Restart RenderDoc and enable the extension under Tools - Manage Extensions.
pause
endlocal
