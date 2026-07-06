# -*- coding: utf-8 -*-
"""kbland 로그인 버튼 셀렉터 진단 — kb_crawler의 로그인 자동화가 어디서 막히는지 파악.
헤드리스로 kbland 접속 → 스크린샷 + 로그인 관련 요소 존재 여부 출력."""
import sys
import os
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
from playwright.sync_api import sync_playwright

SD = r"C:\Users\red85\AppData\Local\Temp\claude\C--Users-red85-OneDrive-Desktop-capcut\38e5f773-4a7a-4aca-b6c6-792018ad3a8e\scratchpad"

with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True)
    ctx = b.new_context(locale="ko-KR", viewport={"width": 1440, "height": 1080})
    page = ctx.new_page()
    page.goto("https://kbland.kr/", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(4000)
    page.screenshot(path=os.path.join(SD, "kb_home.png"))
    print("URL:", page.url)
    print("TITLE:", page.title())

    # 팝업 닫기 시도
    try:
        page.locator(".homePopupcon.open .btn.btn-close").first.click(timeout=2000)
        print("팝업 닫음")
    except Exception:
        print("팝업 없음/못닫음")

    # kb_crawler가 쓰는 셀렉터들 존재 여부
    checks = [
        ('role=button name=로그인하기', lambda: page.get_by_role("button", name="로그인하기", exact=True).count()),
        ('role=button name=메뉴', lambda: page.get_by_role("button", name="메뉴", exact=True).count()),
        ('.btn.btn-login.kakao', lambda: page.locator(".btn.btn-login.kakao").count()),
        ('text=로그인', lambda: page.get_by_text("로그인").count()),
        ('a/button 로그인 포함', lambda: page.locator("a,button").filter(has_text="로그인").count()),
    ]
    for name, fn in checks:
        try:
            print(f"  [{name}] count = {fn()}")
        except Exception as e:
            print(f"  [{name}] ERR {e}")

    # 로그인 관련 클릭가능 요소 텍스트 나열
    print("--- 클릭가능 요소 중 '로그인/카카오' 텍스트 ---")
    els = page.locator("a,button")
    n = min(els.count(), 200)
    seen = set()
    for i in range(n):
        try:
            t = (els.nth(i).inner_text(timeout=500) or "").strip()
        except Exception:
            t = ""
        if t and ("로그인" in t or "카카오" in t or "kakao" in t.lower()) and t not in seen:
            seen.add(t)
            print("   •", repr(t[:40]))
    page.screenshot(path=os.path.join(SD, "kb_home_full.png"), full_page=False)
    b.close()
print("DONE")
