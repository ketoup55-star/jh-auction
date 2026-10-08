"""문건접수송달(HTML) → 법원 문건접수내역·송달내역 요약 파서.

원본은 스피드옥션 수집 HTML로, '문건접수내역'(접수일|접수내역)과 '송달내역'(송달일|송달내역)
두 표를 가진다. 날짜로 시작하는 행만 추출해 시간순 이벤트 목록으로 만든다.
개인정보는 원본에서 이미 'OO' 마스킹되어 있다.
"""

from __future__ import annotations

import re

_TR = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_TD = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")
_DATE = re.compile(r"^20\d\d[.\-]\d{1,2}[.\-]\d{1,2}")


def _txt(s: str) -> str:
    return re.sub(r"\s+", " ", _TAG.sub("", s)).strip()


from re import sub as _re_sub


def parse_court_docs_json(data) -> dict:
    """대법원 §5 문건접수/송달 JSON → parse_court_docs(HTML) 과 **같은 형식**.

    🔴2026-10-07: 09-08 대법원 수집 전환 뒤 옛 수집원의 문건접수송달 HTML 이 끊겨,
      새로 들어온 물건은 상세의 '주의사항 / 법원문건접수 요약'이 비었다.
      크롤러가 적재한 원본 JSON 으로 같은 칸을 채운다(형식만 맞추고 로직은 공유).
    """
    if not isinstance(data, dict):
        return {"available": False, "docs": []}

    def _d(v) -> str:
        """YYYYMMDD · YYYY.MM.DD · YYYY-MM-DD 를 모두 YYYY-MM-DD 로. (실측: §5 는 '20251128' 꼴)"""
        v = str(v or "").strip()
        digits = _re_sub(r"[^0-9]", "", v)
        if len(digits) == 8:
            return f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"
        return v.replace(".", "-").strip("-")

    def _join(row: dict, skip: set) -> str:
        return " ".join(str(v).strip() for k, v in row.items()
                        if k not in skip and v not in (None, "", []))

    docs: list[dict] = []
    for row in (data.get("dlt_ofdocDtsLst") or []):        # 문건접수내역
        if not isinstance(row, dict):
            continue
        d = _d(row.get("ofdocRcptYmd"))
        c = str(row.get("rcptDts") or "").strip() or _join(row, {"ofdocRcptYmd"})
        if d or c:
            docs.append({"date": d, "gubun": "접수", "content": c})
    for row in (data.get("dlt_dlvrDtsLst") or []):         # 송달내역
        if not isinstance(row, dict):
            continue
        d = _d(row.get("dlvrbkRegYmd") or row.get("lastDlvrblRchYmd"))
        c = str(row.get("dlvrDts") or "").strip() or _join(row, {"dlvrbkRegYmd", "lastDlvrblRchYmd"})
        if d or c:
            docs.append({"date": d, "gubun": "송달", "content": c})
    return {"available": bool(docs), "docs": docs}


def parse_court_docs(html: str) -> dict:
    """{available, docs:[{date, gubun, content}]} — gubun: '접수' | '송달'."""
    if not html:
        return {"available": False, "docs": []}

    # 두 표 영역을 헤더로 구분: '문건접수내역' / '송달내역'
    docs: list[dict] = []
    # 섹션 경계 인덱스
    rec_i = html.find("문건접수내역")
    snd_i = html.find("송달내역")

    def rows_in(seg: str, gubun: str):
        for tr in _TR.findall(seg):
            cells = [c for c in (_txt(x) for x in _TD.findall(tr)) if c]
            if len(cells) >= 2 and _DATE.match(cells[0]):
                docs.append({"date": cells[0].replace(".", "-").strip("-"),
                             "gubun": gubun,
                             "content": " ".join(cells[1:])})

    if rec_i >= 0 or snd_i >= 0:
        # 접수 영역: rec_i ~ snd_i, 송달 영역: snd_i ~ 끝
        if rec_i >= 0:
            end = snd_i if (snd_i > rec_i) else len(html)
            rows_in(html[rec_i:end], "접수")
        if snd_i >= 0:
            rows_in(html[snd_i:], "송달")
    else:
        rows_in(html, "문건")

    return {"available": bool(docs), "docs": docs}
