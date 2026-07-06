"""경매 차량외 매물 ↔ Neon reviews(전문가 리뷰·실사용자 후기) 매칭.

reviews 테이블(manufacturer·model·title·oneliner·rating·pic·used_price*·
content_main(jsonb)·short_eval·faq·summary_json)에서 경매차 모델에 맞는 후기를 찾는다.
 - 모델 매칭은 encar_match._model_terms(별칭·괄호·수입차 보정) + 단어경계 재사용.
 - 같은 모델의 여러 세대가 잡히면 경매차 '연식'에 가장 가까운 세대를 고른다.
"""

from __future__ import annotations

import re

from .encar_match import _conn, _model_terms, _year4

_BENZ_RE = re.compile(r"mercedes|benz|벤츠|amg", re.I)
# 벤츠 차급 코드(트림 앞 영문) → 후기DB 표기 '○-클래스'. A35·C63·GLC43 같은 영문/AMG 트림명 보정용.
_BENZ_CLASSES = {"A", "B", "C", "E", "S", "G", "V", "R", "CL", "GL", "ML", "SL", "SLC", "SLK",
                 "CLA", "CLS", "GLA", "GLB", "GLC", "GLE", "GLK", "GLS",
                 "EQA", "EQB", "EQC", "EQE", "EQS"}


def _benz_class_terms(name: str) -> list[str]:
    """벤츠/AMG 차명 → 한글 차급 검색어(A35→'A-클래스', GLC43→'GLC-클래스').
    후기DB가 벤츠 모델을 'A-클래스 W176'처럼 한글 차급으로 저장해, 영문 트림명과 안 이어지는 문제 보정."""
    if not name or not _BENZ_RE.search(name):
        return []
    out: list[str] = []

    def addc(cls: str):
        cls = (cls or "").upper()
        if cls in _BENZ_CLASSES and (cls + "-클래스") not in out:
            out.append(cls + "-클래스")

    for m in re.findall(r"([A-Za-z]{1,3})[ -]?\d{2,4}", name):   # A35·GLC43·E200 → 차급문자
        addc(m)
    for tok in re.split(r"[ /\-]+", name):                       # 숫자 없는 차급(GLA·CLA 등)
        addc(tok)
    return out


# summary_json.mochaDetailContentMap 카테고리 → 한글 라벨(표시 순서)
_CAT_LABEL = [
    ("Design", "디자인"), ("Performance", "주행성능"), ("Maintenance", "유지비·연비"),
    ("Safety", "안전"), ("Dimension", "공간"), ("Purchase", "가격·구매"),
]


def _review_view(r: dict) -> dict:
    """reviews 1행 → 화면용 축약(이미지 더미·중복 제거)."""
    cm = r.get("content_main") or {}
    sj = r.get("summary_json") or {}
    mocha = sj.get("mochaDetailContentMap") or {}
    cats = []
    for key, lab in _CAT_LABEL:
        m = mocha.get(key) or {}
        sc = m.get("score")
        if sc not in (None, "", "0", 0) and m.get("chapterSummary"):
            cats.append({"key": lab, "score": _int(sc), "title": m.get("evaluateTitle"),
                         "summary": m.get("chapterSummary")})
    syn = mocha.get("Synthesis") or {}
    comments = []
    for cc in (sj.get("contentsComments") or []):
        if cc.get("cont"):
            comments.append({"rating": _int(cc.get("rat")), "text": cc.get("cont"),
                             "date": (cc.get("rgsdate") or cc.get("rgsdt") or "")[:10]})
        if len(comments) >= 8:
            break
    faqs = [{"q": f.get("question"), "a": f.get("answer")}
            for f in (r.get("faq") or []) if f.get("question")][:5]
    return {
        "idbid": r.get("idbid"), "manufacturer": r.get("manufacturer"), "model": r.get("model"),
        "title": r.get("title"), "oneliner": r.get("oneliner"), "rating": _int(r.get("rating")),
        "pic": r.get("pic"), "customer_score": _int(r.get("customer_score")),
        "price": {"low": _int(r.get("used_price_low")), "mid": _int(r.get("used_price")),
                  "high": _int(r.get("used_price_high"))},
        "year": cm.get("yr"), "fuel": cm.get("fuelnm"), "displacement": cm.get("dsp"),
        "synthesis_title": syn.get("evaluateTitle"),
        "synthesis": syn.get("synthesisContent"),
        "categories": cats, "comments": comments, "faq": faqs,
    }


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def match_reviews(fields: dict) -> dict:
    """차량 필드(dict label→value) → 해당 차종 전문가/사용자 후기(연식 근접 베스트 1건)."""
    name = fields.get("차명")
    terms = _model_terms(name)
    terms += [t for t in _benz_class_terms(name) if t not in terms]   # 벤츠 트림→차급(A35→A-클래스)
    if not terms:
        return {"available": False, "reason": "차명(모델) 정보 없음"}
    year = _year4(fields.get("연식"))
    fuel = re.sub(r"\s+", "", str(fields.get("사용연료") or ""))

    ors, params = [], []
    for t in terms:                          # 모델 단어경계 매칭(부분일치 금지)
        #  경계: 한글뿐 아니라 영문·숫자도 경계로 — 'A-클래스'가 'GLA-클래스'에 오매칭되는 것 방지
        pat = r"(^|[^0-9A-Za-z가-힣])" + re.escape(t) + r"($|[^0-9A-Za-z가-힣])"
        ors.append("model ~* %s"); params.append(pat)
    sql = ("SELECT idbid,manufacturer,model,title,oneliner,rating,pic,customer_score,"
           "used_price_low,used_price,used_price_high,content_main,faq,summary_json "
           "FROM reviews WHERE (" + " OR ".join(ors) + ")")
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    except Exception as e:
        return {"available": False, "reason": f"후기 조회 실패: {type(e).__name__}"}
    if not rows:
        return {"available": False, "reason": "해당 차종 후기 없음", "model": terms[0]}

    def rank(r):
        cm = r.get("content_main") or {}
        ry = _int(cm.get("yr"))
        ydiff = abs((year or ry or 0) - (ry or year or 0)) if (year and ry) else 99
        fmatch = 0 if (fuel and cm.get("fuelnm") and cm["fuelnm"][:2] in fuel) else 1
        return (ydiff, fmatch, -(len(r.get("model") or "")))   # 연식근접 → 연료일치 → 구체적모델
    best = sorted(rows, key=rank)[0]
    others = [{"idbid": r.get("idbid"), "model": r.get("model"),
               "year": (r.get("content_main") or {}).get("yr")}
              for r in sorted(rows, key=rank)[1:6]]
    out = {"available": True, "review": _review_view(best), "match_count": len(rows)}
    if others:
        out["others"] = others                # 다른 세대 후기(있으면 참고)
    return out
