@echo off
setlocal
pushd "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" desktop-ui.py
) else (
    python desktop-ui.py
)
if errorlevel 1 pause
popd
endlocal
