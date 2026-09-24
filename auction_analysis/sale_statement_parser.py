"""
매각물건명세서(PDF) → 임차인(배당요구·보증금·확정일자) + 최선순위설정/배당요구종기 파서.

매각물건명세서는 법원 공식 표(테두리 있음)라 pdfplumber.extract_tables() 로 셀이 깔끔히 추출된다.
임차인 표 컬럼: 점유자성명 | 점유부분 | 정보출처구분 | 점유의권원 | 임대차기간 |
              보증금 | 차임 | 전입신고일자 | 확정일자 | 배당요구여부(배당요구일자)
→ 배당요구일자가 있으면 배당요구 O. 임차인 없으면 '조사된 임차내역없음'.

목적: 현황조사서엔 없는 '배당요구 여부'를 채워 임차인현황/대항력 분석을 완성.
"""

from __future__ import annotations

import io
import re

import pdfplumber

from .models import Tenant
from .codef_adapter import parse_date_kr, parse_amount_kr


def _norm(s) -> str:
    return re.sub(r"\s+", "", (s or ""))


def _cell(s) -> str:
    return _dedouble(re.sub(r"\s+", " ", (s or "").replace("\n", " ")).strip())


def _dedouble(s: str) -> str:
    """일부 PDF/크롤은 글자가 2번씩 중복됨(예: '개개인인정정보보'='개인정보', '22002266..0066'='2026.06').
    줄 단위로 '인접 동일쌍에 속한 글자' 비율이 높으면(≥0.7) 동일쌍을 1글자로 축약해 복원.
    공백이 1칸이라 정렬이 어긋나도 정규식 `(.)\\1→\\1` 이라 안전. 일반 텍스트(중복비율 낮음)는 영향 없음."""
    if not s:
        return s
    out = []
    for line in s.split("\n"):
        if len(line) >= 8:
            collapsed = re.sub(r"(.)\1", r"\1", line)
            # 동일쌍 축약 시 길이가 38% 이상 줄면 '글자 2배 추출'로 보고 복원.
            #  (정상 텍스트는 중복쌍이 적어 거의 안 줄어듦 → 보존). 공백 정렬과 무관해 견고.
            if len(collapsed) <= len(line) * 0.62:
                line = collapsed
        out.append(line)
    return "\n".join(out)


_HAN_DIGIT = {"일": "1", "이": "2", "삼": "3", "사": "4", "오": "5", "육": "6", "칠": "7", "팔": "8", "구": "9"}
_SMALL_UNIT = {"천": 1000, "백": 100, "십": 10}
_BIG_UNIT = {"억": 100_000_000, "만": 10_000}


def won_amounts(text: str) -> list[int]:
    """명세서 금액 칸 → 금액(원) 목록. 숫자 표기('50,000,000원')와 한글 단위('8,000만원'·'3억1천5백만원'·'1억 5,000만원'·
    '월 50만원'·'오천만원')를 모두 읽는다. 한 칸에 금액이 둘 이상이면('155,000,000 157,000,000' 증액, '3,500만원(월 30만원)')
    각각 돌려준다(호출측이 최댓값 등 선택).
    🔴2026-09-18 실측: 예전엔 칸의 숫자만 뽑아 10만 미만을 버려 '8,000만원'(→8000)·'3억1천5백만원'(→3·1·5)이 통째로 버려졌고,
      명세서에 보증금이 적힌 임차인이 '보증금 미상'으로 들어갔다(G01|2025|51805|1 김윤경 8,000만원, J01|2025|742|1 장도석
      3억1천5백만원) — 상세 화면 임차인 카드에 보증금·인수예상액이 안 나오고 보증금 미상 판정에도 잘못 걸렸다."""
    t = (text or "").replace(",", "")
    t = re.sub(r"([일이삼사오육칠팔구])(?=\s*[천백십만억])", lambda m: _HAN_DIGIT[m.group(1)], t)
    out: list[int] = []
    st = {"total": 0.0, "group": 0.0, "big": None, "on": False}

    def flush():
        v = st["total"] + st["group"]
        if st["on"] and v:
            out.append(int(round(v)))
        st.update(total=0.0, group=0.0, big=None, on=False)

    prev_end = 0
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*([천백십]?)\s*([만억]?)", t):
        n, small, big = float(m.group(1)), m.group(2), m.group(3)
        if t[prev_end:m.start()].strip():                   # 앞 금액과 사이에 글자('원'·괄호 등)가 있으면 다른 금액
            flush()                                         #  ('8,000만원 (2023.4.3. 등기)'의 2023을 나머지로 붙이지 않게)
        prev_end = m.end()
        if not small and not big:                           # 단위 없는 숫자
            if st["on"] and st["big"] and n < _BIG_UNIT[st["big"]]:
                st["group"] += n                            # '1억 50,000,000원'의 나머지
                flush()
            else:
                flush()
                st.update(total=n, on=True)
                flush()
            continue
        if big and st["on"] and st["big"] and _BIG_UNIT[big] >= _BIG_UNIT[st["big"]]:
            flush()                                         # 같은·더 큰 큰단위가 다시 나오면 새 금액('3,500만원 30만원')
        st["group"] += n * (_SMALL_UNIT[small] if small else 1)
        st["on"] = True
        if big:
            st["total"] += st["group"] * _BIG_UNIT[big]
            st["group"] = 0.0
            st["big"] = big
    flush()
    return out


def clean_summary(s: str) -> str:
    """명세서 요약 텍스트 정리:
    ① 전자문서 다운로드 워터마크('개인정보유출주의 … 다운로드일시 …') 제거 — doubled/정상 형태 모두.
    ② 남은 글자 2배 추출 복원(_dedouble)."""
    if not s:
        return s
    # 워터마크(footer) 제거 — doubled 형태
    s = re.sub(r"개개인인정정보보유유출출주주의의.{0,300}?다다운운로로드드일일시시[:\s\d.]*", " ", s)
    # 워터마크 제거 — 정상 형태
    s = re.sub(r"개인정보\s*유출\s*주의.{0,150}?다운로드일시[\s:：]*[\d.]+[\s\d:]*", " ", s)
    s = _dedouble(s)
    return re.sub(r"\s+", " ", s).strip(" ,")


# 헤더 키워드 → 필드
_HDR = [
    ("성명", "name"), ("점유부분", "part"), ("정보출처", "source"),
    ("권원", "right"), ("임대차기간", "period"), ("보증금", "deposit"),
    ("차임", "rent"), ("전입신고", "movein"), ("확정일자", "fixed"),
    ("배당", "demand"),
]


def parse_sale_statement(pdf_bytes: bytes) -> dict:
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            tables = [t for p in pdf.pages for t in (p.extract_tables() or [])]   # 전 페이지(다가구 임차인 표는 수쪽에 걸침 — 1쪽만 읽으면 대부분 누락)
            full = _dedouble("\n".join((p.extract_text() or "") for p in pdf.pages))
    except Exception as e:
        return {"available": False, "reason": f"매각물건명세서 분석 실패: {type(e).__name__}"}
    return _parse_core(tables, full)


def _parse_core(tables: list, full: str) -> dict:
    """표(행=셀 목록)·본문 글자 → 임차인·최선순위·배당요구종기·요약. PDF(pdfplumber)·법원 JSON(글자+좌표) 공용."""
    out: dict = {"available": False, "tenants": []}
    # 🔴글자 없는 PDF(그림만 묶은 스캔)는 '읽음'이 아니다(2026-09-22 실측: 서울중앙 2022타경101244 그림 PDF — 글자 0자인데
    #  예전 코드는 available=True·임차인 0명으로 돌려줘, 실제 임차인 3명(박광서 등) 물건이 '임차인 없음'으로 저장될 뻔했다).
    if len(re.sub(r"\s", "", full)) < 50:
        return {"available": False, "reason": "글자 없는 PDF(그림) — 판독 불가"}

    # 최선순위 설정: 표에서 '최선순위' 셀 다음의 날짜+권리 셀
    senior = ""
    for tbl in tables:
        for row in tbl:
            for j, c in enumerate(row):
                if "최선순위" in _norm(c):
                    for nx in row[j + 1:]:
                        v = _cell(nx)
                        if re.search(r"\d{4}\.", v):
                            senior = v
                            break
                    break
            if senior:
                break
        if senior:
            break
    if not senior:                          # 표에 없으면 본문에서(법원 JSON은 표 테두리가 없어 '최선순위' 셀 구조가 없다)
        _ms = re.search(r"최선순위[\s\S]{0,80}?(\d{4}\s*\.\s*\d{1,2}\s*\.\s*\d{1,2}\s*\.?\s*[가-힣()]+)", full)
        if _ms:
            senior = re.sub(r"\s+", " ", _ms.group(1)).strip()
    # 배당요구종기 — 셀 안 줄바꿈('배당요구종\n기')이 있어 글자 사이 공백 허용. 본문에서 못 찾으면 표에서('배당요구종' 셀 다음 날짜 셀).
    deadline = None
    dl_date = None
    md = re.search(r"배당\s*요구\s*종\s*기[\s\S]{0,30}?(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})", full)
    if not md:
        for tbl in tables:
            for row in tbl:
                for j, c in enumerate(row):
                    if "배당요구종" in _norm(c):
                        nxt = " ".join(_cell(x) for x in row[j + 1:] if x)
                        md = re.search(r"(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})", nxt)
                        break
                if md:
                    break
            if md:
                break
    if md:
        deadline = f"{md.group(1)}-{int(md.group(2)):02d}-{int(md.group(3)):02d}"
        from datetime import date as _date
        dl_date = _date(int(md.group(1)), int(md.group(2)), int(md.group(3)))

    # 명세서 요약사항(narrative): 소멸되지 않는 등기부권리 / 지상권 / 비고(주의사항)
    summary = _summary_sections(full)

    if "조사된 임차내역없음" in full or "조사된임차내역없음" in _norm(full):
        out.update({"available": True, "senior_setup": senior,
                    "dividend_deadline": deadline, "tenants": [], "no_tenant": True, "rows": [],
                    **summary})
        return out

    tenants: list[Tenant] = []
    raw_rows: list[dict] = []      # 표의 원문 행(점유부분·정보출처·권원 등) — item_tenants 채우기(statement_fill)용
    colmap: dict[str, int] = {}
    hdr_ncols = 0             # 헤더가 있던 표의 열 수(연속 표 열 수가 다르면 재배치)
    last_name = ""            # 빈 성명 행(권리신고 연속행)은 직전 점유자 이름을 이어받음
    last_parts: set = set()   # 현재 점유자의 점유부분들 / 직전 행 정보출처 — 쪽 넘김으로 성명이 잘린 '다른 사람' 행 구분용
    last_source = ""
    unknown_n = 0
    _FIELD_ORDER = ("name", "part", "source", "right", "period", "deposit", "rent", "movein", "fixed", "demand")
    # ⚠️다가구 명세서는 임차인 표가 수쪽에 걸치고 2쪽부터는 헤더가 없는 '연속 행'이다.
    #   → 헤더가 나오면 colmap 갱신, 그 뒤 표(헤더 없음)는 직전 colmap을 이어 파싱. (예전엔 첫 표 처리 후 break라 1쪽만 읽혀 대부분 누락)
    #   ⚠️연속 표는 열 수가 다르다(실측 A01|2024|124418|1: 1쪽 16열(빈 칸 포함) → 2쪽부터 10열). 헤더 colmap을 그대로 쓰면
    #     보증금 칸에 날짜가 들어가는 등 열이 밀린다 → 열 수가 다르면 헤더 열 순서대로 위치 재배치(10열=논리 열 순서).
    for tbl in tables:
        hidx = -1
        ncols = max((len(r) for r in tbl), default=0)
        for i, row in enumerate(tbl):                 # 이 표에 임차인 헤더가 있으면 colmap 갱신
            joined = _norm("".join(c or "" for c in row))
            if "정보출처" in joined and "배당" in joined and "보증" in joined:
                hidx = i
                cm: dict[str, int] = {}
                for j, c in enumerate(row):
                    cn = _norm(c)
                    for kw, field in _HDR:
                        if kw in cn and field not in cm:
                            cm[field] = j
                if "name" in cm:
                    colmap = cm
                    hdr_ncols = len(row)
                    last_name, last_parts, last_source = "", set(), ""
                break
        if "name" not in colmap:
            continue                                  # 아직 임차인 헤더 못 만남(앞쪽 부동산표시·최선순위 표)
        cmap = colmap
        if hidx < 0 and ncols and ncols != hdr_ncols:
            order = [f for f, _ in sorted(colmap.items(), key=lambda kv: kv[1])]
            if len(order) == ncols:
                cmap = {f: j for j, f in enumerate(order)}
            elif ncols == len(_FIELD_ORDER):
                cmap = {f: j for j, f in enumerate(_FIELD_ORDER)}
        start = hidx + 1 if hidx >= 0 else 0          # 헤더 표는 헤더 다음부터, 연속 표(헤더 없음)는 처음부터
        ended = False
        for row in tbl[start:]:
            line0 = _cell(row[0])
            # 임차인 표 종료 신호 → 이후는 비고/등기 안내문이므로 중단
            if line0.startswith("<") or "※" in line0 or line0.startswith("비고") \
                    or "등기된 부동산" in line0 or "매각에 따라" in line0 \
                    or "최선순위 설정일자" in line0:
                ended = True
                break

            def get(f):
                j = cmap.get(f, -1)
                return _cell(row[j]) if 0 <= j < len(row) else ""

            name = _norm(row[cmap["name"]] if cmap["name"] < len(row) else "")
            part_n = _norm(get("part"))
            source_n = _norm(get("source"))
            # 성명 빈칸이지만 권리신고/보증금 등 실데이터가 있으면 직전 점유자의 연속행
            #   (예: 한국토지주택공사 등기행 다음의 '권리신고·배당요구 2025.1.17' 행)
            #   단, 쪽이 넘어가며 성명 칸이 사라진 '다른 사람'(직전 사람은 권리신고까지 끝났고 점유부분이 다름)은 성명미상으로 분리
            row_has_data = any(get(f) for f in ("deposit", "demand", "right", "source", "period", "movein", "fixed"))
            if not name:
                if not row_has_data:
                    continue
                # 직전 사람이 '권리신고'까지 끝났는데 점유부분이 다른 무명 행 = 쪽 넘김으로 성명이 잘린 다른 사람
                #  (같은 사람의 등기사항(면적)→현황조사(호수)→권리신고 흐름은 점유부분 표기가 달라도 한 사람으로 유지)
                new_person = not last_name or (
                    part_n and last_parts and part_n not in last_parts and "권리신고" in last_source)
                if new_person:
                    unknown_n += 1
                    name = ("성명미상" + get("part").strip()) if part_n else f"성명미상{unknown_n}"
                    last_name, last_parts, last_source = name, set(), ""
                else:
                    name = last_name
            else:
                if name != last_name:
                    last_parts, last_source = set(), ""
                last_name = name
            if part_n:
                last_parts.add(part_n)
            if source_n:
                last_source = source_n

            # 보증금: 칸에 금액이 둘 이상 적힌 경우('155,000,000 157,000,000' 증액 등)가 있어 숫자를 통째로 이으면
            #  '155000000157000000'(bigint 초과·INSERT 실패, 2026-09-18 실측 6건) → 금액 단위(10만↑) 중 최댓값.
            #  한글 단위('8,000만원'·'3억1천5백만원')도 won_amounts가 읽는다.
            _deps = [n for n in won_amounts(get("deposit")) if n >= 100_000]
            dep = str(max(_deps)) if _deps else ""
            _rents = [n for n in won_amounts(get("rent")) if n >= 1_000]   # 차임(월세): '월 50만원'도(빈칸·없음이면 전세)
            rnt = str(max(_rents)) if _rents else ""
            dem = get("demand")
            dem_date = parse_date_kr(dem)
            # 배당요구일이 배당요구종기를 넘기면 '무효'(배당 못 받음) → demanded_distribution=False
            #   단 신청 사실 자체는 demand_date 로 보존(표시·인수판정에서 종기후 구분)
            within = True
            if dem_date and dl_date:
                within = dem_date <= dl_date
            tenants.append(Tenant(
                name=name,
                move_in_date=parse_date_kr(get("movein")),
                fixed_date=parse_date_kr(get("fixed")),
                deposit=int(dep) if dep else 0,
                rent=(int(rnt) if (rnt and int(rnt) < 20_000_000) else 0),   # 월세 2천만↑은 날짜/보증금 컬럼 오인으로 보고 버림

                demanded_distribution=(bool(dem_date) or ("있음" in dem)) and within,
                demand_date=dem_date,
            ))
            raw_rows.append({"name": name, "part": get("part"), "source": get("source"), "right": get("right"),
                             "period": get("period"), "deposit": get("deposit"), "rent": get("rent"),
                             "movein": get("movein"), "fixed": get("fixed"), "demand": dem})
        if ended:
            break                                     # 비고/등기 안내문 도달 → 임차인 표 끝(이후 표는 무관)

    out.update({"available": True, "senior_setup": senior,
                "dividend_deadline": deadline, "tenants": tenants, "rows": raw_rows, **summary})
    return out


def _summary_sections(full: str) -> dict:
    """매각물건명세서 narrative에서 요약 3개 항목 추출.
    - surviving_rights: '… 소멸되지 아니하는 것' 뒤 ~ '지상권' 앞 (인수되는 등기부권리)
    - ground_rights:    '지상권의 개요' 뒤 ~ 비고/※/개인정보 앞 (설정된 것으로 보는 지상권)
    - caution:          '비고' 뒤 ~ '※'/개인정보 앞 (주의사항). 모두 없으면 빈 문자열."""
    def section(start_kw: str, end_kws: list[str]) -> str:
        i = full.find(start_kw)
        if i < 0:
            return ""
        j = i + len(start_kw)
        end = len(full)
        for ek in end_kws:
            k = full.find(ek, j)
            if 0 <= k < end:
                end = k
        seg = re.sub(r"\s+", " ", full[j:end]).strip(" :·-")
        # 의미 없는 안내문/공백 제거
        if seg in ("", "해당사항 없음", "해당사항없음", "없음"):
            return ""
        return seg

    surviving = section("소멸되지 아니하는 것",
                        ["매각에 따라 설정된 것으로 보는 지상권", "지상권의 개요", "비고"])
    ground = section("지상권의 개요", ["비고", "※", "개인정보"])
    caution = section("비고", ["※", "개인정보유출", "개인정보 유출", "등록자:"])
    caution = re.sub(r"^[>\s]+", "", caution)   # '비고>' 의 '>' 잔여 제거
    return {"surviving_rights": surviving, "ground_rights": ground, "caution": caution}


# ───────── 법원 JSON(매각물건명세서_글자) → 표·본문 ─────────
#  2026-09-22: 법원 직접수집분 명세서는 PDF가 아니라 법원 뷰어 글자 레이어 JSON([페이지JSON문자열...], 각 블록
#  {text, rect[글자마다 left/right/bottom/top]})로 온다. 표 테두리가 없어 pdfplumber 표 추출을 못 쓰므로 헤더 낱말 위치로
#  열 경계를 잡고 글자를 칸에 넣어 PDF와 같은 모양의 표를 만든 뒤 _parse_core(같은 규칙)로 넘긴다.
_JSON_COLS = (   # (필드, 헤더 기준 낱말들 — 앞에서부터 찾음)
    ("name", ("점유자", "성명")), ("part", ("부분",)), ("source", ("정보출처", "구분")), ("right", ("점유의", "권원")),
    ("period", ("임대차기간", "점유기간")), ("deposit", ("보증금",)), ("rent", ("차임",)),
    ("movein", ("전입신고",)), ("fixed", ("확정일자",)), ("demand", ("요구여부", "배당요구일자")),
)
_JSON_HDR_LABEL = {"name": "점유자성명", "part": "점유부분", "source": "정보출처구분", "right": "점유의권원",
                   "period": "임대차기간", "deposit": "보증금", "rent": "차임", "movein": "전입신고일자",
                   "fixed": "확정일자", "demand": "배당요구여부"}
_HDR_WORDS = re.compile(r"점유자|성\s*명|부분|정보출처|구\s*분|점유의|권\s*원|임대차기간|점유기간|보\s*증\s*금|차\s*임|"
                        r"전입신고|외국인등록|류지변경|확정일자|배당|요구여부|일자·사업자|사업자등|록\s*신청일자|등록\(체")
_END_RE = re.compile(r"^\s*(<\s*비\s*고|※|비고|등기된 부동산|매각에 따라|최선순위 설정일자)")


def _json_pages(data) -> list:
    import json as _json
    j = _json.loads(data) if isinstance(data, (bytes, str)) else data
    pages = []
    for p in (j if isinstance(j, list) else [j]):
        try:
            pages.append(_json.loads(p) if isinstance(p, str) else p)
        except Exception:
            pages.append([])
    return pages


def _blocks(page) -> list:
    """[(y, x0, x1, text, [(xc, ch)...])] 위에서 아래(y 큰→작은), 왼→오 순."""
    out = []
    for it in page or []:
        t = it.get("text") or ""
        rs = it.get("rect") or []
        if not t or not rs:
            continue
        if len(rs) == len(t):
            chars = [(((r.get("left") or 0) + (r.get("right") or 0)) / 2, ch) for ch, r in zip(t, rs)]
        else:                               # 글자 수와 칸 수가 다르면 블록 폭에 고르게
            x0, x1 = rs[0].get("left") or 0, rs[-1].get("right") or 0
            w = (x1 - x0) / max(len(t), 1)
            chars = [(x0 + w * (i + 0.5), ch) for i, ch in enumerate(t)]
        out.append((rs[0].get("bottom") or 0, rs[0].get("left") or 0, rs[-1].get("right") or 0, t, chars))
    out.sort(key=lambda b: (-b[0], b[1]))
    return out


def _lines_of(blocks, tol: float = 2.5) -> list:
    """같은 높이 블록을 한 줄로: [(y, [(xc, ch)...], 원문)]."""
    lines = []
    for b in blocks:
        if lines and abs(lines[-1][0] - b[0]) <= tol:
            lines[-1][1].extend(b[4]); lines[-1][2].append(b[3])
        else:
            lines.append([b[0], list(b[4]), [b[3]]])
    return [(y, sorted(ch, key=lambda c: c[0]), " ".join(tx)) for y, ch, tx in lines]


def _col_centers(hdr_lines) -> dict:
    """헤더 줄들에서 열 기준 낱말의 x 중심."""
    centers = {}
    for field, words in _JSON_COLS:
        for w in words:
            for _, chars, _ in hdr_lines:
                s = "".join(ch for _, ch in chars)
                idx = [i for i, (_, ch) in enumerate(chars) if not ch.isspace()]
                s2 = "".join(chars[i][1] for i in idx)
                k = s2.find(w)
                if k >= 0:
                    xs = [chars[idx[i]][0] for i in range(k, k + len(w))]
                    centers[field] = sum(xs) / len(xs)
                    break
            if field in centers:
                break
    return centers


def _words(chars) -> list:
    """한 줄 글자 → 단어 [(첫 글자 x, 단어, 끝 글자 x)]. 공백 글자나 넓은 간격(>9)에서 끊는다."""
    out, cur, x0, px, lx = [], "", None, None, None
    for x, ch in chars:
        if ch.isspace() or (px is not None and x - px > 9):
            if cur:
                out.append((x0, cur, lx))
            cur, x0 = "", None
            if ch.isspace():
                px = x
                continue
        if not cur:
            x0 = x
        cur += ch
        px = lx = x
    if cur:
        out.append((x0, cur, lx))
    return out


def _line_cols(chars, bounds, name_ci=None) -> list:
    """한 줄 → 열별 단어 목록. 단어는 첫 글자가 놓인 열로(긴 이름 '주택도시보증공사'가 옆 칸으로 넘쳐도 이름 칸).
    성명 칸에서 '('로 열린 단어는 ')'까지 이어서 성명으로('(임차인 : 김예림)')."""
    per = [[] for _ in bounds]
    open_name = False
    last_end = None
    for x, w, xe in _words(chars):
        ci = next((i for i, (lo, hi) in enumerate(bounds) if lo <= x < hi), None)
        # 괄호로 이어붙이기는 성명 칸 바로 옆(경계+25pt)이면서 직전 이름 단어에 붙어 있을 때(간격 ≤12)만 —
        #  다른 칸 날짜·금액·'전부'까지 빨아들이지 않게(실측 26건)
        if open_name and name_ci is not None and x < bounds[name_ci][1] + 25 and last_end is not None and x - last_end <= 12:
            ci = name_ci
        if ci is None:
            continue
        per[ci].append(w)
        if name_ci is not None and ci == name_ci:
            open_name = (open_name or "(" in w) and ")" not in w
            last_end = xe
    return per


def _cells_of(lines, bounds, name_ci=None) -> list:
    """줄들 → 열별 글자(줄 사이는 공백)."""
    cols = [[] for _ in bounds]
    for _, chars, _ in lines:
        for ci, ws in enumerate(_line_cols(chars, bounds, name_ci)):
            if ws:
                cols[ci].append(" ".join(ws))
    return [" ".join(c for c in col if c) for col in cols]


def _split_names(block, anchors) -> list:
    """이름 줄들(block=[(y, 글자)] 위→아래)을 len(anchors)개 연속 구간으로: Σ|구간 가운데 y − 앵커 y| 최소."""
    n, k = len(block), len(anchors)
    if k <= 1 or n < k:
        return ["".join(t for _, t in block)] * max(k, 1)
    ys = [y for y, _ in block]
    INF = float("inf")
    cost = [[INF] * (n + 1) for _ in range(k + 1)]
    back = [[0] * (n + 1) for _ in range(k + 1)]
    cost[0][0] = 0.0
    for i in range(1, k + 1):
        for e in range(i, n - (k - i) + 1):
            for s in range(i - 1, e):
                if cost[i - 1][s] == INF:
                    continue
                c = cost[i - 1][s] + abs((ys[s] + ys[e - 1]) / 2 - anchors[i - 1])
                if c < cost[i][e]:
                    cost[i][e], back[i][e] = c, s
    out, e = [], n
    for i in range(k, 0, -1):
        s = back[i][e]
        out.append("".join(t for _, t in block[s:e]))
        e = s
    return out[::-1]


def _assign_rows(anchors, centers, has_prev: bool) -> list:
    """행(위→아래, 기준 높이 anchors) → 이름 덩어리(위→아래, 가운데 centers) 번호. 순서를 지키며 각 덩어리에 연속된 행 묶음을
    주되 '덩어리 가운데 ≈ 그 묶음 첫·끝 행 가운데'가 되도록(동적계획). 병합 칸 이름은 걸친 행들의 가운데에 적히므로
    가장 가까운 이름을 고르면 이웃 이름이 붙는다(실측 B01|2023|106707|1: 등기 행에 위 사람 이름).
    쪽 맨 위 이름 없는 행들은 -1(앞 쪽 마지막 이름을 이어받음). 덩어리가 행보다 많으면 일부 덩어리는 건너뛴다(벌점)."""
    R, B = len(anchors), len(centers)
    if not B:
        return [-1 if has_prev else None] * R
    INF = float("inf")
    SKIP, LEAD = 40.0, (8.0 if has_prev else 60.0)   # 이어받기는 행마다 소액(맞는 이름이 있으면 그쪽을 고르게)
    # dp[i][j] = 행 i개·덩어리 j개를 쓴 최소 비용
    dp = [[INF] * (B + 1) for _ in range(R + 1)]
    bk = [[None] * (B + 1) for _ in range(R + 1)]
    dp[0][0] = 0.0
    for i in range(1, R + 1):                           # 맨 앞 이어받기 묶음(덩어리 없음)
        dp[i][0] = LEAD * i
        bk[i][0] = ("lead", 0)
    for j in range(1, B + 1):
        for i in range(0, R + 1):
            if dp[i][j - 1] + SKIP < dp[i][j]:          # 덩어리 j-1 건너뜀
                dp[i][j], bk[i][j] = dp[i][j - 1] + SKIP, ("skip", i)
            for s in range(0, i):
                if dp[s][j - 1] == INF:
                    continue
                c = dp[s][j - 1] + abs(centers[j - 1] - (anchors[s] + anchors[i - 1]) / 2)
                if c < dp[i][j]:
                    dp[i][j], bk[i][j] = c, ("take", s)
    out = [None] * R
    i, j = R, B
    while i > 0 or j > 0:
        kind, s = bk[i][j] if bk[i][j] else ("lead", 0)
        if kind == "lead":
            for r in range(0, i):
                out[r] = -1
            break
        if kind == "skip":
            j -= 1
            continue
        for r in range(s, i):
            out[r] = j - 1
        i, j = s, j - 1
    return out


def json_to_tables(data) -> tuple:
    """법원 JSON → (tables, full). tables = [[헤더행, 임차인행...]] (_parse_core가 PDF 표와 같게 읽음)."""
    pages = _json_pages(data)
    full_lines, rows_all, fields, bounds = [], [], None, None
    in_table = False
    last_nm = ""
    for page in pages:
        lines = _lines_of(_blocks(page))
        full_lines += [t for _, _, t in lines]
        # 헤더: '그 일자'로 끝나는 안내문 다음, 헤더 낱말로만 된 연속 줄
        hi = None
        for i, (_, _, t) in enumerate(lines):
            if "정보출처" in t.replace(" ", "") or ("점유자" in t and "임대차" in t):
                hi = i; break
        data_lines = []
        if hi is not None:
            s = hi
            while s > 0 and _HDR_WORDS.search(lines[s - 1][2]) and len(lines[s - 1][2]) < 40:
                s -= 1
            e = hi
            while e + 1 < len(lines) and _HDR_WORDS.search(lines[e + 1][2]) and \
                    not re.search(r"\d{4}\s*\.\s*\d", lines[e + 1][2]) and len(lines[e + 1][2]) < 45:
                e += 1
            cen = _col_centers(lines[s:e + 1])
            if "name" in cen and len(cen) >= 6:
                order = sorted(cen.items(), key=lambda kv: kv[1])
                fields = [f for f, _ in order]
                xs = [x for _, x in order]
                bounds = [((xs[i - 1] + xs[i]) / 2 if i else -1e9, (xs[i] + xs[i + 1]) / 2 if i + 1 < len(xs) else 1e9)
                          for i in range(len(xs))]
                in_table = True
                data_lines = lines[e + 1:]
        elif in_table:
            data_lines = [ln for ln in lines if not re.search(r"^\s*-?\s*\d+\s*-?\s*$", ln[2])]   # 연속 쪽(헤더 없음): 쪽번호 줄 제외
        if not in_table or not bounds:
            continue
        body = []
        for ln in data_lines:
            if _END_RE.search(ln[2]) or "조사된 임차내역" in ln[2]:
                in_table = False
                break
            body.append(ln)
        if not body:
            continue
        # ① 행 나누기 = '정보출처' 칸이 새로 시작하는 줄(등기사항·현황조사·권리신고 — 행마다 정확히 하나).
        #   성명 칸은 좁아 긴 이름이 여러 줄로 접히고(주택도/시보증/공사), 한 사람이 여러 행에 걸친 병합 칸이라
        #   성명 줄을 행 기준으로 쓰면 한 사람이 여러 명으로 쪼개진다(실측 200건 중 41건) → 이름은 ②에서 따로 모은다.
        ni = fields.index("name")
        nlo, nhi = bounds[ni]
        si = fields.index("source") if "source" in fields else None

        def _col_txt(ln, ci):
            lo_, hi_ = bounds[ci]
            return "".join(ch for x, ch in ln[1] if lo_ <= x < hi_ and not ch.isspace())
        anchors = []
        if si is not None:
            prev = ""
            for ln in body:
                t = _col_txt(ln, si)
                if t and re.match(r"(등기|현황|권리|주민|전입|임대|신고|진술|기타|점유|배당)", t):
                    anchors.append(ln[0])
                prev = t or prev
        if not anchors:                                    # 정보출처 칸이 없으면 성명 줄 기준(옛 방식)
            anchors = [ln[0] for ln in body if _col_txt(ln, ni)]
        if not anchors:
            continue
        cuts = [(anchors[i] + anchors[i + 1]) / 2 for i in range(len(anchors) - 1)]
        groups = [[] for _ in anchors]
        for ln in body:
            groups[sum(1 for c in cuts if ln[0] < c)].append(ln)
        # ② 이름 덩어리 = 성명 칸에서 줄 간격이 좁게(≤14, 실측 칸 안 줄간격 12~13·다른 사람 간격 ≥26) 이어진 줄들 → 한 이름.
        #   각 행은 높이가 가장 가까운 덩어리의 이름(병합 칸 이름은 걸친 행들의 가운데에 적힌다).
        nlines = [(ln[0], "".join(_line_cols(ln[1], bounds, ni)[ni])) for ln in body]
        nlines = [(y, t) for y, t in nlines if t]
        blocks_n = []
        for y, t in nlines:
            if blocks_n and blocks_n[-1][-1][0] - y <= 14:
                blocks_n[-1].append((y, t))
            else:
                blocks_n.append([(y, t)])
        names = [("".join(t for _, t in b), (b[0][0] + b[-1][0]) / 2) for b in blocks_n]
        near = _assign_rows(anchors, [n[1] for n in names], bool(last_nm))
        srcs = [(_col_txt(g[0], si) if (si is not None and g) else "") for g in groups]
        srcs = ["".join(_line_cols(ln[1], bounds, ni)[si]) if si is not None else "" for g in groups for ln in g[:0]] or \
               [("".join("".join(_line_cols(ln[1], bounds, ni)[si]) for ln in g) if si is not None else "") for g in groups]
        for gi, g in enumerate(groups):
            cells = _cells_of(g, bounds, ni)
            if near[gi] is None or near[gi] < 0:           # 쪽 맨 위 이름 없는 행 = 앞 쪽에서 이어진 병합 칸(이름은 앞 쪽에 적힘)
                nm, same_blk = last_nm, []
            else:
                nm = names[near[gi]][0]
                same_blk = [j for j in range(len(groups)) if near[j] == near[gi]]
            if len(same_blk) > 1:
                kinds = [re.match(r"(등기|현황|권리|주민|전입|임대|신고|진술|기타|점유|배당)", srcs[j] or "") for j in same_blk]
                kinds = [m.group(1) if m else "" for m in kinds]
                if len(set(kinds)) < len(kinds):
                    # 같은 출처가 반복 = 서로 다른 사람들(외국인 여러 명 — 사람 사이 줄간격 11이 한 이름 안 12~13과 같아 간격으론
                    #  못 가른다, 실측 A04|2026|3329|1). 칸 내용은 세로 가운데 정렬이므로 이름 줄을 행 수만큼 연속 구간으로 나눠
                    #  '구간 가운데 ≈ 그 행 정보출처 높이'가 되는 분할(동적계획)을 고른다.
                    nm = _split_names(blocks_n[near[gi]], [anchors[j] for j in same_blk])[same_blk.index(gi)]
            row = [cells[fields.index(f)] if f in fields else "" for f, _ in _JSON_COLS]
            row[0] = nm
            rows_all.append(row)
            last_nm = nm
    if not fields:
        return [], "\n".join(full_lines)
    header = [_JSON_HDR_LABEL[f] for f, _ in _JSON_COLS]
    return [[header] + rows_all], "\n".join(full_lines)


def parse_sale_statement_json(data) -> dict:
    """법원 명세서 글자 JSON → parse_sale_statement와 같은 결과 형식."""
    try:
        tables, full = json_to_tables(data)
    except Exception as e:
        return {"available": False, "reason": f"명세서 JSON 분석 실패: {type(e).__name__}"}
    return _parse_core(tables, full)


# ── 지분매각: 전유면적 × 매각지분 비율 → 실제 매각되는 지분면적 (2026-09-23 주인님 지시) ──
#  items의 building_area·area_excl은 원천마다 전체/지분이 뒤섞여 있어(실측: 같은 '지분매각'인데 A02|2024|65819|1은
#  building_area=전체·area_excl=지분, A03|2026|50392|1은 그 반대) 컬럼만으로는 못 가린다. 명세서에는 전유부분 면적과
#  '매각지분 … N분의 M'이 항상 적혀 있어 그것으로 계산한다.
_NUM_FR = r"[0-9,]+(?:\.[0-9]+)?"        # 분모·분자에 소수가 온다(실측 '1345.8분의 281.23', '2438.6분의 4.09')
_FRAC_A = re.compile(f"({_NUM_FR})\\s*분의\\s*({_NUM_FR})")        # 'N분의 M'
_FRAC_B = re.compile(f"({_NUM_FR})\\s*/\\s*({_NUM_FR})")           # 'M/N' (실측 '임주현 지분 1/2 전부')
_SHARE_SEG = re.compile(r"매각지분(.{0,220})")
_EXCL_SEG = re.compile(r"전유부분의?\s*건물의?\s*표시(.{0,220}?)대지권")
_AREA_ANY = re.compile(r"([0-9,]+(?:\.[0-9]+)?)\s*(?:㎡|m²)")
_FLOOR_AREA = re.compile(r"((?:지하\s*)?\d+층|옥탑\d*층?|지층)\s*([0-9,]+(?:\.[0-9]+)?)\s*(?:㎡|m²)")   # 층별 면적(건물 전체 합산용)
_LAND_AREA = re.compile(r"(?:대|전|답|임야|잡종지|도로)\s+([0-9,]+(?:\.[0-9]+)?)\s*(?:㎡|m²)")            # 토지의 표시 '대 626㎡'


def _share_num(s):
    try:
        return float(str(s).replace(",", ""))
    except Exception:
        return None


_SHARE_STOP = re.compile(r"대지권의\s*비율|전유부분|부동산의\s*표시|감정평가|비고란")


def _ratios_in(seg: str) -> list:
    """구간 안의 지분 비율(0~1). 'N분의 M'·'M/N' 둘 다.
    🔴2026-09-23 실측: '매각지분' 뒤 구간에 다음 목적물의 '대지권의비율'이 딸려 들어와 0.01·0.0614 같은 엉뚱한 값이 잡혔다
      (23건). → ①다음 항목 머리말이 나오면 거기서 끊고 ②같은 분모의 지분만 합산한다(분모가 다르면 다른 목적물)."""
    st = _SHARE_STOP.search(seg)
    if st:
        seg = seg[:st.start()]
    pairs = []
    for m in _FRAC_A.finditer(seg):
        den, num = _share_num(m.group(1)), _share_num(m.group(2))
        if den and num and 0 < num <= den:
            pairs.append((den, num))
    for m in _FRAC_B.finditer(seg):
        num, den = _share_num(m.group(1)), _share_num(m.group(2))
        if den and num and 0 < num <= den:
            pairs.append((den, num))
    if not pairs:
        return []
    den0 = pairs[0][0]
    # 🔴같은 지분이 건물·토지에 각각 적히면 중복이다(실측 C01|2025|510699|1 '49분의14'가 두 번 → 28/49로 2배,
    #   C02|2025|32813|1 '2분의 1' 두 번 → 1.0). 같은 (분모,분자)는 한 번만 센다. 서로 다른 분자는 합산(9분의 3 + 9분의 2).
    seen, out = set(), []
    for den, num in pairs:
        if den != den0 or (den, num) in seen:
            continue
        seen.add((den, num))
        out.append(num / den0)
    return out


def share_info(text: str) -> dict:
    """명세서 글자 → {excl_area(전유 전체면적), ratio(매각지분 0~1), share_area(지분면적)}. 못 읽으면 값 None.

    🔴규칙은 실측으로 다듬었다(2026-09-23, 크롤러 값과 어긋난 4건 원문 대조):
      ①'매각지분' 뒤 구간에서만 비율을 찾는다 — 앞의 '대지권의비율 432.4분의 35.46'을 매각지분으로 오인했었다.
      ②같은 사람의 지분이 여러 건이면 합산한다('갑구 1번 9분의 3 전부, 갑구 18번 9분의 2 전부' = 5/9).
      ③전유부분 면적은 '전유부분의 건물의 표시'~'대지권' 사이의 면적을 모두 더한다(1층102호 68.50 + 지층 24.83 + 계단실 6)."""
    t = re.sub(r"\s+", " ", text or "")
    ratio = None
    # ④건물(전유) 지분을 먼저 본다 — 명세서는 토지 지분과 건물 지분을 따로 적는데, 앞에 오는 토지 지분을 쓰면 틀린다
    #  (실측 A03|2025|1111|1: 토지 '1549.4분의 41.219'를 잡아 30.51㎡, 실제 건물 지분 기준은 93.47㎡)
    _ex_pos = t.find("전유부분")
    segs = [m for m in _SHARE_SEG.finditer(t)]
    after = [m for m in segs if _ex_pos >= 0 and m.start() > _ex_pos]
    ratio_after = False
    for seg in after + segs:
        rs = _ratios_in(seg.group(1))
        if rs:
            s = sum(rs)
            ratio = s if 0 < s <= 1 else rs[0]
            ratio_after = seg in after
            break
    excl = None
    eseg = _EXCL_SEG.search(t)
    if eseg:
        areas = [_share_num(x) for x in _AREA_ANY.findall(eseg.group(1))]
        areas = [a for a in areas if a]
        if areas:
            excl = round(sum(areas), 2)
    share = round(excl * ratio, 2) if (excl and ratio) else None
    # 일반건물(단독·다가구·상가 등)은 전유부분이 없다 → 층별 면적 합계(건물 전체)와 토지 면적을 기준값으로 함께 돌려준다.
    #  (2026-09-23 실측: 지분매각 407건 중 300건이 이 경우. 예 M01|2025|8396|1 '1층 60.59㎡ 2층 38.52㎡' = 99.11㎡ = 건물 전체)
    floors = [_share_num(x[1]) for x in _FLOOR_AREA.findall(t)]
    floors = [a for a in floors if a]
    bld_total = round(sum(floors), 2) if floors else None
    lands = [_share_num(x) for x in _LAND_AREA.findall(t)]
    lands = [a for a in lands if a]
    land_total = round(max(lands), 2) if lands else None
    # ratio_after=True면 '전유부분(건물)' 뒤에서 읽은 지분 = 건물 지분이 확실. False면 토지 지분일 수 있어 호출측이 보수적으로 다룬다.
    return {"excl_area": excl, "ratio": ratio, "share_area": share, "ratio_after_excl": ratio_after,
            "bld_total": bld_total, "land_total": land_total}
