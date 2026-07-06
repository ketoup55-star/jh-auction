"""차량외 매물의 차체유형 분류 — 'SUV' 또는 '승용자동차'(2분류).

크롤 데이터의 usage_name/모델명에는 차종(세단/SUV) 표기가 없어, 모델명을 SUV 목록과
대조해 분류한다. SUV 키워드에 걸리면 'SUV', 아니면 모두 '승용자동차'(세단·경차·픽업·승합 포함).
목록에 없는 SUV는 '승용자동차'로 분류되므로, 누락분은 _SUV_KEYWORDS에 한 줄씩 추가하면 된다.
"""

from __future__ import annotations

import re

# SUV 모델 키워드(국산+수입). 모델명에 포함되면 SUV로 분류.
_SUV_KEYWORDS = [
    # 현대
    "산타페", "싼타페", "santafe", "santa fe", "투싼", "tucson", "ix35", "ix55", "코나", "kona",
    "팰리세이드", "펠리세이드", "palisade", "베뉴", "venue", "캐스퍼", "casper", "베라크루즈",
    "맥스크루즈", "테라칸", "넥쏘", "nexo",
    # 기아
    "쏘렌토", "소렌토", "sorento", "스포티지", "sportage", "셀토스", "seltos", "니로", "niro",
    "모하비", "mohave", "쏘울", "soul", "카렌스", "ev9", "ev5",
    # 제네시스
    "gv60", "gv70", "gv80",
    # 르노삼성/르노코리아
    "qm3", "qm5", "qm6", "xm3", "아르카나", "arkana", "캡처", "captur", "콜레오스", "koleos",
    "kgm", "그랑콜레오스",
    # KGM/쌍용
    "티볼리", "tivoli", "코란도", "korando", "렉스턴", "rexton", "토레스", "torres", "액티언",
    "actyon", "카이런", "무쏘", "musso", "로디우스",
    # 쉐보레/GM
    "트레일블레이저", "trailblazer", "트랙스", "trax", "이쿼녹스", "equinox", "캡티바", "captiva",
    "올란도", "orlando", "윈스톰", "트래버스", "traverse", "타호", "tahoe", "콜로라도", "colorado",
    "블레이저", "blazer",
    # 수입 - 독일
    "x1", "x2", "x3", "x4", "x5", "x6", "x7", "xm",
    "gla", "glb", "glc", "gle", "gls", "g클래스", "g-class", "g350", "g400", "g500", "g63",
    "q2", "q3", "q4", "q5", "q7", "q8", "e-tron", "etron",
    "티구안", "tiguan", "투아렉", "touareg", "t-roc", "troc", "아테온",  # 아테온은 세단인데 제외 필요 → 아래서 보정
    "카이엔", "cayenne", "마칸", "macan",
    # 수입 - 영국/스웨덴
    "레인지로버", "range rover", "rangerover", "이보크", "evoque", "벨라", "velar",
    "디스커버리", "discovery", "디펜더", "defender", "프리랜더",
    "xc40", "xc60", "xc90", "c40",
    # 수입 - 미국/일본
    "cr-v", "crv", "hr-v", "hrv", "파일럿", "pilot", "패스포트", "passport",
    "익스플로러", "explorer", "이스케이프", "escape", "브롱코", "bronco", "엣지", "edge", "쿠가",
    "랭글러", "wrangler", "체로키", "cherokee", "레니게이드", "renegade", "컴패스", "compass",
    "rav4", "라브4", "하이랜더", "highlander", "벤자", "4runner", "랜드크루저", "land cruiser",
    "포레스터", "forester", "아웃백", "outback", "xv", "cx-3", "cx-5", "cx-9", "cx5", "cx9",
    "로그", "rogue", "쥬크", "엑스트레일", "x-trail", "패스파인더", "pathfinder", "무라노", "murano",
    "qx50", "qx60", "qx70", "qx80",
    "xt4", "xt5", "xt6", "에스컬레이드", "escalade", "스토닉",
    "에비에이터", "aviator", "코세어", "corsair", "네비게이터", "navigator", "노틸러스", "mkc", "mkx",
    "2008", "3008", "5008", "에어크로스", "티볼리에어",
    "stelvio", "스텔비오", "에코스포트", "ecosport", "그랜드체로키",
    "테슬라 모델 x", "model x", "model y", "모델y", "모델x",
    # 수입 고급 SUV
    "르반떼", "르반떼", "levante", "그레칼레", "grecale",
    "f-pace", "fpace", "e-pace", "epace", "i-pace", "ipace",
    "우루스", "urus", "벤테이가", "bentayga", "dbx", "푸로산게", "purosangue", "쿨리넌", "cullinan",
]

# SUV 키워드에 우연히 걸리지만 실제론 SUV 아닌 모델(보정 — 우선 '승용자동차'로 강제)
_NOT_SUV = ["아테온", "arteon", "q50", "q70"]


def is_suv(model_name) -> bool:
    t = re.sub(r"\s+", "", str(model_name or "")).lower()
    if not t:
        return False
    if t == "suv":                  # usage_name이 통째로 'SUV'(차종 미상이나 SUV로 등록)
        return True
    for n in _NOT_SUV:
        if n.replace(" ", "").lower() in t:
            return False
    for kw in _SUV_KEYWORDS:
        if kw.replace(" ", "").lower() in t:
            return True
    return False


def body_type(model_name) -> str:
    """모델명 → 'SUV' 또는 '승용자동차'."""
    return "SUV" if is_suv(model_name) else "승용자동차"
