"""
온비드(OnBid) 부동산 물건목록 OpenAPI 연동 (data.go.kr).

서비스: 한국자산관리공사_온비드 부동산 물건목록 조회서비스(OnbidRlstListSrvc2)
  Endpoint: https://apis.data.go.kr/B010003/OnbidRlstListSrvc2/getRlstCltrList2
  필수 파라미터: serviceKey, pageNo, numOfRows, resultType, prptDivCd(재산유형),
                pvctTrgtYn(수의계약가능여부 Y/N)
  키 = .env ONBID_SERVICE_KEY (data.go.kr 계정 공통키).
응답(XML) 주요필드(검증 2026-06): cltrMngNo(물건관리번호), onbidCltrNm(물건명/소재지),
  cltrUsg*CtgrNm(용도), dspsMthodNm(처분방식), cptnMthodNm(입찰방식),
  apslEvlAmt(감정가), lowstBidPrcIndctCont(최저입찰가),
  cltrBidBgngDt/EndDt(입찰기간 YYYYMMDDHHMM), lctnSd/Sgg/EmdNm(소재지), orgNm/rqstOrgNm.
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from typing import Optional

import httpx

_OP = "https://apis.data.go.kr/B010003/OnbidRlstListSrvc2/getRlstCltrList2"
_OP_DTL = "https://apis.data.go.kr/B010003/OnbidRlstDtlSrvc2/getRlstDtlInf2"  # 물건상세(면적·전체주소·PNU·사진)
_UA = {"User-Agent": "Mozilla/5.0"}

# 재산종류명 → prptDivCd
PROP_CD = {"압류재산": "0007", "국유재산": "0010", "수탁재산": "0008",
           "유입자산": "0006", "공유재산": "0002", "기타일반재산": "0005"}


def _t(el, tag: str) -> str:
    c = el.find(tag)
    return (c.text or "").strip() if c is not None and c.text else ""


def _num(s: str) -> int:
    s = re.sub(r"[^0-9]", "", s or "")
    return int(s) if s else 0


def _f(s: str):
    s = re.sub(r"[^0-9.]", "", s or "")
    try:
        return float(s) if s else None
    except ValueError:
        return None


def _dt(s: str) -> str:
    """YYYYMMDDHHMM → 'YYYY-MM-DD HH:MM'."""
    s = (s or "").strip()
    if len(s) >= 12:
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]} {s[8:10]}:{s[10:12]}"
    if len(s) >= 8:
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return s


class OnbidSource:
    def __init__(self, service_key: Optional[str] = None):
        self.key = service_key or os.environ.get("ONBID_SERVICE_KEY", "")

    def list_items(self, *, page: int = 1, rows: int = 20, prop: str = "압류재산",
                   dpsl_mtd: Optional[str] = None, usg_lcls: Optional[str] = None,
                   goods: Optional[str] = None, **_) -> dict:
        if not self.key:
            return {"error": "온비드 서비스키 미설정(ONBID_SERVICE_KEY)", "items": [], "total": 0}
        params = {
            "serviceKey": self.key, "pageNo": str(page), "numOfRows": str(rows),
            "resultType": "xml", "pvctTrgtYn": "N",
            "prptDivCd": PROP_CD.get(prop, prop or "0007"),
        }
        if dpsl_mtd:
            params["dspsMthodCd"] = dpsl_mtd
        if usg_lcls:
            params["cltrUsgLclsCtgrId"] = usg_lcls
        try:
            r = httpx.get(_OP, params=params, headers=_UA, timeout=30)
            root = ET.fromstring(r.text)
        except Exception as e:
            return {"error": f"온비드 호출 실패: {type(e).__name__}", "items": [], "total": 0}
        code = root.findtext(".//resultCode")
        if code not in ("00", "000"):
            return {"error": f"온비드 API 오류(코드 {code}): {root.findtext('.//resultMsg')}",
                    "items": [], "total": 0}
        total = _num(root.findtext(".//totalCount") or "0")
        items = [self._summary(it) for it in root.findall(".//items/item")]
        # 물건명(소재지) 키워드 클라이언트 필터(API에 명칭검색 없음)
        if goods:
            items = [x for x in items if goods in (x.get("name") or "")]
        return {"items": items, "total": total, "page": page}

    def _summary(self, it) -> dict:
        appraisal = _num(_t(it, "apslEvlAmt"))
        minbid = _num(_t(it, "lowstBidPrcIndctCont"))
        rate = round(100 * minbid / appraisal) if appraisal else None
        usg = " ".join(x for x in [_t(it, "cltrUsgMclsCtgrNm"), _t(it, "cltrUsgSclsCtgrNm")] if x)
        addr = " ".join(x for x in [_t(it, "lctnSdnm"), _t(it, "lctnSggnm"), _t(it, "lctnEmdNm")] if x)
        name = _t(it, "onbidCltrNm").strip()
        cltrno = _t(it, "onbidCltrno"); pbctno = _t(it, "pbctNo")
        pbanc = _t(it, "onbidPbancNo"); cdtn = _t(it, "pbctCdtnNo")
        prpt_cd = _t(it, "cltrPrptDivCd") or _t(it, "prptDivCd")
        scrn = _t(it, "cltrScrnGrpCd") or "0001"
        # 온비드 물건상세 URL(500 방지: cltrno·pbctNo·plnmNo·pbctCdtnNo 모두 있어야 열림)
        onbid_url = ""
        if cltrno and pbctno and pbanc and cdtn:
            onbid_url = ("https://www.onbid.co.kr/op/cltrpbancinf/cltrdtl/CltrDtlController/mvmnCltrDtl.do"
                         f"?cltrScrnGrpCd={scrn}&cltrPrptDivCd={prpt_cd}&onbidCltrno={cltrno}"
                         f"&onbidPbancNo={pbanc}&pbctNo={pbctno}&pbctCdtnNo={cdtn}")
        mfl = re.search(r"제?\s*(\d+)\s*층", name)      # 물건명에서 층 파싱(제20층)
        mho = re.search(r"제?\s*([0-9]+)\s*호", name)    # 호 파싱(제2002호)
        return {
            "id": "|".join([cltrno, pbctno, _t(it, "pbctNsq")]),
            "manage_no": _t(it, "cltrMngNo"),
            "name": name,
            "usage": usg or _t(it, "cltrUsgLclsCtgrNm"),
            "address": addr or name,
            "prop_type": _t(it, "prptDivNm"),                 # 재산유형(압류재산 등)
            "disposal": _t(it, "dspsMthodNm"),                # 처분방식
            "bid_method": _t(it, "cptnMthodNm") or _t(it, "bidDivNm"),
            "appraisal_price": appraisal,
            "min_price": minbid,
            "bid_ratio": rate,
            "bid_begin": _dt(_t(it, "cltrBidBgngDt")),
            "bid_close": _dt(_t(it, "cltrBidEndDt")),
            "org": _t(it, "rqstOrgNm") or _t(it, "orgNm"),
            "thumb": _t(it, "thnlImgUrlAdr"),                 # 온비드 썸네일 이미지 URL
            "pnu": _t(it, "ltnoPnu"),                         # 지번 PNU → 좌표·건축물대장
            "pbanc_no": pbanc,                                # 공고번호(상세 URL·상세 API)
            "pbct_cdtn_no": cdtn,                             # 공매조건번호(상세 URL·상세 API)
            "onbid_url": onbid_url,                           # 물건별 온비드 상세 링크
            "floor": int(mfl.group(1)) if mfl else None,      # 층(물건명 파싱)
            "ho": mho.group(1) if mho else None,              # 호(물건명 파싱)
        }

    def _fetch_detail_root(self, cltr_mng_no: str, pbct_cdtn_no: Optional[str] = None):
        """물건상세 XML root + resultCode 확인. (detail·bid_schedule 공용)"""
        if not self.key or not cltr_mng_no:
            return None, "키/물건번호 없음"
        p = {"serviceKey": self.key, "cltrMngNo": cltr_mng_no,
             "resultType": "xml", "numOfRows": "50", "pageNo": "1"}
        if pbct_cdtn_no:
            p["pbctCdtnNo"] = pbct_cdtn_no
        try:
            r = httpx.get(_OP_DTL, params=p, headers=_UA, timeout=30)
            root = ET.fromstring(r.text)
        except Exception as e:
            return None, f"온비드 상세 호출 실패: {type(e).__name__}"
        if (root.findtext(".//resultCode") or "") not in ("00", "000"):
            return None, (root.findtext(".//resultMsg") or "온비드 상세 오류")
        return root, None

    @staticmethod
    def _urls_of(el, tag: str) -> list:
        """<tag><listItem><urlAdr>URL</urlAdr>...  → [URL,...] (온비드 첨부/사진은 listItem/urlAdr 중첩구조).
        평면 텍스트(콤마·파이프 구분)도 폴백 파싱."""
        out = []
        lst = el.find(tag)
        if lst is not None:
            for li in lst.findall("listItem"):
                u = (li.findtext("urlAdr") or "").strip()
                if u.startswith("http"):
                    out.append(u)
            if not out and (lst.text or "").strip():   # 평면 텍스트 폴백
                out = [u.strip() for u in re.split(r"[,\|]", lst.text) if u.strip().startswith("http")]
        return list(dict.fromkeys(out))                # 중복 제거(순서 유지)

    def detail(self, cltr_mng_no: str, pbct_cdtn_no: Optional[str] = None) -> dict:
        """물건상세(면적·전체주소·PNU·사진URL·임대차 + 공고문·첨부파일·공고정보). cltrMngNo 필수, pbctCdtnNo로 회차 특정.
        경매 재사용 기능(유사거래·시세·예상낙찰가 등)에 필요한 {면적·주소·감정가}를 제공한다."""
        root, err = self._fetch_detail_root(cltr_mng_no, pbct_cdtn_no)
        if root is None:
            return {"error": err} if err and "없음" not in err else {}
        items = root.findall(".//item")
        it = None
        if pbct_cdtn_no:
            it = next((x for x in items if (x.findtext("pbctCdtnNo") or "") == str(pbct_cdtn_no)), None)
        it = it or (items[0] if items else None)
        if it is None:
            return {}

        def T(tag):
            c = it.findtext(tag)
            return (c or "").strip() if c else ""
        photos = self._urls_of(it, "potoUrlList")

        # ── 첨부파일: 감정평가서(apslEvlClgList)·정정공고(crtnLstClgList)·일괄입찰(batcBidCltrClgList)·도면(lmapUrlAdrList) ──
        files = []
        for u in self._urls_of(it, "apslEvlClgList"):
            files.append({"name": "감정평가서", "url": u})
        for u in self._urls_of(it, "crtnLstClgList"):
            files.append({"name": "정정공고", "url": u})
        for u in self._urls_of(it, "batcBidCltrClgList"):
            files.append({"name": "일괄입찰물건", "url": u})
        for u in self._urls_of(it, "lmapUrlAdrList"):
            files.append({"name": "위치도/도면", "url": u})

        # ── 공고문(공고 내용 텍스트): 물건상세 API의 세부내용 필드들을 라벨과 함께 묶음 ──
        notice_parts = []
        for tag, label in [("cltrEtcCont", "물건세부"), ("locVntyPscdCont", "위치·부근현황"),
                           ("utlzPscdCont", "이용현황"), ("icdlCdtnCont", "부대조건"),
                           ("evcRsbyTrgtCont", "명도책임"), ("dsplVldCont", "처분효력"),
                           ("purrQlfcCont", "매수인자격"), ("pytnMtrsCont", "유의사항")]:
            v = T(tag)
            if v:
                notice_parts.append({"label": label, "text": v})

        # ── 공고 정보(공고보기): 물건상세 API에서 얻을 수 있는 항목만(공고 전용 API는 이 키로 미제공). ──
        pbanc = T("onbidPbancNo")
        notice_info = {
            "pbanc_no": pbanc,                                   # 공고번호
            "prop_type": T("prptDivNm"),                        # 재산유형
            "org": T("rqstOrgNm") or T("orgNm"),                # 공고기관/집행기관
            "bid_type": " ".join(x for x in [T("cptnMthodNm"), T("bidMthodNm")] if x),  # 입찰방식(일반경쟁 최고가방식)
            "bid_div": T("bidDivNm"),                           # 입찰구분(일반경쟁)
            "amt_open": T("totalamtUnpcDivNm"),                 # 총액/단가 구분
            "round": T("pbctNsq"),                              # 공고회차(회차)
            "notice_ymd": _dt(T("frstPbancYmd")) or _dt(T("mdfcnDt")),  # 공고일(없으면 수정일)
        }
        return {
            "bld_area": _f(T("bldSqms")),          # 건물/전용 면적 ㎡
            "land_area": _f(T("landSqms")),        # 대지 면적 ㎡
            "addr_road": T("cltrRadr"),            # 도로명 전체주소(번지·호)
            "addr_jibun": T("zadrNm"),             # 지번 전체주소
            "pnu": T("ltnoPnu"),
            "appraisal_price": _num(T("apslEvlAmt")),
            "photos": photos,
            "rent_method": T("rentMthodNm"),
            "rent_period": T("rentPerdCont"),
            "usage_scls": T("cltrUsgSclsCtgrNm"),  # 세부용도(다세대주택/아파트 등)
            "notice": notice_parts,                # 공고문(세부내용 텍스트 블록)
            "files": files,                        # 첨부파일([{name,url}])
            "notice_info": notice_info,            # 공고 정보(공고보기)
        }

    def bid_schedule(self, cltr_mng_no: str) -> dict:
        """입찰일정 및 장소 — cltrMngNo만으로 호출 시 반환되는 회차별 item(유찰 저감)을 표로.
        컬럼: 입찰관리번호(pbctCdtnNo)·회차(pbctNsq)·입찰구분(bidDivNm)·입찰기간·최저입찰가(lowstBidPrcIndctCont).
        개찰일시/개찰장소 필드는 물건상세 API에 없어 개찰=입찰마감시각·장소='온비드(전자입찰)'로 표기."""
        root, err = self._fetch_detail_root(cltr_mng_no, None)
        if root is None:
            return {"available": False, "reason": err or "조회 실패", "rounds": []}
        rounds = []
        for it in root.findall(".//item"):
            def T(tag, _it=it):
                c = _it.findtext(tag)
                return (c or "").strip() if c else ""
            end = T("cltrBidEndDt")
            rounds.append({
                "cdtn_no": T("pbctCdtnNo"),                     # 입찰관리번호
                "round": _num(T("pbctNsq")) or None,            # 회차
                "bid_div": T("bidDivNm"),                       # 입찰구분
                "bid_begin": _dt(T("cltrBidBgngDt")),
                "bid_close": _dt(end),
                "min_price": _num(T("lowstBidPrcIndctCont")),   # 최저입찰가(원)
                "open_dt": _dt(end),                            # 개찰일시(≈입찰마감 — 별도필드 없음)
                "open_place": "온비드(전자입찰)",                 # 개찰장소(기본값)
                "status": T("pbctStatNm"),                      # 공매상태(입찰준비/진행 등)
            })
        rounds.sort(key=lambda x: (x["round"] is None, x["round"] or 0))   # 회차 오름차순(문자열정렬 방지)
        return {"available": bool(rounds), "rounds": rounds, "manage_no": cltr_mng_no}
