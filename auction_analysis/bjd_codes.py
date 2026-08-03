"""법정동코드 변환: 주소 → (시군구코드, 법정동코드, 본번, 부번).

행정표준 법정동코드 전체자료(동/읍/면/리 20,267개)를 bjd_codes.tsv 로 내장.
건축물대장 API(getBrTitleInfo)는 sigunguCd(5)+bjdongCd(5)+bun(4)+ji(4)로 조회하므로
주소에서 법정동명까지 잘라 코드표에서 찾고, 지번(본번-부번)을 추출한다.
"""

from __future__ import annotations

import os
import re

_PATH = os.path.join(os.path.dirname(__file__), "bjd_codes.tsv")
_MAP: dict[str, str] | None = None

# 법정동명(시도 시군구 …동/읍/면/리[N가]) + 지번(본번[-부번])
_RE = re.compile(r"^(.*?(?:동|읍|면|리|가))\s+(\d+)(?:-(\d+))?")


def _load() -> dict[str, str]:
    global _MAP
    if _MAP is None:
        _MAP = {}
        try:
            with open(_PATH, encoding="utf-8") as f:
                for line in f:
                    p = line.rstrip("\n").split("\t")
                    if len(p) == 2:
                        _MAP[p[0]] = p[1]
        except Exception:
            _MAP = {}
    return _MAP


# 2026-07-01 행정구역 개편 등으로 '시도명 자체'가 바뀐 케이스 — 기존 DB에 옛 명칭으로
#  저장된 주소가 코드 변환에 실패하지 않도록 옛→신 치환(폴백). 코드표는 신 명칭만 갖고 있다.
#  ⚠️광주광역시는 전남과 통합되어 사라졌고, 전남/전북/강원은 명칭 변경됨(실측 확인).
_SIDO_RENAME = {
    "전라남도": "전남광주통합특별시",
    "광주광역시": "전남광주통합특별시",
    "전라북도": "전북특별자치도",
    "강원도": "강원특별자치도",
}

# 시도명 축약/생략 → 정식 명칭(주소가 '서울 서초구…', '경기 …'처럼 축약된 케이스).
#  _resolve_code ⓪에서 정식명으로 치환 후 재귀 → _SIDO_RENAME(개편)·①②폴백까지 그대로 탄다.
#  '서울'→'서울특별시'는 다시 축약 대상이 아니므로 무한재귀 없음.
#  '광주'→'광주광역시'→(개편)→'전남광주통합특별시'처럼 2단계 치환도 재귀로 처리된다.
_SIDO_ABBR = {
    "서울": "서울특별시", "부산": "부산광역시", "대구": "대구광역시",
    "인천": "인천광역시", "광주": "광주광역시", "대전": "대전광역시",
    "울산": "울산광역시", "세종": "세종특별자치시", "경기": "경기도",
    "강원": "강원도", "충북": "충청북도", "충남": "충청남도",
    "전북": "전라북도", "전남": "전라남도", "경북": "경상북도",
    "경남": "경상남도", "제주": "제주특별자치도",
}


def _resolve_code(name: str) -> str | None:
    """법정동명 문자열 → 10자리 법정동코드. 개명·승격·시도개편 폴백 포함."""
    mp = _load()
    code = mp.get(name)
    if code:
        return code
    toks0 = name.split()
    # ⓪-a 시도 축약 → 정식(서울→서울특별시, 경기→경기도). 재귀로 개편치환·①②폴백까지 탄다.
    if toks0 and toks0[0] in _SIDO_ABBR:
        renamed = " ".join([_SIDO_ABBR[toks0[0]]] + toks0[1:])
        code = _resolve_code(renamed)
        if code:
            return code
    # ⓪-b 시도명 개편 치환 후 재시도(전라남도→전남광주통합특별시 등). 치환 뒤 ①②폴백도 그대로 탄다.
    if toks0 and toks0[0] in _SIDO_RENAME:
        renamed = " ".join([_SIDO_RENAME[toks0[0]]] + toks0[1:])
        code = _resolve_code(renamed)
        if code:
            return code
    # ① 읍↔면 승격/강등 치환 (모현읍↔모현면 등)
    for a, b in (("읍", "면"), ("면", "읍")):
        if name.endswith(a):
            code = mp.get(name[:-1] + b)
            if code:
                return code
    # ② 시도 + 마지막 동/읍/면/리명으로 유일 매칭 (구 개명: 남구↔미추홀구 등)
    toks = name.split()
    if len(toks) >= 2:
        sido, leaf = toks[0], toks[-1]
        mids = toks[1:-1]
        cands = [(k, v) for k, v in mp.items()
                 if k.startswith(sido + " ") and k.endswith(" " + leaf)]
        if len(cands) == 1:
            return cands[0][1]
        if len(cands) > 1 and mids:
            scored = sorted(cands, key=lambda kv: -sum(1 for t in mids if t in kv[0]))
            top = sum(1 for t in mids if t in scored[0][0])
            second = sum(1 for t in mids if t in scored[1][0])
            if top > second:           # 최고점이 유일할 때만 채택(애매하면 None)
                return scored[0][1]
    return None


def _resolve_road_paren(address: str) -> str | None:
    """도로명주소 '시도 시군구 …로/길 N, … (법정동, 건물명)'에서 괄호 법정동을 뽑아
    '시도 시군구 법정동'으로 10자리 코드 조회(지번은 미상 처리). 유사거래·시세는 시군구(앞5)만 필요.
    시도 축약·개편은 _resolve_code가 처리하므로 head는 원문 그대로 넘긴다."""
    m = re.search(r"\(\s*([가-힣]+(?:동|리|가)\d*)\b", address)
    if not m:
        return None
    dong = m.group(1)
    toks = address.split()
    if len(toks) < 2:
        return None
    head = [toks[0]]  # 시도
    for t in toks[1:5]:
        if t.endswith(("시", "군", "구")):
            head.append(t)
            if t.endswith("구"):        # 시군구(+일반구)까지 확보하면 종료
                break
        else:
            break                        # 도로명 토큰 시작 → 헤더 종료
    if len(head) < 2:
        return None
    return _resolve_code(" ".join(head) + " " + dong)


def resolve_bjd(address: str):
    """주소 → (sigunguCd5, bjdongCd5, bun4, ji4). 실패 시 None.
    개명(남구→미추홀구)·승격(면→읍)·시도개편·시도축약 주소도 폴백으로 변환.
    지번 파싱 실패 시(도로명주소) 괄호의 법정동으로 시군구까지는 복구한다."""
    if not address:
        return None
    m = _RE.match(address.strip())
    if m:
        name = re.sub(r"\s+", " ", m.group(1)).strip()
        bun, ji = m.group(2), (m.group(3) or "0")
        code = _resolve_code(name)
        if code and len(code) == 10:
            return code[:5], code[5:], bun.zfill(4), ji.zfill(4)
    # 도로명주소 폴백: 지번 패턴이 없어 _RE/_resolve_code 실패 → 괄호 법정동으로 시군구 복구.
    code = _resolve_road_paren(address)
    if code and len(code) == 10:
        return code[:5], code[5:], "0000", "0000"
    return None
