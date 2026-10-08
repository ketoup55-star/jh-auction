# -*- coding: utf-8 -*-
"""KB 단지 재매칭 — items.kb_complex_no / kb_match_conf 를 지번 대조 기준으로 다시 채운다.

🔴2026-10-08 주인님 지적(군산 나운동 489 금호 104동): 실거래 1억대인데 경쟁매물 호가가 3억대.
  같은 지번 489 의 물건이 주소 표기에 따라 세 갈래로 매칭돼 있었다
  ('금호타운아파트'→금호타운(1차) 930세대 / '금호'·'금호아파트'→나운금호어울림센트럴 993세대 2022년 신축).
  원인은 kb_crawler._score 가 단지명 **부분 문자열 포함**만 본 것 — 군산 후보 6개가 전부 1.0 동점이 되고
  먼저 나온 쪽이 이겼다. 그 호가가 추정시세 (실거래+호가)/2 에 들어가 차익을 거짓으로 만들었다.

이 스크립트는 kb_crawler 의 고친 _score(지번 ARNO 대조)로 **전량 재판정**한다.
  · KB 통합검색(kb_search)은 공개 API — 토큰이 필요 없다.
  · 매칭만 갱신한다. 매물(kb_listing) 재수집은 하지 않는다(토큰 필요).
    단지가 바뀌어 우리 DB에 그 단지 매물이 없으면 화면은 실시간 폴백으로 간다.
  · kb_match_conf 가 **1.4 이상이면 지번까지 확인된 매칭**이다(base 0.4~0.5 + 이름 0~0.5 + 지번 1.0).
    종전에는 3,405건이 모두 1.0 만점이라 변별력이 전혀 없었다.

사용:
  python kb_rematch.py --dry  --limit 50     # 바꾸지 않고 비교만
  python kb_rematch.py --apply               # 실제 반영
"""
from __future__ import annotations
import os, sys, time, argparse

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
import psycopg
from kb_crawler import match_address

#  이 값 이상이면 지번까지 맞은 매칭. api/main.py 가 '호가를 시세에 섞을지' 판단할 때도 같은 선을 쓴다.
JIBUN_SURE = 1.4


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="DB 에 반영(없으면 비교만)")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only-apt", action="store_true", default=True)
    a = ap.parse_args()
    apply = a.apply and not a.dry

    con = psycopg.connect(os.environ["SUPABASE_DB_URL"], prepare_threshold=None,
                          connect_timeout=90, autocommit=True)
    cur = con.cursor()
    cur.execute("set statement_timeout = '110s'")
    sql = """select i.item_key, i.address, i.area_excl, i.kb_complex_no, c.name
               from items i left join kb_complex c on c.complex_no = i.kb_complex_no::text
              where i.data_class='현황' and i.usage_name like %s
              order by i.item_key"""
    params: list = ["%아파트%"]
    if a.limit:
        sql += " limit %s"
        params.append(a.limit)
    cur.execute(sql, params)
    rows = cur.fetchall()
    print(f"대상 {len(rows):,}건 · 모드 {'반영' if apply else '비교만'}")

    t0 = time.time()
    same = chg = newly = lost = err = sure = 0
    for idx, (ik, addr, ar, old, oldname) in enumerate(rows, 1):
        try:
            r = match_address(addr, hints={"area_excl": ar})
        except Exception as e:
            err += 1
            if err <= 5:
                print(f"  !! {ik} {e}")
            continue
        new = r.get("complex_no")
        conf = float(r.get("confidence") or 0)
        if conf >= JIBUN_SURE:
            sure += 1
        if new is None and old is not None:
            lost += 1
        elif new is not None and old is None:
            newly += 1
        elif str(new) == str(old):
            same += 1
        else:
            chg += 1
            if chg <= 40:
                print(f"  변경 {ik} {addr[:48]}\n       {oldname} → {r.get('kb_name')} "
                      f"(conf {conf} jibun_ok={r.get('jibun_ok')})")
        if apply:
            #  🔴새 결과가 없으면 **기존 값을 건드리지 않는다**(2026-10-08 회귀에서 확인).
            #    도로명 주소 물건은 괄호 속 단지명을 못 뽑아 매칭에 실패하는데(api/main.py:10101 참조),
            #    그걸 None 으로 덮어쓰면 과거에 다른 경로로 채워 둔 멀쩡한 매칭까지 잃는다
            #    (회귀 200건 중 7건 = 3.5%, 전량이면 약 120건).
            if new is None:
                pass
            #  매칭이 바뀌면 그 단지 기준으로 계산된 캐시(시세·경쟁매물)는 모두 무효다.
            #    kb_* 집계도 옛 단지 것이므로 비운다. 다음 조회 때 새 단지로 다시 계산된다.
            elif str(new) != str(old):
                cur.execute("""update items set kb_complex_no=%s, kb_match_conf=%s,
                                      kb_deal_cnt=null, kb_lease_cnt=null, kb_rent_cnt=null,
                                      kb_deal_min=null, kb_deal_max=null, kb_synced_at=null,
                                      est_price=null, expected_bid=null, profit=null, kb_count=null
                                where item_key=%s""",
                            (str(new) if new else None, conf or None, ik))
                cur.execute("delete from api_cache where cache_key = any(%s)",
                            ([f"apt:{ik}", f"analysis:{ik}", f"expbid:{ik}", f"vexpbid:{ik}",
                              f"kbmatch:{ik}"],))
            else:
                cur.execute("update items set kb_match_conf=%s where item_key=%s",
                            (conf or None, ik))
        if idx % 200 == 0:
            el = time.time() - t0
            print(f"  … {idx:,}/{len(rows):,}  {el/60:.1f}분 경과 · "
                  f"남은 {(len(rows)-idx)*el/idx/60:.0f}분")

    el = time.time() - t0
    print(f"\n═══ 결과 ═══")
    print(f"  그대로 {same:,} · 변경 {chg:,} · 새로매칭 {newly:,} · 매칭상실 {lost:,} · 오류 {err}")
    print(f"  지번까지 확인(conf≥{JIBUN_SURE}) {sure:,}건 ({sure*100/max(1,len(rows)):.1f}%)")
    print(f"  소요 {el/60:.1f}분 · 건당 {el/max(1,len(rows)):.2f}초")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
