@echo off
rem Run gongmae buy-grade warmer (launched hidden by warm_gongmae_hidden.vbs)
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
"C:\Users\red85\AppData\Local\Python\pythoncore-3.14-64\python.exe" -X utf8 warm_gongmae_grade.py >> _warm_gongmae.log 2>&1
