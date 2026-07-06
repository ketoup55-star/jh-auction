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
