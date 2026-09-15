@echo off
rem SPTSoloStart launcher (silent, no console window)
rem Uses pythonw if available; falls back to python
cd /d "%~dp0"

where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "%~dp0spt_solostart.py"
    exit /b 0
)

where python >nul 2>nul
if %errorlevel%==0 (
    start "" /min python "%~dp0spt_solostart.py"
    exit /b 0
)

rem Fallback: try common Python install paths
for %%P in (
    "C:\Program Files\Python311\pythonw.exe"
    "C:\Program Files\Python312\pythonw.exe"
    "C:\Program Files\Python310\pythonw.exe"
) do (
    if exist %%P (
        start "" %%P "%~dp0spt_solostart.py"
        exit /b 0
    )
)

echo [ERROR] pythonw/python not found. Install Python 3.11+ first.
pause
