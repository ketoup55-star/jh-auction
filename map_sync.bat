@echo off
rem 경공매 지도 map_points 일일 동기화 (신규 진행중 물건 좌표 추가 + 비활성 제거)
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
"C:\Users\red85\AppData\Local\Python\pythoncore-3.14-64\python.exe" backfill_map_points.py --workers 12 >> _map_sync.log 2>&1
