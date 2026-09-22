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
    out: dict = {"available": False, "tenants": []}
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            tables = [t for p in pdf.pages for t in (p.extract_tables() or [])]   # 전 페이지(다가구 임차인 표는 수쪽에 걸침 — 1쪽만 읽으면 대부분 누락)
            full = _dedouble("\n".join((p.extract_text() or "") for p in pdf.pages))
    except Exception as e:
        return {"available": False, "reason": f"매각물건명세서 분석 실패: {type(e).__name__}"}
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
