@echo off
echo STARTING DEBUG BATCH
cd /d d:\ptb\dev_ptb\dev_be
call venv\Scripts\activate.bat
echo ACTIVATED VENV
python --version
echo PIP LIST:
pip list
echo RUNNING SIMPLE PYTHON:
python -u -c "print('Python sanity check')"
echo RUNNING APP.PY:
python -u app.py
echo BATCH FINISHED
