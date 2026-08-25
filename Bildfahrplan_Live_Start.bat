@echo off
setlocal

rem Alle Pfade bleiben unabhaengig vom aktuellen Arbeitsverzeichnis.
set "APP_DIR=%~dp0"
set "VENV_DIR=%APP_DIR%.venv"
set "PYTHONW=%VENV_DIR%\Scripts\pythonw.exe"
set "APP=%APP_DIR%bildfahrplan_app.py"

if not exist "%VENV_DIR%\" (
    echo FEHLER: Die virtuelle Python-Umgebung wurde nicht gefunden:
    echo   "%VENV_DIR%"
    echo.
    echo Bitte zuerst im Anwendungsordner eine .venv erstellen und die
    echo Abhaengigkeiten aus requirements.txt installieren.
    pause
    exit /b 1
)

if not exist "%PYTHONW%" (
    echo FEHLER: pythonw.exe wurde in der virtuellen Umgebung nicht gefunden:
    echo   "%PYTHONW%"
    echo.
    echo Bitte die .venv mit einer vollstaendigen Windows-Python-Installation neu erstellen.
    pause
    exit /b 1
)

if not exist "%APP%" (
    echo FEHLER: Die Bildfahrplan-Anwendung wurde nicht gefunden:
    echo   "%APP%"
    pause
    exit /b 1
)

pushd "%APP_DIR%"
start "StellwerkSim Bildfahrplan" "%PYTHONW%" "%APP%" --host 127.0.0.1 --port 3691
set "RESULT=%ERRORLEVEL%"
popd

if not "%RESULT%"=="0" (
    echo FEHLER: Die Bildfahrplan-Anwendung konnte nicht gestartet werden.
    pause
    exit /b %RESULT%
)

endlocal
exit /b 0
