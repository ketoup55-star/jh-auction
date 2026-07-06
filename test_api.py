"""
REST API 검증 테스트 (TestClient, 서버 없이).

    python test_api.py
"""

import sys
sys.stdout.reconfigure(encoding="utf-8")

from fastapi.testclient import TestClient
from api.main import app


def check(label, cond):
    print(("  ✅ " if cond else "  ❌ ") + label)
    return cond


def main():
    ok = True
    with TestClient(app) as c:
        print("[GET /] 홈 화면 + [GET /api] 정보")
        home = c.get("/")
        ok &= check("홈 HTML 서빙(간편검색)",
                    home.status_code == 200 and "간편검색" in home.text)
        r = c.get("/api").json()
        ok &= check(f"적재 4건 (got {r['count']})", r["count"] == 4)

        print("[GET /properties] 전체/필터")
        allp = c.get("/properties").json()
        ok &= check(f"전체 4건 (got {allp['count']})", allp["count"] == 4)
        villa = c.get("/properties", params={"type": "빌라"}).json()
        ok &= check("빌라 1건", villa["count"] == 1)
        seoul = c.get("/properties", params={"region": "서울특별시"}).json()
        ok &= check("서울 1건", seoul["count"] == 1)
        cheap = c.get("/properties", params={"max_price": 300_000_000}).json()
        ok &= check("3억 이하 2건", cheap["count"] == 2)
        # 제외된 물건은 조회 불가
        excluded = c.get("/properties", params={"court": "부산"}).json()
        ok &= check("선순위가등기(부산) 미적재", excluded["count"] == 0)

        print("[GET /properties] 종합검색 필터")
        multi = c.get("/properties", params=[("type", "아파트"), ("type", "빌라")]).json()
        ok &= check("다중 유형(아파트+빌라) 2건", multi["count"] == 2)
        imp = c.get("/properties", params={"auction_type": "강제경매"}).json()
        ok &= check("강제경매 1건(도시형)", imp["count"] == 1)
        apr = c.get("/properties", params={"appraisal_min": 500_000_000}).json()
        ok &= check("감정가 5억 이상 2건", apr["count"] == 2)  # 아파트12억,상가5.5억
        fc = c.get("/properties", params={"failed_min": 2}).json()
        ok &= check("유찰 2회 이상 3건", fc["count"] == 3)
        ba = c.get("/properties", params={"building_area_min": 100}).json()
        ok &= check("건물 100㎡ 이상 1건(상가주택)", ba["count"] == 1)
        kw = c.get("/properties", params={"keyword": "강남"}).json()
        ok &= check("소재지 '강남' 검색 1건", kw["count"] == 1)
        srt = c.get("/properties", params={"sort": "감정가높은"}).json()
        ok &= check("감정가높은순 첫 물건=아파트12억",
                    srt["items"][0]["appraisal_value"] == 1_200_000_000)
        page2 = c.get("/static/search.html")
        ok &= check("검색화면 서빙", page2.status_code == 200 and "종합검색" in page2.text)

        print("[GET /properties] 통계(물건통계·용도통계)")
        st = c.get("/properties").json()["stats"]
        ok &= check("전체 통계 4건", st["status_counts"]["전체"] == 4)
        ok &= check("유찰 3건", st["status_counts"].get("유찰") == 3)
        ok &= check("재진행 1건", st["status_counts"].get("재진행") == 1)
        ok &= check("용도통계 4종", len(st["type_counts"]) == 4)
        bratio = c.get("/properties").json()["items"][0]["bid_ratio"]
        ok &= check(f"유찰% 계산값 존재 (got {bratio})", bratio > 0)

        print("[GET /properties/{case_no}] 상세 + 권리분석 + 배당")
        d = c.get("/properties/2026타경1002").json()  # 빌라, 선순위 대항력 임차인
        # 대항력 임차인 존재 → 전문가 검토 + 경고. 최저가(2.1억)에선 배당으로 전액 회수.
        ok &= check("대항력 임차인 → 전문가검토",
                    d["analysis"]["needs_expert_review"] is True)
        ok &= check("경고 존재", len(d["analysis"]["warnings"]) > 0)
        rec = d["distribution"]["tenant_recoveries"][0]
        ok &= check("최저가 2.1억에선 보증금 1.8억 전액 회수",
                    rec["received"] == 180_000_000 and rec["buyer_assumes"] == 0)
        print(f"    위험도: {d['analysis']['risk_level']} / "
              f"최저가배당 낙찰자인수: {d['distribution']['buyer_assumed_total']:,}원")

        print("[GET /properties/없음] 404")
        ok &= check("404 반환", c.get("/properties/없는사건").status_code == 404)

        print("[POST /analyze] 즉석 분석 + 배당")
        body = {
            "case_no": "테스트",
            "rights": [
                {"type": "근저당권", "reg_date": "2024-03-10",
                 "holder": "○○은행", "amount": 80_000_000},
            ],
            "tenants": [
                {"name": "김소액", "move_in_date": "2024-04-01",
                 "fixed_date": "2024-05-01", "deposit": 60_000_000,
                 "demanded_distribution": True},
            ],
            "sale_price": 100_000_000,
            "region": "그밖의지역",
        }
        a = c.post("/analyze", json=body).json()
        recovered = a["distribution"]["tenant_recoveries"][0]["received"]
        ok &= check(f"소액최우선 2,500만 회수 (got {recovered:,})",
                    recovered == 25_000_000)

        print("[POST /analyze] 매각가 민감도 — 낮은 낙찰가면 인수 발생")
        low = {
            "case_no": "민감도",
            "rights": [{"type": "근저당권", "reg_date": "2022-06-20",
                        "holder": "□□은행", "amount": 150_000_000}],
            "tenants": [{"name": "이대항", "move_in_date": "2021-11-01",
                         "fixed_date": "2021-11-01", "deposit": 180_000_000,
                         "demanded_distribution": True}],
            "sale_price": 150_000_000, "region": "그밖의지역",
        }
        la = c.post("/analyze", json=low).json()
        assumed = la["distribution"]["buyer_assumed_total"]
        ok &= check(f"1.5억 낙찰 시 3천만 인수 (got {assumed:,})",
                    assumed == 30_000_000)

        print("[GET /properties/{case_no}/distribution] 슬라이더 재계산")
        # 빌라(선순위 임차인) 매각가를 낮추면 인수 발생
        full = c.get("/properties/2026타경1002/distribution",
                     params={"sale_price": 210_000_000}).json()
        ok &= check("최저가 2.1억 → 인수 0",
                    full["buyer_assumed_total"] == 0)
        low = c.get("/properties/2026타경1002/distribution",
                    params={"sale_price": 150_000_000}).json()
        ok &= check(f"1.5억 → 3천만 인수 (got {low['buyer_assumed_total']:,})",
                    low["buyer_assumed_total"] == 30_000_000)

        print("[GET /static/detail.html] 상세 화면 서빙")
        page = c.get("/static/detail.html")
        ok &= check("HTML 200 + 본문 포함",
                    page.status_code == 200 and "권리분석" in page.text)

        print("[POST /analyze] 잘못된 권리종류 → 422")
        bad = c.post("/analyze", json={"rights": [
            {"type": "이상한권리", "reg_date": "2024-01-01"}]})
        ok &= check("422 반환", bad.status_code == 422)

    print("-" * 70)
    print("🎉 API 전체 통과" if ok else "⚠️ API 일부 실패")


if __name__ == "__main__":
    main()
