"""국토부 공동주택(K-apt) 단지정보 OpenAPI 연동 (data.go.kr).

  1) 단지 목록제공: AptListService4/getSigunguAptList4 (sigunguCode=시군구5자리)
       → 단지명 매칭으로 kaptCode 획득
  2) 기본 정보제공: AptBasisInfoServiceV5/getAphusBassInfoV5 (kaptCode)
       → 세대수·동수·준공일·주차·난방·관리방식·시공사 등

키 미전파 시 403을 반환할 수 있어, 실패는 None으로 graceful 처리(실거래는 별도라 영향 없음).

🔴 2026-09-16 근본수정: data.go.kr이 구버전(AptListService3·AptBasisInfoServiceV4)을 폐기
   ("NO_OPENAPI_SERVICE_ERROR"·400) → 모든 아파트 kapt 조회가 조용히 None이 되어 지번 표제부
   API로 폴백 → 부속동 오독으로 세대수·승강기 결측 + 층 오매칭이 대량 발생했다(실측 진행중
   아파트 21%). list 3→4, basis/detail V4→V5로 교체(필드명 동일). 폐기 재발은 _get_json의
   [KAPT-DEAD] 경보로 조기 포착한다.
"""

from __future__ import annotations

import os
import re
import sys
import time

import httpx

_LIST = "https://apis.data.go.kr/1613000/AptListService4/getSigunguAptList4"
_BASIS = "https://apis.data.go.kr/1613000/AptBasisInfoServiceV5/getAphusBassInfoV5"
_DETAIL = "https://apis.data.go.kr/1613000/AptBasisInfoServiceV5/getAphusDtlInfoV5"
_UA = {"User-Agent": "Mozilla/5.0"}
_KAPT_HEALTH = {"dead_warned": 0.0, "quota_until": 0.0, "down_until": 0.0}   # 폐기응답 경보 스로틀·일일 한도 차단 해제·접속불가 차단 해제 시각
# 접속불가 차단기는 클라우드(CLOUD_READER)에서만 — 로컬은 9/17 세대수 수정대로 재시도를 끝까지 한다(일시 실패로 표제부 폴백 방지)
_CONN_BREAKER = os.environ.get("CLOUD_READER", "0") in ("1", "true", "True")
import threading as _threading
_LIST_LOCK = _threading.Lock()         # 시군구 단지목록 락 사전 보호


def _next_midnight_ts() -> float:
    """다음 자정+5분(로컬 시각) — data.go.kr 일일 트래픽은 자정에 리셋."""
    import datetime as _dt
    t = _dt.datetime.now().replace(hour=0, minute=5, second=0, microsecond=0) + _dt.timedelta(days=1)
    return t.timestamp()


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _num(s: str) -> str:
    """'2700.0' → '2700' (정수형 소수점 제거). 아니면 원본."""
    s = (s or "").strip()
    m = re.fullmatch(r"(\d+)\.0+", s)
    return m.group(1) if m else s


def _lcs(a: str, b: str) -> int:
    """최장 공통 부분수열 길이(순서 유지). 단지명 퍼지매칭용."""
    m, n = len(a), len(b)
    if not m or not n:
        return 0
    dp = [0] * (n + 1)
    for i in range(1, m + 1):
        prev = 0
        for j in range(1, n + 1):
            tmp = dp[j]
            dp[j] = prev + 1 if a[i - 1] == b[j - 1] else (dp[j] if dp[j] >= dp[j - 1] else dp[j - 1])
            prev = tmp
    return dp[n]


# ── 단지명 정합 보조(2026-09-17 세대수 감사 후 추가) ──
#  실측 오매칭: 연수2차대우→연수2차'우성', 양주'자이'→양주'현대', 노르웨이'아침'→노르웨이'숲', 율정마을13단지→7단지,
#  햇빛마을아파트→22단지(실제 24단지). 공통점 = 접두(법정동·마을명)는 달라도 되지만 **브랜드 꼬리·단지번호·차수는 같아야** 한다.
def _core(nm: str) -> str:
    """단지명에서 일반어(아파트·단지·마을·N단지·N차·숫자·기호)를 걷어낸 핵심 문자열."""
    s = _norm(nm)
    s = re.sub(r"\d+(?:[-,]\d+)*\s*(?:단지|차|블록|BL|bl)", "", s)
    s = re.sub(r"아파트|오피스텔|주상복합|공동주택|단지|마을|제|[0-9]+|[()\-·.,'\"]", "", s)
    return s


def _danji_set(nm: str) -> set:
    """'N단지'·'1,2단지'·'18-1단지' → {'N'} / {'1','2'} / {'18-1'}. 없으면 빈 집합."""
    m = re.search(r"(\d+(?:[-,]\d+)*)\s*단지", _norm(nm))
    return set(m.group(1).split(",")) if m else set()


def _ident_set(nm: str) -> set:
    """단지 식별 번호: 'N단지' 우선, 없으면 'N차'·끝 숫자('현대2'·'래미안3차') — 이름은 같고 번호만 다른 이웃단지 구분용."""
    s = _danji_set(nm)
    if s:
        return s
    n = _strip_generic(_norm(nm)) if "_strip_generic" in globals() else _norm(nm)
    m = re.search(r"(\d+)\s*차$", n) or re.search(r"(\d+)$", n)
    return {m.group(1)} if m else set()


def _cha_no(nm: str):
    m = re.search(r"(\d+)차", _norm(nm))
    return m.group(1) if m else None


_BRANDS = ("현대", "대우", "우성", "삼성", "자이", "푸르지오", "편한세상", "힐스테이트", "래미안", "롯데캐슬", "롯데", "아이파크",
           "위브", "더샵", "호반", "중흥", "한신", "한양", "동아", "쌍용", "벽산", "경남", "동부", "두산", "금호", "코오롱", "대림",
           "한화", "포스코", "태영", "계룡", "신동아", "삼환", "극동", "한일", "진흥", "동원", "부영", "동신", "성원", "대원", "신성",
           "한라", "풍림", "우방", "청구", "보람", "화성", "영남", "유림", "반도", "서희", "이수", "삼정", "한솔", "동양", "월드",
           "삼익", "현진", "세경", "주공", "휴먼시아", "엘에이치", "LH", "SK", "에스케이", "GS", "KCC", "대방", "협성", "일신",
           "신안", "동문", "대주", "경동", "선경", "럭키", "라이프", "동성", "삼부", "건영", "한진", "미성", "신성", "성지", "동일")


def _brands(core: str) -> set:
    return {b for b in _BRANDS if b in core}


def _strip_generic(s: str) -> str:
    """점수 계산용: 단지명 접미 일반어(아파트·오피스텔·주상복합)를 뗀다 — '햇빛마을아파트' vs '행신햇빛마을24단지'."""
    return re.sub(r"아파트|오피스텔|주상복합|공동주택", "", s or "")


def _core_compatible(tcore: str, ncore: str) -> bool:
    """핵심 문자열의 '꼬리'가 같아야 같은 단지로 본다(접두 지역명 차이는 허용, 브랜드 꼬리 차이는 거부).
    접두가 서로 다른 글자로 '치환'됐는데 그중 하나가 시공사 브랜드면(현우파크맨션↔현대파크맨션) 다른 단지로 본다."""
    if not tcore or not ncore:
        return True                     # 핵심어가 없으면(숫자·일반어뿐) 기존 점수식에 맡김
    bt, bn = _brands(tcore), _brands(ncore)
    if bt and bn and bt.isdisjoint(bn):    # 양쪽 다 브랜드가 있는데 겹치지 않음(연수대우≠연수우성, 양주자이≠양주현대)
        return False
    if tcore in ncore or ncore in tcore:
        return True
    k = 0
    while k < min(len(tcore), len(ncore)) and tcore[-1 - k] == ncore[-1 - k]:
        k += 1
    shorter = min(len(tcore), len(ncore))
    if not (k >= shorter or (k >= 3 and k >= 0.6 * shorter)):
        return False
    tpre, npre = tcore[:len(tcore) - k], ncore[:len(ncore) - k]
    if tpre and npre and (_brands(tpre) or _brands(npre)):   # 접두 치환이고 한쪽이 브랜드 → 다른 단지
        return False
    return True


class KaptSource:
    def __init__(self, key: str | None = None):
        self.key = key or os.environ.get("ONBID_SERVICE_KEY", "")
        self._code_cache: dict[str, str | None] = {}   # (lawd|단지명) → kaptCode
        self._basis_cache: dict[str, dict | None] = {}  # kaptCode → 기본정보
        self._list_cache: dict[str, list] = {}          # lawd_cd → 시군구 단지목록(재사용)
        self._list_locks: dict[str, object] = {}        # lawd_cd → 목록 순회 락(시군구별)
        # ★목록 디스크 캐시(7일): 재시작마다 ~200시군구×최대 10페이지를 다시 받아 일일 한도를 태우던 것 방지(2026-09-17 실측: 재시작 7회 → 한도 소진).
        self._list_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "kapt_lists.json")
        self._list_saved_at = 0.0
        try:
            import json as _json
            if os.path.exists(self._list_file) and time.time() - os.path.getmtime(self._list_file) < 7 * 86400:
                with open(self._list_file, encoding="utf-8") as f:
                    d = _json.load(f)
                if isinstance(d, dict):
                    self._list_cache.update({k: v for k, v in d.items() if isinstance(v, list) and v})
        except Exception:
            pass

    def _save_lists(self) -> None:
        """목록 캐시를 디스크에 저장(60초 디바운스, 베스트에포트)."""
        if time.time() - self._list_saved_at < 60:
            return
        self._list_saved_at = time.time()
        try:
            import json as _json
            tmp = self._list_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                _json.dump(self._list_cache, f, ensure_ascii=False)
            os.replace(tmp, self._list_file)
        except Exception:
            pass

    @staticmethod
    def quota_blocked() -> bool:
        """오늘 K-apt 일일 한도 소진 상태인가(자정 리셋)."""
        return time.time() < _KAPT_HEALTH.get("quota_until", 0.0)

    def prefetch_lists(self, lawd_cds) -> int:
        """시군구 단지목록을 미리(순차·스로틀 하에) 채운다 — 병렬 재계산이 새 지역에 들어갈 때 멈추지 않게. 새로 받은 수 반환."""
        n = 0
        for lc in dict.fromkeys([c for c in (lawd_cds or []) if c]):
            if lc not in self._list_cache:
                if self._sigungu_list(lc):
                    n += 1
        return n

    def _get_json(self, url: str, params: dict) -> dict | None:
        """★재시도(2026-09-17): 서버 부하 구간에서 타임아웃/5xx가 나면 재시도 없이 None → K-apt 실패로 오인돼
        표제부 폴백(오독 위험)으로 흘렀다. 3회 재시도 + 실패 로그(10분 스로틀)."""
        last = ""
        from .api_throttle import throttle
        if self.quota_blocked():                          # 오늘 일일 한도 소진 → 즉시 None(재시도·대기 없음)
            return None
        if time.time() < _KAPT_HEALTH.get("down_until", 0.0):   # 연결 자체가 안 되는 중 → 즉시 None(아래 참고)
            return None
        _conn_fail = 0
        for _try in range(4):
            try:
                throttle()                                # 전역 초당 제한(429 방지)
                r = httpx.get(url, params={**params, "serviceKey": self.key, "_type": "json"},
                              headers=_UA, timeout=25)
                if r.status_code == 429 and "EXCEEDS_ERROR" in (r.text or "") and "PER_SECOND" not in (r.text or ""):
                    # ★일일 요청 한도 초과(LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR) — 초당 제한과 다르다.
                    #   실측(2026-09-17): 서버를 7번 재시작하며 시군구 목록(최대 10페이지)을 매번 다시 받아 목록 API 한도를 태웠다.
                    #   오늘은 끝 → 자정 이후로 차단 표시, 호출측은 quota 표식을 남겨 스윕이 내일 다시 계산한다.
                    _KAPT_HEALTH["quota_until"] = _next_midnight_ts()
                    if time.time() - _KAPT_HEALTH.get("quota_warned", 0.0) > 600:
                        _KAPT_HEALTH["quota_warned"] = time.time()
                        print(f"[kapt] 공동주택 API 일일 한도 소진({url.rsplit('/', 1)[-1]}) → 자정까지 K-apt 미사용, "
                              f"해당 물건은 quota 표식으로 내일 재계산", flush=True)
                    return None
                if r.status_code == 200:
                    j = r.json()
                    try:
                        code = str(j["response"]["header"]["resultCode"])
                    except Exception:
                        code = "00"
                    if code in ("00", "0", "03"):        # 03 = NODATA(정상·빈 결과)
                        return j
                    last = f"resultCode={code}"
                    if code == "22":                      # 초당 요청 제한 → 잠깐 쉬고 재시도
                        time.sleep(1.0 + _try)
                        continue
                    break                                 # 그 밖의 오류코드는 재시도 무의미
                last = f"HTTP {r.status_code}"
                if "NO_OPENAPI_SERVICE_ERROR" in (r.text or ""):
                    # ★재발방지: data.go.kr이 K-apt API 버전을 폐기하면 여기서 조용히 None→지번폴백→오매칭.
                    if time.time() - _KAPT_HEALTH["dead_warned"] > 600:
                        _KAPT_HEALTH["dead_warned"] = time.time()
                        sys.stderr.write(f"[KAPT-DEAD] 공동주택 API 폐기응답 감지 — 엔드포인트 버전 확인 필요: {url}\n")
                        sys.stderr.flush()
                    return None
                if r.status_code == 429 or 500 <= r.status_code < 600:   # 429=초당 제한(실측) → 지수 백오프 재시도
                    time.sleep(2.0 * (2 ** _try))
                    continue
                break
            except Exception as e:                        # 타임아웃·연결오류 → 재시도
                last = f"{type(e).__name__}"
                if _CONN_BREAKER and isinstance(e, (httpx.ConnectTimeout, httpx.ConnectError)):
                    # 🔴2026-09-18 CloudType에서 K-apt 접속이 안 됨(ConnectTimeout) → 호출마다 25초×4회 재시도, 시군구 목록은
                    #  여러 페이지라 상세 요청 하나가 몇 분씩 멈췄다. 연결이 두 번 연달아 안 되면 10분간 K-apt를 건너뛴다
                    #  (단지정보는 건축물대장 폴백, 로컬 주기작업이 K-apt로 채운 캐시를 클라우드가 읽는다).
                    _conn_fail += 1
                    if _conn_fail >= 2:
                        _KAPT_HEALTH["down_until"] = time.time() + 600
                        break
                time.sleep(1.0 + _try)
        if time.time() - _KAPT_HEALTH.get("fail_warned", 0.0) > 600:
            _KAPT_HEALTH["fail_warned"] = time.time()
            print(f"[kapt] API 호출 실패({last}) {url.rsplit('/', 1)[-1]} — 10분간 첫 1건만 기록", flush=True)
        return None

    @staticmethod
    def _items(j: dict | None) -> list[dict]:
        try:
            body = j["response"]["body"]
            items = body.get("items")
            if not items:
                return []
            it = items.get("item") if isinstance(items, dict) else items
            if isinstance(it, dict):
                return [it]
            return it or []
        except Exception:
            return []

    def _fetch_sigungu(self, lawd_cd: str) -> list[dict]:
        """단일 sigunguCode 단지목록(페이지 순회)."""
        out: list[dict] = []
        page = 1
        while page <= 10:  # 시군구당 최대 10페이지(1000단지)
            j = self._get_json(_LIST, {"sigunguCode": lawd_cd, "pageNo": str(page),
                                       "numOfRows": "100"})
            items = self._items(j)
            if not items:
                break
            out.extend(items)
            try:
                total = int(j["response"]["body"]["totalCount"])
            except Exception:
                total = 0
            if page * 100 >= total or len(items) < 100:
                break
            page += 1
        return out

    def _sigungu_list(self, lawd_cd: str) -> list[dict]:
        """시군구 전체 단지목록(kaptCode·kaptName). 시군구당 1회만 호출하도록 캐시.
        ⚠️화성(41590) 등 일부 '구 없는 시'는 kapt가 시코드(끝자리0)엔 0건이고 단지를 형제 sub코드
        (XXXX1~9: 41591/93/95/97…)에 쪼개 담음 → 시코드가 비면 형제 sub코드를 합산해 누락 방지."""
        if lawd_cd in self._list_cache:
            return self._list_cache[lawd_cd]
        # ★시군구별 락: 같은 시군구는 한 스레드만 페이지 순회(429 폭주 방지), 다른 시군구는 병렬. (전역 락은 새 지역 진입 시
        #   16스레드 전부가 한 목록 완성을 기다려 수 분 멈추던 실측 문제)
        with _LIST_LOCK:
            lk = self._list_locks.setdefault(lawd_cd, _threading.Lock())
        with lk:
            if lawd_cd in self._list_cache:
                return self._list_cache[lawd_cd]
            out = self._fetch_sigungu(lawd_cd)
            if lawd_cd.endswith("0"):   # 시코드 → 형제 sub코드도 합산(화성 41590: 시코드 429단지 + 41591~ 에 석봉마을 등 분산 수록)
                seen = {it.get("kaptCode") for it in out}
                for d in range(1, 10):
                    for it in self._fetch_sigungu(lawd_cd[:4] + str(d)):
                        if it.get("kaptCode") not in seen:
                            seen.add(it.get("kaptCode"))
                            out.append(it)
            if out:                             # 성공만 캐시(403 전파지연 시 재시도 허용)
                self._list_cache[lawd_cd] = out
                self._save_lists()
            return out

    def find_kapt_code(self, lawd_cd: str, apt_name: str, danji=None, bjd: str | None = None) -> str | None:
        """시군구 단지목록에서 단지명으로 kaptCode 매칭(가장 유사한 단지).
        danji: 주소 동번호에서 유도한 단지번호 힌트(예 2405동→24). 주면 그 번호를 가진 단지만 후보.
        bjd: 물건 법정동코드 10자리. 같은 이름 단지가 여럿이면(광주 북구 '현대아파트' 2곳) 법정동이 같은 후보로 좁힌다."""
        if not (self.key and lawd_cd and apt_name):
            return None
        danji = str(danji) if danji not in (None, "", 0) else None
        bjd = str(bjd) if bjd else None
        ck = f"{lawd_cd}|{_norm(apt_name)}|{danji or ''}|{bjd or ''}"
        if ck in self._code_cache:
            return self._code_cache[ck]
        target = _norm(apt_name)
        tcore = _core(target)
        tdanji = _danji_set(target) or ({danji} if danji else set())
        tcha = _cha_no(target)
        target_s = _strip_generic(target) or target      # 점수용(접미 일반어 제거)
        cands: list[tuple[float, str, str]] = []
        for it in self._sigungu_list(lawd_cd):
            nm_raw = _norm(it.get("kaptName"))
            if not nm_raw:
                continue
            nm = _strip_generic(nm_raw) or nm_raw
            ncore = _core(nm_raw)
            # ★정합 가드(2026-09-17): 단지번호·차수가 둘 다 있는데 다르면 다른 단지(율정마을13단지≠7단지).
            #   힌트(danji)를 쓸 땐 후보에 그 번호가 '있어야' 한다(구월힐스테이트 11→3단지 오매칭 차단).
            ndanji = _danji_set(nm)
            if tdanji and ndanji and not (tdanji & ndanji):
                continue
            if danji and not ndanji:
                continue
            ncha = _cha_no(nm)
            if tcha and ncha and tcha != ncha:
                continue
            # 브랜드 꼬리가 다르면 다른 단지(연수2차'대우'≠'우성', 노르웨이'아침'≠'숲', 양주'자이'≠'현대').
            if not _core_compatible(tcore, ncore):
                continue
            # 점수(접미 일반어 제거본으로): ①완전일치 ②부분포함 ③부분수열 커버리지(시공사/위치 prefix·중간삽입어 차이 대응)
            if nm == target_s:
                score = 1000
            elif nm in target_s or target_s in nm:
                score = 500 - abs(len(nm) - len(target_s))
            else:
                # 예: 주소'금호서한이다음' ↔ kapt'금호신도시서한이다음'(중간 '신도시' 삽입) → substring 실패
                #   단지명 대부분이 순서대로 들어있으면(커버리지≥0.7) 매칭. 잘못된 '복현3차 서한이다음'보다 우선.
                #   핵심어 기준 커버리지도 인정(탑실'마을'대주피오레2단지 ↔ '공세'대주피오레2단지).
                l = _lcs(nm, target_s)
                cov = l / max(len(target_s), 1)
                if tcore and ncore:
                    cov = max(cov, _lcs(ncore, tcore) / max(len(tcore), 1))
                if cov >= 0.7 and l >= 4:
                    score = 300 + cov * 100 - abs(len(nm) - len(target_s)) * 0.5
                else:
                    continue
            if tdanji and ndanji and (tdanji & ndanji):
                score += 150            # 단지번호가 명시적으로 일치하는 후보 우선('리슈빌2단지' → '리슈빌1,2단지' > '리슈빌')
            cands.append((score, it.get("kaptCode"), nm_raw, str(it.get("bjdCode") or "")))
        # ★법정동 일치 후보가 있으면 그것만(같은 이름 다른 동 배제). 단지가 여러 동에 걸쳐 K-apt 법정동이 다를 수 있어 '있을 때만' 좁힌다.
        same_dong = False
        if bjd:
            same = [c for c in cands if c[3] == bjd]
            if same:
                cands = same
                same_dong = True
        cands.sort(key=lambda c: -c[0])
        best, best_code, best_nm = (cands[0][:3] if cands else (-1, None, ""))
        # ★완전 동점(같은 이름 단지 2곳 이상)이면 찍지 않는다 → None.
        if len(cands) >= 2 and cands[1][0] == best and cands[1][1] != best_code:
            best_code = None
        # ★애매성 가드: 1·2위가 비슷한 점수인데 둘 다 단지번호가 있고 서로 다르면(햇빛마을 20~24단지) 찍지 않는다 → None.
        #   동번호 힌트가 있으면 위에서 번호로 걸러지므로 여기 안 걸린다.
        if len(cands) >= 2 and best_code and _strip_generic(best_nm) != target_s:
            s2, c2, nm2 = cands[1][:3]
            d1, d2 = _ident_set(best_nm), _ident_set(nm2)
            if c2 != best_code and best - s2 < 100 and d1 and d2 and d1 != d2:
                best_code = None        # '현대1/현대2/현대3'처럼 번호만 다른 이웃단지 사이에서 찍지 않는다
        # ── 확신 가드(주인님 지시: 억지 매칭 금지, 확신 없으면 정보없음) ──
        #  물건명이 kapt 단지명의 대부분을 덮어야 확정한다. 짧은 브랜드명('편한세상')이
        #  같은 시군구의 다른 특정단지('율하이편한세상' 506세대)에 substring으로 잘못 붙는 것을 차단.
        #  물건명이 단지명의 <60%만 덮으면(=구별 접두어를 통째로 빠뜨림) 매칭 취소 → None(세대수 정보없음).
        #  완전일치(nm==target)는 항상 통과. kaptName은 소규모(<의무관리)를 아예 담지 않으므로,
        #  DB에 없는 소단지에 딴 단지를 억지로 붙이는 대신 정보없음을 노출하는 게 옳다.
        #  단, 단지번호 힌트로 유일하게 걸러진 후보('햇빛마을'+24단지 → 행신햇빛마을24단지)는 통과.
        if best_code and _strip_generic(best_nm) != target_s and not danji:
            _bn = _strip_generic(best_nm) or best_nm
            # 같은 법정동으로 좁혀진 후보이고 핵심어를 통째로 품고 있으면('치평동 라인동산' → '상무1차라인동산') 접두어가 길어도 확정.
            _contained = same_dong and tcore and (tcore in _core(best_nm))
            if not _contained and _lcs(_bn, target_s) / max(len(_bn), 1) < 0.6:
                best_code = None
        if best_code:                       # 성공만 캐시
            self._code_cache[ck] = best_code
        return best_code

    def brief(self, lawd_cd: str, apt_name: str, danji=None, bjd: str | None = None) -> dict | None:
        """목록뷰용: 준공년도·세대수·승강기. basis_info(캐시) 재사용.
        이름만으로 애매하면(N단지 여러 개) danji(동번호//100) 힌트로 한 번 더 시도."""
        code = self.find_kapt_code(lawd_cd, apt_name, bjd=bjd)
        if not code and danji:
            code = self.find_kapt_code(lawd_cd, apt_name, danji=danji, bjd=bjd)
        if not code:
            return None
        info = self.basis_info(code)
        if not info:
            return None
        return {"build_year": (info.get("approved") or "")[:4],
                "households": info.get("households"),
                "elevator": info.get("elevator"),
                "kapt_code": code}

    def basis_info(self, kapt_code: str) -> dict | None:
        """kaptCode 기본정보 → 표시용 dict. 실패 None."""
        if not (self.key and kapt_code):
            return None
        if kapt_code in self._basis_cache:
            return self._basis_cache[kapt_code]
        j = self._get_json(_BASIS, {"kaptCode": kapt_code})
        try:
            b = j["response"]["body"]["item"]
        except Exception:
            return None                     # 성공만 캐시(전파지연 재시도 허용)

        def g(src, *keys):
            # ★V5는 숫자를 float(0.0/916.0)로 준다. 옛 코드는 문자열 "0"만 걸러 0.0이 '0'으로 통과
            #   → 미등록 단지가 "0세대"로 저장·정렬됐다(실측 45건). 정규화 후 '0'이면 없음으로.
            for k in keys:
                v = src.get(k)
                if v in (None, "", "None"):
                    continue
                s = _num(str(v).strip())
                if s in ("", "0"):
                    continue
                return s
            return ""

        used = g(b, "kaptUsedate")            # YYYYMMDD
        used_fmt = (f"{used[:4]}.{used[4:6]}.{used[6:8]}" if len(used) == 8 else used)

        # 상세정보(주차·교통·시설) 병합 — 같은 서비스의 getAphusDtlInfoV5
        dj = self._get_json(_DETAIL, {"kaptCode": kapt_code})
        try:
            dd = dj["response"]["body"]["item"]
        except Exception:
            dd = {}
        p_ground = g(dd, "kaptdPcnt")
        p_under = g(dd, "kaptdPcntu")
        try:
            p_total = (str(int(float(p_ground or 0)) + int(float(p_under or 0)))
                       if (p_ground or p_under) else "")
        except Exception:
            p_total = ""

        out = {
            "name": g(b, "kaptName"),
            "road_addr": g(b, "doroJuso"),         # 도로명 주소(블록 주소 물건 지도 좌표용 — 2026-09-18)
            "addr": g(b, "kaptAddr"),              # 법정동 지번 주소
            "households": g(b, "kaptdaCnt"),       # 세대수
            "dongs": g(b, "kaptDongCnt"),          # 동수
            "approved": used_fmt,                  # 사용승인일(준공)
            "floors_high": g(b, "kaptTopFloor"),   # 최고층
            "heat": g(b, "codeHeatNm"),            # 난방방식
            "manage": g(b, "codeMgrNm"),           # 관리방식
            "builder": g(b, "kaptBcompany"),       # 시공사
            "developer": g(b, "kaptAcompany"),     # 시행사
            "hall_type": g(b, "codeHallNm"),       # 복도유형
            "sale_type": g(b, "codeSaleNm"),       # 분양형태
            "area_total": g(b, "kaptTarea"),       # 연면적
            "parking": p_total,                    # 총 주차대수(지상+지하)
            "elevator": g(dd, "kaptdEcnt"),        # 승강기 대수(>0이면 있음)
            "cctv": g(dd, "kaptdCccnt"),           # CCTV 대수
            "bus_time": g(dd, "kaptdWtimebus"),    # 버스 소요
            "subway_time": g(dd, "kaptdWtimesub"), # 지하철 소요
            "subway_line": g(dd, "subwayLine"),    # 지하철 노선
            "ev_charger": g(dd, "groundElChargerCnt"),  # 전기차 충전
        }
        self._basis_cache[kapt_code] = out
        return out

    def complex_detail(self, lawd_cd: str, apt_name: str, danji=None, bjd: str | None = None) -> dict | None:
        """시군구코드+단지명 → 단지 상세정보(기본정보). 실패/미전파 시 None."""
        code = self.find_kapt_code(lawd_cd, apt_name, bjd=bjd)
        if not code and danji:
            code = self.find_kapt_code(lawd_cd, apt_name, danji=danji, bjd=bjd)
        if not code:
            return None
        info = self.basis_info(code)
        if info:
            info["kapt_code"] = code
        return info
