# -*- coding: utf-8 -*-
"""아파트 매매 매물 수집기 (단지 기반) — 복원 크롤러 확장 기능.

기존(수동 토큰) 한계를 없애고, 복원 크롤러의 카카오 자동로그인으로 KB
accessToken(siteToken)을 자동 획득한 뒤, 검증된 프로젝트 파이프라인
(kb_match → kb_api → kb_crawl 적재)을 그대로 구동한다.

흐름:
  카카오 자동로그인 → siteToken 추출 → Supabase 진행중 아파트(items) 조회
  → 주소로 KB단지 매칭 → 단지 매매매물(trade_code=1) 수집 → kb_listing/kb_complex/items 적재

사용:
  py -3.13 kb_apt_collect.py [--limit N] [--dry] [--no-photos] [--random] [--headless]

  계정: 환경변수 KB_EMAIL/KB_PW 가 있으면 사용, 없으면 입력창이 뜬다.
  (비밀번호는 화면 입력창에만 들어가며 로그/인자에 남기지 않는다)
"""
from __future__ import annotations
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = r"C:\Users\red85\스피드옥션 크롤러"
sys.path.insert(0, HERE)         # 복원 fetch.py (자동로그인/토큰추출)
sys.path.insert(0, PROJECT_DIR)  # 검증된 kb_match / kb_api / kb_crawl

import fetch          # noqa: E402  (복원 크롤러)
import kb_crawl       # noqa: E402  (기존 파이프라인)


def prompt_credentials() -> tuple[str, str]:
    """환경변수 우선, 없으면 tkinter 입력창으로 카카오 계정을 받는다."""
    email = os.environ.get("KB_EMAIL", "").strip()
    password = os.environ.get("KB_PW", "")
    if email and password:
        return email, password

    import tkinter as tk
    result = {}
    root = tk.Tk()
    root.title("카카오 로그인")
    root.geometry("340x190")
    tk.Label(root, text="카카오 이메일").pack(anchor="w", padx=12, pady=(14, 0))
    e_email = tk.Entry(root, width=38)
    e_email.pack(padx=12)
    e_email.insert(0, email)
    tk.Label(root, text="카카오 비밀번호").pack(anchor="w", padx=12, pady=(8, 0))
    e_pw = tk.Entry(root, width=38, show="*")
    e_pw.pack(padx=12)

    def on_ok():
        result["email"] = e_email.get().strip()
        result["pw"] = e_pw.get()
        root.destroy()

    e_pw.bind("<Return>", lambda _e: on_ok())
    tk.Button(root, text="로그인하고 수집 시작", command=on_ok).pack(pady=14)
    root.mainloop()
    if not result.get("email") or not result.get("pw"):
        sys.exit("계정 미입력 — 종료")
    return result["email"], result["pw"]


def main():
    ap = argparse.ArgumentParser(description="아파트 매매 매물 수집(단지 기반)")
    ap.add_argument("--limit", type=int, help="처리할 아파트 건수 제한(테스트용)")
    ap.add_argument("--dry", action="store_true", help="매칭만 출력, DB 미적재")
    ap.add_argument("--no-photos", action="store_true", help="매물 사진 수집 생략")
    ap.add_argument("--random", action="store_true", help="무작위 순서(샘플 테스트용)")
    ap.add_argument("--resume", action="store_true",
                    help="이미 시도된 아파트(kb_item_match 존재)는 건너뛰기 → 중단/재개 안전")
    ap.add_argument("--headless", action="store_true",
                    help="로그인 브라우저 숨김(추가 동의가 없을 때만 권장)")
    a = ap.parse_args()

    if a.resume:
        # 기존 kb_crawl 은 수정하지 않고, 모듈 변수 ACTIVE_SQL 만 필터링한다.
        # "\n    order by item_key" 꼬리는 그대로 둬야 --random 치환이 동작한다.
        kb_crawl.ACTIVE_SQL = kb_crawl.ACTIVE_SQL.replace(
            "\n    order by item_key",
            "\n      and item_key not in (select item_key from kb_item_match)"
            "\n    order by item_key",
        )

    email, password = prompt_credentials()

    print("[1/2] 카카오 자동로그인 → KB accessToken 추출 중...")
    # 로그인 단계는 추가 동의 화면 대비 기본 표시(headless=False).
    from playwright.sync_api import sync_playwright, TimeoutError as PWT
    with sync_playwright() as pw:
        browser = pw.chromium.launch(**fetch._make_chromium_launch_options(a.headless))
        try:
            ctx = browser.new_context(locale="ko-KR", viewport=fetch.PLAYWRIGHT_VIEWPORT)
            page = ctx.new_page()
            fetch._perform_kakao_login(page, email, password, fetch.LOGIN_TIMEOUT_MS, PWT)
            member = fetch.wait_for_kbland_login(page, timeout_ms=60000)
            token = fetch._extract_access_token(page, ctx)
        finally:
            browser.close()

    if not token:
        sys.exit("accessToken 추출 실패 — 로그인이 끝까지 완료되지 않았습니다. 다시 시도하세요.")
    print(f"      토큰 획득 완료 (len={len(token)}).")

    print(f"[2/2] 수집 시작 (limit={a.limit or '전체'}, dry={a.dry}, photos={not a.no_photos})")
    stat = kb_crawl.run(
        token,
        limit=a.limit,
        dry=a.dry,
        fetch_photos=not a.no_photos,
        random_order=a.random,
    )
    print(f"[완료] {stat}")


if __name__ == "__main__":
    main()
