"""법정동코드 데이터셋 로더.

원본 lawd_data.py는 거대한 LAWD_CD_LIST 리터럴이었으나, 복원본에서는
같은 폴더의 lawd_data.json(원본 EXE에서 그대로 추출)에서 로드한다.
구조: [{ "code", "fullCode", "name", "sigungu": [
          { "code", "fullCode", "name", "eupmyeondong": [
              { "code", "fullCode", "name" }, ... ]}, ... ]}, ... ]
"""
from __future__ import annotations

import json
from pathlib import Path

_JSON_PATH = Path(__file__).resolve().parent / "lawd_data.json"

with _JSON_PATH.open("r", encoding="utf-8") as _f:
    LAWD_CD_LIST = json.load(_f)
