@echo off
cd /d d:\ptb\dev_ptb\dev_be
if exist venv\Scripts\activate.bat (
    call venv\Scripts\activate.bat
    python app.py
) else (
    python app.py
)
pause
