"""국토부 공동주택(K-apt) 단지정보 OpenAPI 연동 (data.go.kr).

  1) 단지 목록제공: AptListService3/getSigunguAptList3 (sigunguCode=시군구5자리)
       → 단지명 매칭으로 kaptCode 획득
  2) 기본 정보제공: AptBasisInfoServiceV4/getAphusBassInfoV4 (kaptCode)
       → 세대수·동수·준공일·주차·난방·관리방식·시공사 등

키 미전파 시 403을 반환할 수 있어, 실패는 None으로 graceful 처리(실거래는 별도라 영향 없음).
"""

from __future__ import annotations

import os
import re

import httpx

_LIST = "https://apis.data.go.kr/1613000/AptListService3/getSigunguAptList3"
_BASIS = "https://apis.data.go.kr/1613000/AptBasisInfoServiceV4/getAphusBassInfoV4"
_DETAIL = "https://apis.data.go.kr/1613000/AptBasisInfoServiceV4/getAphusDtlInfoV4"
_UA = {"User-Agent": "Mozilla/5.0"}


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


class KaptSource:
    def __init__(self, key: str | None = None):
        self.key = key or os.environ.get("ONBID_SERVICE_KEY", "")
        self._code_cache: dict[str, str | None] = {}   # (lawd|단지명) → kaptCode
        self._basis_cache: dict[str, dict | None] = {}  # kaptCode → 기본정보
        self._list_cache: dict[str, list] = {}          # lawd_cd → 시군구 단지목록(재사용)

    def _get_json(self, url: str, params: dict) -> dict | None:
        try:
            r = httpx.get(url, params={**params, "serviceKey": self.key, "_type": "json"},
                          headers=_UA, timeout=25)
            if r.status_code != 200:
                return None
            return r.json()
        except Exception:
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
        out = self._fetch_sigungu(lawd_cd)
        if not out and lawd_cd.endswith("0"):   # 시코드인데 0건 → 형제 sub코드 합산(화성식 분할 대응)
            for d in range(1, 10):
                out.extend(self._fetch_sigungu(lawd_cd[:4] + str(d)))
        if out:                             # 성공만 캐시(403 전파지연 시 재시도 허용)
            self._list_cache[lawd_cd] = out
        return out

    def find_kapt_code(self, lawd_cd: str, apt_name: str) -> str | None:
        """시군구 단지목록에서 단지명으로 kaptCode 매칭(가장 유사한 단지)."""
        if not (self.key and lawd_cd and apt_name):
            return None
        ck = f"{lawd_cd}|{_norm(apt_name)}"
        if ck in self._code_cache:
            return self._code_cache[ck]
        target = _norm(apt_name)
        best, best_code, best_nm = -1, None, ""
        for it in self._sigungu_list(lawd_cd):
            nm = _norm(it.get("kaptName"))
            if not nm:
                continue
            # 점수: ①완전일치 ②부분포함 ③부분수열 커버리지(시공사/위치 prefix·중간삽입어 차이 대응)
            if nm == target:
                score = 1000
            elif nm in target or target in nm:
                score = 500 - abs(len(nm) - len(target))
            else:
                # 예: 주소'금호서한이다음' ↔ kapt'금호신도시서한이다음'(중간 '신도시' 삽입) → substring 실패
                #   단지명 대부분이 순서대로 들어있으면(커버리지≥0.7) 매칭. 잘못된 '복현3차 서한이다음'보다 우선.
                l = _lcs(nm, target)
                cov = l / max(len(target), 1)
                if cov >= 0.7 and l >= 4:
                    score = 300 + cov * 100 - abs(len(nm) - len(target)) * 0.5
                else:
                    continue
            if score > best:
                best, best_code, best_nm = score, it.get("kaptCode"), nm
        # ── 확신 가드(주인님 지시: 억지 매칭 금지, 확신 없으면 정보없음) ──
        #  물건명이 kapt 단지명의 대부분을 덮어야 확정한다. 짧은 브랜드명('편한세상')이
        #  같은 시군구의 다른 특정단지('율하이편한세상' 506세대)에 substring으로 잘못 붙는 것을 차단.
        #  물건명이 단지명의 <60%만 덮으면(=구별 접두어를 통째로 빠뜨림) 매칭 취소 → None(세대수 정보없음).
        #  완전일치(nm==target)는 항상 통과. kaptName은 소규모(<의무관리)를 아예 담지 않으므로,
        #  DB에 없는 소단지에 딴 단지를 억지로 붙이는 대신 정보없음을 노출하는 게 옳다.
        if best_code and best_nm != target:
            if _lcs(best_nm, target) / max(len(best_nm), 1) < 0.6:
                best_code = None
        if best_code:                       # 성공만 캐시
            self._code_cache[ck] = best_code
        return best_code

    def brief(self, lawd_cd: str, apt_name: str) -> dict | None:
        """목록뷰용: 준공년도·세대수·승강기. basis_info(캐시) 재사용."""
        code = self.find_kapt_code(lawd_cd, apt_name)
        if not code:
            return None
        info = self.basis_info(code)
        if not info:
            return None
        return {"build_year": (info.get("approved") or "")[:4],
                "households": info.get("households"),
                "elevator": info.get("elevator")}

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
            for k in keys:
                v = src.get(k)
                if v not in (None, "", "0", "None"):
                    return _num(str(v).strip())
            return ""

        used = g(b, "kaptUsedate")            # YYYYMMDD
        used_fmt = (f"{used[:4]}.{used[4:6]}.{used[6:8]}" if len(used) == 8 else used)

        # 상세정보(주차·교통·시설) 병합 — 같은 서비스의 getAphusDtlInfoV4
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

    def complex_detail(self, lawd_cd: str, apt_name: str) -> dict | None:
        """시군구코드+단지명 → 단지 상세정보(기본정보). 실패/미전파 시 None."""
        code = self.find_kapt_code(lawd_cd, apt_name)
        if not code:
            return None
        info = self.basis_info(code)
        if info:
            info["kapt_code"] = code
        return info
