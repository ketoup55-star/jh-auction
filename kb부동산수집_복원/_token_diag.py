# -*- coding: utf-8 -*-
"""accessToken 자동추출 + 서명 API 통과 여부 진단 GUI.

카카오 이메일/비번을 이 창에만 입력 -> 로그인 -> kbland 세션의 토큰 후보를
모두 덤프하고, 찾은 토큰으로 단지 매매 API(propList/main)를 실제 호출해본다.
결과는 _token_diag.log 에 기록된다. (비밀번호는 로그에 남기지 않음)
"""
from __future__ import annotations
import os, sys, traceback, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = r"C:\Users\red85\스피드옥션 크롤러"
sys.path.insert(0, HERE)
sys.path.insert(0, PROJECT)  # kb_api, kb_sign 재사용

LOG = os.path.join(HERE, "_token_diag.log")

def log(msg):
    line = f"[{datetime.datetime.now():%H:%M:%S}] {msg}"
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line)

def run_diag(email, password):
    open(LOG, "w", encoding="utf-8").close()
    log("=== 진단 시작 ===")
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWT
        import fetch
        with sync_playwright() as pw:
            browser = pw.chromium.launch(**fetch._make_chromium_launch_options(False))
            try:
                ctx = browser.new_context(locale="ko-KR", viewport=fetch.PLAYWRIGHT_VIEWPORT)
                page = ctx.new_page()
                log("카카오 로그인 시도...")
                fetch._perform_kakao_login(page, email, password, fetch.LOGIN_TIMEOUT_MS, PWT)
                log("카카오 OAuth 완료. KB siteToken 발급 대기(최대 60초)...")
                log("  (추가 동의/약관 화면이 뜨면 브라우저에서 직접 진행해주세요)")

                member = fetch.wait_for_kbland_login(page, timeout_ms=60000)
                log(f"vuex.member.isLogin = {member.get('isLogin')}")
                # member 의 키/값 요약 덤프 (토큰류는 길이만)
                for k, v in (member or {}).items():
                    if isinstance(v, str) and len(v) > 40:
                        log(f"  member[{k}] = <len {len(v)}> {v[:24]}...")
                    else:
                        log(f"  member[{k}] = {v}")

                token = fetch._extract_access_token(page, ctx)
                log(f"_extract_access_token 결과: {'(찾음, len=%d)' % len(token) if token else '(못 찾음)'}")
                if not token:
                    log("  -- localStorage 키 목록(진단) --")
                    keys = page.evaluate("() => Object.keys(localStorage)")
                    log("  " + ", ".join(keys or []))
            finally:
                browser.close()

        if not token:
            log("토큰 미발견 -> 위 덤프에서 TOKEN? 표시된 키를 알려주세요.")
            return
        # 서명 API 실제 호출 테스트
        log("서명 API 호출 테스트 (단지 11433, 매매)...")
        import kb_api
        client = kb_api.KBClient(token)
        data = client.list_properties("11433", trade_code="1", page=1, page_size=5)
        pl = data.get("propertyList") or []
        total = data.get("총매물건수")
        log(f"  -> propertyList {len(pl)}건 수신, 총매물건수={total}")
        if pl:
            p = pl[0]
            log(f"  샘플: 단지/건물명={p.get('건물명') or p.get('건물동명')} 매매가={p.get('매매가')} 중개={p.get('중개업소명')}")
            log("=== 성공: 자동 토큰이 서명 API에 통합니다 ===")
        else:
            log("=== 토큰은 받았으나 매물 0건 (토큰 무효 또는 단지 매물없음 가능) ===")
    except Exception as e:
        log(f"ERROR: {e}")
        log(traceback.format_exc())

def main():
    import tkinter as tk
    root = tk.Tk(); root.title("토큰 진단"); root.geometry("360x200")
    tk.Label(root, text="카카오 이메일").pack(anchor="w", padx=12, pady=(12,0))
    e_email = tk.Entry(root, width=40); e_email.pack(padx=12)
    tk.Label(root, text="카카오 비밀번호").pack(anchor="w", padx=12, pady=(8,0))
    e_pw = tk.Entry(root, width=40, show="*"); e_pw.pack(padx=12)
    status = tk.Label(root, text="", fg="blue"); status.pack(pady=6)
    def on_click():
        status.config(text="실행 중... 브라우저 확인하세요"); root.update()
        run_diag(e_email.get().strip(), e_pw.get())
        status.config(text="완료. _token_diag.log 확인")
    tk.Button(root, text="토큰 추출 + API 테스트", command=on_click).pack(pady=8)
    root.mainloop()

if __name__ == "__main__":
    main()
