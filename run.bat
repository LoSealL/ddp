@echo off
echo === DDP: Installing dependencies ===
pip install -q -r requirements.txt
if errorlevel 1 (
    echo Failed to install dependencies.
    pause
    exit /b 1
)
echo.
echo === DDP: Starting server on http://localhost:8000 ===
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
pause
