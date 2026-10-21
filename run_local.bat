@echo off
setlocal
if not exist ".venv\Scripts\python.exe" (
  echo Virtual environment not found. Run: python -m venv .venv
  echo Then run: .\.venv\Scripts\python.exe -m pip install -r requirements.txt
  pause
  exit /b 1
)
".venv\Scripts\python.exe" run_local.py
if errorlevel 1 pause
endlocal
