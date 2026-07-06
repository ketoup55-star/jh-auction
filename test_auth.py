"""
회원/로그인/관심물건 검증 테스트.

    python test_auth.py

A) UserStore 단위(해싱·세션·관심물건)
B) API 흐름(회원가입→me→관심물건→로그아웃→401)
"""

import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
os.environ["AUCTION_AUTH_DB"] = ":memory:"   # 앱 user_store도 메모리로
os.environ["KAKAO_REST_KEY"] = "testkey"     # 카카오 리다이렉트 테스트용

from auction_analysis.auth import UserStore, hash_password, verify_password
from fastapi.testclient import TestClient
from api.main import app


def check(label, cond):
    print(("  ✅ " if cond else "  ❌ ") + label)
    return cond


def test_store():
    print("[A] UserStore 단위")
    ok = True
    us = UserStore(":memory:")
    # 비밀번호 해싱
    h = hash_password("secret123")
    ok &= check("해시 검증 성공", verify_password("secret123", h))
    ok &= check("틀린 비번 거부", not verify_password("wrong", h))
    # 가입
    u = us.create_user("Test@Example.com", "secret123", "홍길동")
    ok &= check("가입 이메일 소문자화", u["email"] == "test@example.com")
    ok &= check("기본 등급 무료", u["grade"] == "무료")
    # 중복/약한 비번
    try:
        us.create_user("test@example.com", "secret123"); dup = False
    except ValueError: dup = True
    ok &= check("중복 이메일 거부", dup)
    try:
        us.create_user("a@b.com", "123"); weak = False
    except ValueError: weak = True
    ok &= check("짧은 비번 거부", weak)
    # 인증
    ok &= check("로그인 성공", us.authenticate("test@example.com", "secret123") is not None)
    ok &= check("로그인 실패(틀린 비번)", us.authenticate("test@example.com", "x") is None)
    # 세션
    tok = us.create_session(u["id"])
    ok &= check("세션 조회", us.get_user_by_session(tok)["id"] == u["id"])
    us.delete_session(tok)
    ok &= check("세션 삭제 후 None", us.get_user_by_session(tok) is None)
    # 관심물건
    us.add_favorite(u["id"], "2026타경1001")
    us.add_favorite(u["id"], "2026타경1001")  # 중복 무시
    us.add_favorite(u["id"], "2026타경1002")
    ok &= check("관심물건 2건", len(us.list_favorites(u["id"])) == 2)
    ok &= check("is_favorite True", us.is_favorite(u["id"], "2026타경1001"))
    us.remove_favorite(u["id"], "2026타경1001")
    ok &= check("관심물건 삭제 후 1건", len(us.list_favorites(u["id"])) == 1)
    # 소셜(카카오) 회원
    s1 = us.get_or_create_social_user("kakao", "9001", "kk@kakao.com", "카카오유저")
    ok &= check("소셜 회원 생성", s1["provider"] == "kakao")
    s1b = us.get_or_create_social_user("kakao", "9001", "kk@kakao.com", "카카오유저")
    ok &= check("같은 카카오ID 재로그인 동일 회원", s1b["id"] == s1["id"])
    s2 = us.get_or_create_social_user("kakao", "9002")  # 이메일 미동의
    ok &= check("다른 카카오ID 신규 + 합성이메일",
                s2["id"] != s1["id"] and s2["email"].endswith("@kakao.local"))
    ok &= check("소셜회원 이메일/비번 로그인 불가",
                us.authenticate("kk@kakao.com", "") is None)
    return ok


def test_api():
    print("[B] API 인증 흐름")
    ok = True
    with TestClient(app) as c:
        # 비로그인 me → 401
        ok &= check("비로그인 /auth/me 401", c.get("/auth/me").status_code == 401)
        # 회원가입
        r = c.post("/auth/signup", json={"email": "user@test.com",
                                         "password": "pass1234", "name": "김경매"})
        ok &= check("회원가입 200", r.status_code == 200)
        ok &= check("쿠키 발급", "sid" in c.cookies)
        # 로그인 상태 me
        me = c.get("/auth/me")
        ok &= check("로그인 후 /auth/me 성공", me.status_code == 200 and me.json()["name"] == "김경매")
        # 중복 가입 409
        dup = c.post("/auth/signup", json={"email": "user@test.com", "password": "pass1234"})
        ok &= check("중복가입 409", dup.status_code == 409)
        # 관심물건 추가/조회
        add = c.post("/favorites/2026타경1002")
        ok &= check("관심물건 추가", add.status_code == 200 and add.json()["favorite"])
        favs = c.get("/favorites").json()
        ok &= check("관심물건 목록 1건", favs["count"] == 1 and favs["items"][0]["case_no"] == "2026타경1002")
        st = c.get("/favorites/2026타경1002/status").json()
        ok &= check("관심물건 상태 True", st["logged_in"] and st["favorite"])
        # 없는 물건 관심 추가 404
        ok &= check("없는 물건 관심 404", c.post("/favorites/없는사건").status_code == 404)
        # 관심물건 삭제
        c.delete("/favorites/2026타경1002")
        ok &= check("삭제 후 0건", c.get("/favorites").json()["count"] == 0)
        # 로그아웃 → me 401
        c.post("/auth/logout")
        ok &= check("로그아웃 후 /auth/me 401", c.get("/auth/me").status_code == 401)
        ok &= check("로그아웃 후 관심물건 401", c.get("/favorites").status_code == 401)
        # 재로그인
        lg = c.post("/auth/login", json={"email": "user@test.com", "password": "pass1234"})
        ok &= check("재로그인 성공", lg.status_code == 200)
        ok &= check("틀린 비번 로그인 401",
                    c.post("/auth/login", json={"email": "user@test.com", "password": "x"}).status_code == 401)
        # 카카오 로그인 리다이렉트 (키 설정됨)
        kr = c.get("/auth/kakao/login", follow_redirects=False)
        loc = kr.headers.get("location", "")
        ok &= check("카카오 인증 리다이렉트",
                    kr.status_code == 303 and "kauth.kakao.com" in loc and "testkey" in loc)
    return ok


def main():
    results = [test_store(), test_api()]
    print("-" * 70)
    print(("🎉 전체 통과" if all(results) else "⚠️ 일부 실패") + f"  ({sum(results)}/{len(results)})")


if __name__ == "__main__":
    main()
