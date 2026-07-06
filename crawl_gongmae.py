# -*- coding: utf-8 -*-
"""온비드 부동산 공매 물건 전량 수집 → Supabase gongmae_items 저장(소재지 필터용).

온비드 API가 소재지 필터를 지원하지 않아, 전량을 우리 DB에 담아 우리가 필터한다.
 - onbid_source(OnbidSource._summary) 재사용, numOfRows=1000 페이징.
 - 재산유형 전체(압류/국유/수탁/유입/공유/기타) 순회.
 - manage_no(물건관리번호) 기준 upsert. 일 1회 갱신 권장.
"""
import os
import sys
import json
import time
import xml.etree.ElementTree as ET

import httpx
import psycopg

_ROOT = os.path.dirname(os.path.abspath(__file__))
for _line in open(os.path.join(_ROOT, ".env"), encoding="utf-8"):
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip())
sys.path.insert(0, _ROOT)
from auction_analysis.onbid_source import OnbidSource, PROP_CD  # noqa: E402

_URL = "https://apis.data.go.kr/B010003/OnbidRlstListSrvc2/getRlstCltrList2"
_UA = {"User-Agent": "Mozilla/5.0"}
KEY = os.environ["ONBID_SERVICE_KEY"]
DBURL = os.environ["SUPABASE_DB_URL"]
onbid = OnbidSource()

_UPSERT = """
INSERT INTO gongmae_items
  (id,manage_no,address,usage,prop_type,disposal,name,bid_close,min_price,appraisal_price,data,updated_at)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
ON CONFLICT (id) DO UPDATE SET
  manage_no=EXCLUDED.manage_no, address=EXCLUDED.address, usage=EXCLUDED.usage,
  prop_type=EXCLUDED.prop_type, disposal=EXCLUDED.disposal, name=EXCLUDED.name,
  bid_close=EXCLUDED.bid_close, min_price=EXCLUDED.min_price,
  appraisal_price=EXCLUDED.appraisal_price, data=EXCLUDED.data, updated_at=now();
"""


def _page(prop_cd, page):
    p = {"serviceKey": KEY, "pageNo": str(page), "numOfRows": "1000",
         "resultType": "xml", "pvctTrgtYn": "N", "prptDivCd": prop_cd}
    r = httpx.get(_URL, params=p, headers=_UA, timeout=60)
    root = ET.fromstring(r.text)
    total = int((root.findtext(".//totalCount") or "0").strip() or 0)
    items = root.findall(".//items/item")
    return items, total


def main():
    conn = psycopg.connect(DBURL, prepare_threshold=None, connect_timeout=20, autocommit=False)
    run_start = conn.execute("SELECT now()").fetchone()[0]   # 이 시각 이후 upsert된 것만 '이번 크롤에 살아있는' 물건
    saved = 0
    for prop_name, prop_cd in PROP_CD.items():
        page = 1
        while True:
            try:
                items, total = _page(prop_cd, page)
            except Exception as e:
                print(f"[{prop_name}] p{page} 오류: {str(e)[:60]} — 3초 후 재시도", flush=True)
                time.sleep(3)
                continue
            if not items:
                break
            batch = []
            for it in items:
                s = onbid._summary(it)
                iid = (s.get("id") or "").strip()
                if not iid or iid == "||":
                    continue
                batch.append((iid, s.get("manage_no"), s.get("address"), s.get("usage"),
                              s.get("prop_type"), s.get("disposal"), s.get("name"),
                              s.get("bid_close"), s.get("min_price"), s.get("appraisal_price"),
                              json.dumps(s, ensure_ascii=False)))
            with conn.cursor() as cur:
                cur.executemany(_UPSERT, batch)
            conn.commit()
            saved += len(batch)
            print(f"[{prop_name}] p{page}: +{len(batch)}건 (누적 {saved}) / total {total}", flush=True)
            if page * 1000 >= total:
                break
            page += 1
            time.sleep(0.4)   # 온비드 rate limit 보호
    print(f"수집 완료. 총 {saved}건 upsert.", flush=True)
    # ── 마감정리: 이번 크롤에 안 잡힌(=온비드에서 빠진=마감된) 물건 삭제 ──
    # 부분실패(API 장애 등)로 saved가 비정상적으로 적으면 대량삭제 방지 위해 스킵.
    if saved >= 30000:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM gongmae_items WHERE updated_at < %s", (run_start,))
            purged = cur.rowcount
        conn.commit()
        print(f"마감정리: {purged}건 삭제(온비드 목록에서 빠진 물건).", flush=True)
    else:
        print(f"마감정리 스킵: saved={saved} < 30000 (부분실패 의심, 안전상 삭제 안 함).", flush=True)
    conn.close()
    _rebuild_regions()


def _rebuild_regions():
    """시도→시군구 지역맵을 static/data/gongmae_regions.json로 갱신(드롭다운용)."""
    try:
        conn = psycopg.connect(DBURL, prepare_threshold=None, connect_timeout=20)
        cur = conn.execute(
            "SELECT split_part(address,' ',1) sd, split_part(address,' ',2) sg, count(*) "
            "FROM gongmae_items WHERE address IS NOT NULL AND address<>'' GROUP BY 1,2")
        m = {}
        for sd, sg, _n in cur.fetchall():
            if not sd:
                continue
            m.setdefault(sd, set())
            if sg:
                m[sd].add(sg)
        conn.close()
        out = {sd: sorted(sgg) for sd, sgg in m.items()}
        os.makedirs(os.path.join(_ROOT, "static", "data"), exist_ok=True)
        with open(os.path.join(_ROOT, "static", "data", "gongmae_regions.json"), "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False)
        print(f"지역맵 갱신: 시도 {len(out)} / 시군구 {sum(len(v) for v in out.values())}", flush=True)
        # 용도맵(대분류→세부용도, 빈도순)
        conn = psycopg.connect(DBURL, prepare_threshold=None, connect_timeout=20)
        cur = conn.execute(
            "SELECT split_part(usage,' ',1) lc, split_part(usage,' ',2) sc, count(*) n "
            "FROM gongmae_items WHERE usage IS NOT NULL AND usage<>'' GROUP BY 1,2")
        um = {}
        for lc, sc, n in cur.fetchall():
            if not lc:
                continue
            um.setdefault(lc, {})
            if sc:
                um[lc][sc] = um[lc].get(sc, 0) + n
        conn.close()
        uout = {lc: [s for s, _ in sorted(sc.items(), key=lambda x: -x[1])] for lc, sc in um.items()}
        with open(os.path.join(_ROOT, "static", "data", "gongmae_usages.json"), "w", encoding="utf-8") as f:
            json.dump(uout, f, ensure_ascii=False)
        print(f"용도맵 갱신: 대분류 {len(uout)} / 세부 {sum(len(v) for v in uout.values())}", flush=True)
    except Exception as e:
        print(f"지역/용도맵 갱신 실패: {str(e)[:80]}", flush=True)


if __name__ == "__main__":
    main()
