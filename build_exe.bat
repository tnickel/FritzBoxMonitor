@echo off
rem Baut die standalone SolarMonitor.exe nach dist\SolarMonitor.exe
rem Benoetigt wird nur das Projekt-venv (uv pip install pyinstaller)
cd /d "%~dp0"
".venv\Scripts\python.exe" -m PyInstaller SolarMonitor.spec --noconfirm --clean
echo.
echo Fertig: dist\SolarMonitor.exe
