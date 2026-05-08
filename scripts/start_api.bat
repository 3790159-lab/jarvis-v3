@echo off
setlocal
if exist .venv\Scripts\activate.bat (
    call .venv\Scripts\activate.bat
)
if "%APP_PORT%"=="" set APP_PORT=8015
uvicorn app.main:app --reload --port %APP_PORT%
