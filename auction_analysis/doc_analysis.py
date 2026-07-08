"""
온디맨드 문서 분석: 물건의 등기 PDF를 받아 권리분석을 수행한다.

흐름: item_key → 등기 R2 URL 조회 → PDF 다운로드(텍스트 PDF 확인) →
      텍스트 추출(pdfplumber) → 권리 파싱(registry_parser) → 권리분석(engine).
결과는 메모리 캐시(같은 물건 재요청 시 재파싱 안 함).

주의/한계:
  - 등기 미보유 물건은 available=False.
  - 현황조사서는 R2에 HTML 오류페이지로 저장돼 있어 임차인 추출 불가
    → 본 분석은 '등기 기반 권리분석(말소기준·인수/소멸)'까지만.
"""

from __future__ import annotations

import io
import re

import httpx

import os
import gc
import threading

from .registry_parser import parse_registry, parse_building
from .occupancy_parser import parse_occupancy
from .appraisal_parser import parse_appraisal
from .sale_statement_parser import parse_sale_statement
from .engine import analyze
from .models import AuctionProperty
from .disk_cache import DiskDict, SqliteDict

# 디스크 영구 캐시(재시작에도 유지) — 콜드 재계산(PDF 파싱) 방지
_CDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_cache = SqliteDict(os.path.join(_CDIR, "cache_analysis.db"))         # DiskDict→SqliteDict(2026-06-29): 증분 upsert + 메모리 비상주(통짜 JSON 198MB 로드 제거). 동시 프로세스(샤드) WAL 공유 안전
_appraisal_cache = SqliteDict(os.path.join(_CDIR, "cache_appraisal.db"))
_summary_cache = SqliteDict(os.path.join(_CDIR, "cache_summary.db"))
_DOCSUM_VER = 2   # 캐시 포맷 버전(2=tenant_rents에 전입일·확정일 포함). 불일치 캐시는 1회 재파싱.
_vehicle_cache = SqliteDict(os.path.join(_CDIR, "cache_vehicle.db"))

# PDF 동시 파싱 상한 — _prewarm_docs가 4작업×4워커=최대 16개 PDF(대용량 감정평가서)를 동시에 메모리에
# 올려 4GB+ 폭증·미완주를 유발했음. 세마포어로 동시 다운로드+파싱을 제한해 메모리 상한을 고정.
_PDF_SEM = threading.Semaphore(8)   # 3→8 상향(2026-06-29): flush_cache로 PDF당 메모리가 갇혀(검증 ~50MB/parse) 8 동시여도 ~400MB로 안전. docs 예열 ~2.7배 가속(처리대상은 동일, 속도만)
_pdf_calls = 0
_pdf_calls_lock = threading.Lock()


def flush_caches() -> None:
    """대기 중인 분석 캐시를 디스크로 강제 저장(종료/주기)."""
    for c in (_cache, _appraisal_cache, _summary_cache, _vehicle_cache):
        try:
            c.flush()
        except Exception:
            pass


def evict_item(item_key: str) -> None:
    """크롤러 갱신 시 해당 물건의 문서분석 메모리/디스크 캐시 제거(재계산 유도)."""
    for c in (_cache, _appraisal_cache, _summary_cache, _vehicle_cache):
        try:
            c.pop(item_key, None)
        except Exception:
            pass


_MULTI_RE = re.compile(r"기호\s*[(（]\s*2")


def _mark_multi(r):
    """일괄매각(한 물번에 여러 대) 감지 — 감정평가서 '기타'의 '기호(2)' 표기 → multi_vehicle 플래그 부착.
    여러 대가 한 필드로 뒤섞여 차량현황·시세가 오도되므로 상세는 배지 표시, 시세는 억제(주인님 2026-07-08)."""
    if isinstance(r, dict) and r.get("available"):
        r["multi_vehicle"] = bool(_MULTI_RE.search(str(r.get("etc_note") or "")))
    return r


def analyze_vehicle(source, item_key: str) -> dict:
    """차량외(자동차·중기) 차량/중기현황.
    ① 우선 vehicle_specs DB(크롤러 구조화) → PDF 파싱 없이 즉시.
    ② 행이 없으면 폴백: 매각물건명세서 '자동차의 표시' + 감정평가서 파싱."""
    from .vehicle_parser import build_vehicle, build_vehicle_from_specs
    try:                                    # ① DB(vehicle_specs)
        spec = source.vehicle_specs(item_key)
    except Exception:
        spec = None
    if spec:
        r = build_vehicle_from_specs(spec)
        if r:                               # 사고는 크롤 데이터에 없음 → '확인 필요'(감정평가서 파싱 안 함)
            return _mark_multi(r)
    if item_key in _vehicle_cache:          # ② 폴백(구 PDF 파싱) — 캐시
        return _vehicle_cache[item_key]
    out = {"available": False}
    ms_url = source.media_url(item_key, "매각물건명세서")
    if ms_url:
        try:
            ms_text = _pdf_text(ms_url)
            ap_text = ""
            ap_url = source.media_url(item_key, "감정평가서")
            if ap_url:
                try:
                    ap_text = _pdf_text(ap_url)
                except Exception:
                    ap_text = ""        # 스캔본 감정평가서는 텍스트 없음 → 명세서만
            r = build_vehicle(ms_text, ap_text)
            if r:
                out = r
        except Exception as e:
            out = {"available": False, "reason": type(e).__name__}
    if out.get("available"):
        _mark_multi(out)
        _vehicle_cache.remember(item_key, out)
    return out


def _fmt_senior(s: str) -> str:
    """최선순위 표기 정리: 날짜는 'YYYY.MM.DD.', 권리명은 PDF 줄바꿈 공백 제거.
    예: '2024. 3. 11. 강제경매개 시결정' → '2024.03.11. 강제경매개시결정'."""
    if not s:
        return ""
    m = re.search(r"(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.?", s)
    if not m:
        return re.sub(r"\s+", " ", s).strip()
    date = f"{m.group(1)}.{int(m.group(2)):02d}.{int(m.group(3)):02d}."
    rest = (s[:m.start()] + " " + s[m.end():])
    rest = re.sub(r"\s+", "", rest)            # 한글 권리명 내부 공백 제거
    return f"{date} {rest}".strip()


def analyze_doc_summary(source, item_key: str) -> dict:
    """명세서 요약사항: 최선순위·소멸되지않는권리·지상권·주의사항(비고) + 법원문건접수/송달. 캐시 적용."""
    if item_key in _summary_cache:
        cached = _summary_cache[item_key]
        # 캐시 포맷 버전 불일치면 1회 재파싱(전입일 병합 등 포맷 갱신 반영). 미available 캐시는 그대로.
        if cached.get("_v") == _DOCSUM_VER or not cached.get("available"):
            return cached
    out: dict = {"available": False}
    try:
        ms = _load_sale_statement(source, item_key)
        court = {"available": False, "docs": []}
        url = source.media_url(item_key, "문건접수송달")
        if url:
            try:
                from .court_docs_parser import parse_court_docs
                html = httpx.get(url, timeout=40, follow_redirects=True).text
                court = parse_court_docs(html)
            except Exception:
                pass
        if ms.get("available") or court.get("available"):
            docs = court.get("docs", [])
            docs = sorted(docs, key=lambda d: d.get("date", ""), reverse=True)  # 최신순
            out = {
                "available": True,
                "_v": _DOCSUM_VER,
                "senior_setup": _fmt_senior(ms.get("senior_setup") or ""),
                "surviving_rights": ms.get("surviving_rights") or "",
                "ground_rights": ms.get("ground_rights") or "",
                "caution": ms.get("caution") or "",
                "dividend_deadline": ms.get("dividend_deadline"),
                "court_docs": docs,
                # 명세서 임차인정보(차임·전입일·확정일): 임차인/대항력 패널(item_tenants 기반)이 이름으로
                #  병합. item_tenants(현황조사)가 전입일·차임 미상일 때 명세서 값으로 보완. 차임 없어도 포함.
                "tenant_rents": [{"name": t.name, "rent": getattr(t, "rent", 0) or 0,
                                  "move_in": t.move_in_date.isoformat() if getattr(t, "move_in_date", None) else None,
                                  "fixed": t.fixed_date.isoformat() if getattr(t, "fixed_date", None) else None}
                                 for t in _merge_tenants(ms.get("tenants") or [])],
            }
    except Exception as e:
        out = {"available": False, "reason": type(e).__name__}
    _summary_cache.remember(item_key, out)
    return out


def analyze_appraisal(source, item_key: str) -> dict:
    """감정평가서 기반 물건현황·감정평가현황(dict). 캐시 적용."""
    if item_key in _appraisal_cache:
        return _appraisal_cache[item_key]
    out: dict = {"available": False, "reason": "감정평가서 미확보"}
    url = source.media_url(item_key, "감정평가서")
    if url:
        try:
            text = _pdf_text(url)
            res = parse_appraisal(text)
            out = res if res.get("available") else {"available": False,
                                                    "reason": "감정평가서에서 항목을 추출하지 못함"}
        except Exception as e:
            out = {"available": False, "reason": f"감정평가서 분석 실패: {type(e).__name__}"}
    _appraisal_cache.remember(item_key, out)
    return out


def _pdf_text(url: str) -> str:
    import pdfplumber
    global _pdf_calls
    with _PDF_SEM:                                  # 동시 PDF 다운로드+파싱 ≤3 — 대용량 감정평가서 동시 적재 메모리 폭증 방지
        data = httpx.get(url, timeout=40, follow_redirects=True).content
        if data[:5] != b"%PDF-":
            raise ValueError("PDF 아님(HTML/손상 파일)")
        parts = []
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for p in pdf.pages:
                parts.append(p.extract_text() or "")
                try:
                    p.flush_cache()                # 페이지 객체 캐시 즉시 해제 — pdfplumber 페이지 누적(누수)·대용량 피크 방지
                except Exception:
                    pass
        text = "\n".join(parts)
        del data
    with _pdf_calls_lock:                           # pdfminer 순환참조 주기적 회수(매 호출 gc는 느려 20건마다)
        _pdf_calls += 1
        due = (_pdf_calls % 20 == 0)
    if due:
        gc.collect()
    return text


def _load_sale_statement(source, item_key: str) -> dict:
    """매각물건명세서(PDF) → 임차인(배당요구·보증금)+최선순위/배당종기. 실패 시 빈 결과."""
    url = source.media_url(item_key, "매각물건명세서")
    if not url:
        return {"available": False, "tenants": []}
    try:
        data = httpx.get(url, timeout=40, follow_redirects=True).content
        return parse_sale_statement(data)
    except Exception as e:
        return {"available": False, "tenants": [], "reason": type(e).__name__}


_AGENCY_RE = re.compile(r"공사|공단|은행|보증|관리원|입주자")


def _occupant_key(name: str) -> str:
    """행의 '실제 점유자' 식별키.
    - 승계기관 행: '입주자:○○' 의 ○○ (예: 한국토지주택공사(입주자:장우중) → 장우중)
    - 일반 점유자 행: 앞쪽 (호수)·(층) 접두와 뒤쪽 부가 괄호를 떼낸 사람 이름
      (예: (201호)박정관 → 박정관, 황인희(별지) → 황인희)
    """
    name = name or ""
    m = re.search(r"입주자[:\s]*([가-힣]{2,4})", name)
    if m:
        return m.group(1)
    name = re.sub(r"^\s*\([^)]*\)\s*", "", name)   # 앞쪽 (201호)/(1층) 등 제거
    name = re.sub(r"\(.*$", "", name)               # 뒤쪽 (별지)/(입주자:..) 등 제거
    return name.strip()


def _merge_tenants(ts: list) -> list:
    """매각물건명세서의 '같은 사람'의 여러 행(현황조사·등기·권리신고)을 한 명으로 병합.

    원칙(누락 방지): 점유자 식별키(_occupant_key)가 같은 행끼리만 묶고, 서로 다른 사람은
    전입일이 우연히 같더라도 절대 합치지 않는다. 승계기관(LH/HUG/SGI 등) 행은 '입주자:○○'로
    원임차인에 연결돼 그 기관의 권리신고(보증금·배당요구)가 원임차인 정보로 합쳐진다.
    표에 존재하는 모든 점유자(승계 무관한 독립 임차인 포함)를 빠짐없이 보존한다."""
    from .models import Tenant

    groups: dict[str, list] = {}
    order: list[str] = []
    for t in ts:
        key = _occupant_key(t.name) or (t.name or "").strip() or f"__{len(order)}"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(t)

    out = []
    for key in order:
        grp = groups[key]
        # 대표 이름: 승계기관/입주자표기가 아닌 '사람행' 우선(호수 접두는 표시에 유지)
        person = next((g.name for g in grp if g.name and not _AGENCY_RE.search(g.name)),
                      grp[0].name)
        out.append(Tenant(
            name=person,
            move_in_date=next((g.move_in_date for g in grp if g.move_in_date), None),
            fixed_date=next((g.fixed_date for g in grp if g.fixed_date), None),
            deposit=max((g.deposit for g in grp), default=0),
            rent=max((g.rent for g in grp), default=0),       # 차임(월세): 행 중 최댓값(권리신고 행에 기재)
            demanded_distribution=any(g.demanded_distribution for g in grp),
            demand_date=next((g.demand_date for g in grp if g.demand_date), None),
        ))
    return out


def _load_occupancy(source, item_key: str) -> dict:
    """현황조사서(HTML) → 임차인/점유 정보. 실패 시 빈 결과."""
    url = source.media_url(item_key, "현황조사서")
    if not url:
        return {"tenants": [], "occupancy": "", "notes": [], "raw_count": 0,
                "available": False}
    try:
        t = httpx.get(url, timeout=40, follow_redirects=True).text
        o = parse_occupancy(t)
        o["available"] = True
        return o
    except Exception as e:
        return {"tenants": [], "occupancy": "", "notes": [], "raw_count": 0,
                "available": False, "reason": f"현황조사서 분석 실패: {type(e).__name__}"}


def analyze_registry(source, item_key: str) -> dict:
    """등기+현황조사서 기반 권리분석 결과(dict). 캐시 적용."""
    if item_key in _cache:
        return _cache[item_key]

    out: dict = {"available": False, "reason": "등기 문서 미확보"}
    url = source.media_url(item_key, "등기")
    if url:
        try:
            text = _pdf_text(url)
            rights = parse_registry(text)
            if not rights:
                out = {"available": False, "reason": "등기에서 권리를 추출하지 못함"}
            else:
                # 임차인: 매각물건명세서(배당요구 확정) 우선, 없으면 현황조사서(배당요구 미상)
                occ = _load_occupancy(source, item_key)
                ms = _load_sale_statement(source, item_key)
                if ms.get("available") and ms.get("tenants"):
                    tenants_in = _merge_tenants(ms["tenants"])
                    demanded_known = True
                else:
                    tenants_in = occ["tenants"]
                    demanded_known = False
                ms_deadline = ms.get("dividend_deadline") if ms.get("available") else None
                res = analyze(AuctionProperty(
                    case_no=item_key, rights=rights, tenants=tenants_in))
                out = {
                    "available": True,
                    "baseline": (
                        {"type": res.baseline_right.type.value,
                         "date": res.baseline_right.reg_date.isoformat(),
                         "holder": res.baseline_right.holder}
                        if res.baseline_right else None
                    ),
                    "risk_level": res.risk_level,
                    "needs_expert_review": res.needs_expert_review,
                    "assumed_amount_total": res.assumed_amount_total,
                    "rights": [
                        {"type": v.right.type.value,
                         "date": v.right.reg_date.isoformat(),
                         "holder": v.right.holder,
                         "amount": v.right.amount,
                         "status": v.status,
                         "reason": v.reason}
                        for v in res.right_verdicts
                    ],
                    "tenants": [
                        {"name": tv.tenant.name,
                         "move_in": tv.tenant.move_in_date.isoformat()
                         if tv.tenant.move_in_date else None,
                         "fixed": tv.tenant.fixed_date.isoformat()
                         if tv.tenant.fixed_date else None,
                         "deposit": tv.tenant.deposit,
                         # 배당요구: 매각물건명세서 기준이면 True/False/"late"(종기후 무효), 현황조사서면 None(미상)
                         "demanded": (
                             None if not demanded_known
                             else True if tv.tenant.demanded_distribution
                             else "late" if tv.tenant.demand_date
                             else False),
                         # 배당요구 신청일(종기 후라도 표시용으로 보존)
                         "demand_date": (tv.tenant.demand_date.isoformat()
                                         if tv.tenant.demand_date else None),
                         "has_opposing_power": tv.has_opposing_power,
                         "assume": tv.buyer_assumes_deposit,
                         "reason": tv.reason}
                        for tv in res.tenant_verdicts
                    ],
                    "occupancy": occ.get("occupancy", ""),
                    "occupancy_notes": occ.get("notes", []),
                    "occupancy_available": occ.get("available", False),
                    "dividend_deadline": ms_deadline,        # 매각물건명세서 배당요구종기
                    "senior_setup": ms.get("senior_setup") if ms.get("available") else None,
                    "building": parse_building(text),
                    "warnings": res.warnings,
                }
        except Exception as e:
            out = {"available": False, "reason": f"등기 분석 실패: {type(e).__name__}"}

    _cache.remember(item_key, out)
    return out
