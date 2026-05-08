@echo off
setlocal
if exist .venv\Scriptsctivate.bat (
    call .venv\Scriptsctivate.bat
)
python -m app.worker
