@echo off
setlocal
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" "%~dp0demo.py" %*
) else (
    where py.exe >nul 2>&1
    if errorlevel 1 (
        python "%~dp0demo.py" %*
    ) else (
        py -3.12 "%~dp0demo.py" %*
    )
)
if errorlevel 1 pause
