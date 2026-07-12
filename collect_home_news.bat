@echo off
rem Run home-news collector (launched hidden by collect_home_news_hidden.vbs)
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
"C:\Users\red85\AppData\Local\Python\pythoncore-3.14-64\python.exe" collect_home_news.py >> _home_news.log 2>&1
