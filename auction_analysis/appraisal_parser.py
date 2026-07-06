"""
감정평가서(PDF 텍스트) → 물건현황(요항표)·감정평가현황 파서.

추출:
  · 요항표 (1)~(10): 위치/교통/구조/이용상태/설비/형상/도로/토지이용계획/공부차이/기타
  · 토지·건물 배분내역(금액), 감정평가액
  · 용도지역(토지이용계획), 기준시점

전제: 텍스트 추출 가능한 감정평가서 PDF. 일부 물건은 미보유/스캔본일 수 있어 방어적으로 파싱.
"""

from __future__ import annotations

import re

# 요항표 항목 번호 → 표시 라벨(레퍼런스 순서)
_YOHANG_LABELS = {
    1: "위치 및 주위환경", 2: "교통상황", 3: "건물의 구조", 4: "이용상태",
    5: "설비내역", 6: "토지의 형상 및 이용상태", 7: "인접 도로상태",
    8: "토지이용계획 및 제한상태", 9: "공부와의 차이", 10: "기타참고사항",
}

# "(N) 제목\n본문..."  본문은 다음 (N)/페이지푸터까지
_SEC = re.compile(
    r"\((\d{1,2})\)\s*([^\n]+?)\n([\s\S]*?)(?=\(\d{1,2}\)\s|\n오상호|\n[-–]\s*\d+\s*[-–]|감정평가요항표|\Z)"
)


def _clean(s: str) -> str:
    return re.sub(r"[ \t]+", " ", s).strip()


def parse_appraisal(text: str) -> dict:
    """감정평가서 텍스트 → {available, items[(번호,라벨,내용)], 배분, 용도지역, 기준시점}."""
    out: dict = {"available": False}
    if "감정평가요항표" not in text and "평가개요" not in text:
        return out

    # 요항표 영역(첫 '감정평가요항표' 이후)에서 항목별 본문 추출, 번호별 최장본 채택
    region = text[text.find("감정평가요항표"):] if "감정평가요항표" in text else text
    best: dict[int, str] = {}
    for m in _SEC.finditer(region):
        try:
            n = int(m.group(1))
        except ValueError:
            continue
        if n not in _YOHANG_LABELS:
            continue
        body = _clean(m.group(3))
        # 요약 라인(본문 없음)은 건너뜀('없음.' 같은 짧은 실제 본문은 유지)
        if len(body) < 2:
            continue
        if len(body) > len(best.get(n, "")):
            best[n] = body

    items = [{"no": n, "label": _YOHANG_LABELS[n], "text": best[n]}
             for n in sorted(best)]

    # 토지/건물 배분내역
    land = _won(re.search(r"토\s*지\s*:\s*([\d,]+)", text))
    bldg = _won(re.search(r"건\s*물\s*:\s*([\d,]+)", text))
    total = _won(re.search(r"합\s*계\s*\\?\s*([\d,]+)", text))

    # 용도지역: (8) 토지이용계획 본문에서
    zone = ""
    z = best.get(8, "")
    mz = re.search(r"(제?\d*[가-힣]*?(?:주거|상업|공업|녹지|관리|농림|자연환경)지역)", z)
    if mz:
        zone = mz.group(1)

    # 기준시점
    base_dt = ""
    mb = re.search(r"기준시점[^\d]*(\d{4})\s*[.\-]\s*(\d{1,2})\s*[.\-]\s*(\d{1,2})", text)
    if not mb:
        mb = re.search(r"가격조사\s*완료일인\s*(\d{4})\s*[.\-]\s*(\d{1,2})\s*[.\-]\s*(\d{1,2})", text)
    if mb:
        base_dt = f"{mb.group(1)}-{int(mb.group(2)):02d}-{int(mb.group(3)):02d}"

    if not items and not (land or bldg):
        return out
    out.update({
        "available": True,
        "items": items,
        "land_value": land, "bldg_value": bldg, "total_value": total,
        "zone": zone, "base_date": base_dt,
    })
    return out


def _won(m) -> int:
    return int(m.group(1).replace(",", "")) if m else 0
