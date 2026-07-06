# -*- coding: utf-8 -*-
"""KB 카카오 자동로그인 헬퍼 — 독립 프로세스로 실행(서버 스레드에서 playwright sync API를 직접 돌리면
멈추는 제약을 회피). KB_EMAIL/KB_PW(.env)로 kbland 카카오 로그인 → {token, headers} JSON을
마지막 줄에 출력. 서버(_kb_issue_token)가 subprocess로 호출해 결과를 파싱한다.

단독 실행도 가능:  python kb_kakao_login.py   (창이 떠서 자동로그인, 캡차는 사람이 처리)
"""
import sys
import os
import json

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# .env 로드(KB_EMAIL/KB_PW)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except Exception:
    pass

import kb_crawler

email = os.environ.get("KB_EMAIL", "")
pw = os.environ.get("KB_PW", "")
token, headers = None, None
try:
    if not email or not pw:
        print("NO_CREDENTIALS: KB_EMAIL/KB_PW 미설정", file=sys.stderr)
    else:
        token, headers = kb_crawler._kakao_login_capture(email, pw)
except Exception as e:  # noqa: BLE001
    import traceback
    print("LOGIN_ERROR:", e, file=sys.stderr)
    traceback.print_exc()

# 마지막 줄에 JSON(서버가 이 줄을 파싱)
print(json.dumps({"token": token, "headers": headers or {}}, ensure_ascii=False))
