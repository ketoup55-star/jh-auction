# -*- coding: utf-8 -*-
"""준공년도(items.build_year) 재판정 — 고친 감정평가서 파서로 (주인님 지시 2026-10-08).

🔴근본 원인(원문에서 확인):
  감정평가서에는 「사용승인일」이 여러 곳에 나온다. ①대상 물건 개요 ②인근 **거래사례** 표
  ③인근 **평가사례** 표. PDF 가 표를 세로로 흘리면서 ②③의 거래시점/기준시점 날짜가
  '사용승인일' 글자 바로 뒤에 오고, 옛 파서는 `re.search`(첫 매치)라 그 **최근 날짜**를 집었다.
  실측 — 효성 1995.09.12(진짜) → 2026.01.30 집음 / 한울 1996.07.25 → 2025.04.22 / 성원 1997 → 2024.08.06.
  그래서 items.build_year 가 2024~2026 으로 쏠렸다(336건).
  고친 파서는 '사용승인' 매치마다 **앞 문맥이 사례 표면 건너뛰고**, 값이 열 제목 몇 줄 뒤에
  오는 표 구조를 감안해 90자 안에서 찾는다. 검증: 효성 1995 · 한울 1996 · 성원 1997 ✅

🔴건축물대장은 다른 함정이 있다: **같은 지번의 다른 신축 건물** 문서가 섞여 있다
  (천안 두정동 525-1 대우아파트(KB 1996·1038세대)의 문서가 "2023.10.13 사용승인되어 신규작성
   [2021-허가담당관-신축허가-203]"). 그래서 대장 값은 KB 와 크게 어긋나고 '신축허가' 문구가
  있으면 쓰지 않는다.

판정 순서
  ① 감정평가서(고친 파서)  — 그 물건을 직접 감정한 문서라 지번 오독이 없다. 1순위.
  ② 건축물대장            — 단, KB(지번확인·세대수 일치)와 10년 이상 다르고 '신축허가' 문구가
                            있으면 다른 건물로 보고 버린다.
  ③ KB build_ym           — 지번까지 확인(conf>=1.4)되고 세대수가 일치할 때만.
  ④ 없으면 **기존 값 유지** (지우지 않는다)

사용:
  python fix_build_year.py --dry --limit 30
  python fix_build_year.py --apply
"""
from __future__ import annotations
import os, sys, time, argparse, re

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)
from dotenv import load_dotenv
load_dotenv(os.path.join(_ROOT, ".env"))
import psycopg


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--keys-out", default="",
                    help="바꾼 item_key 를 적을 파일(디스크 brief 캐시 정리용)")
    a = ap.parse_args()
    apply = a.apply and not a.dry

    import importlib
    m = importlib.import_module("api.main")
    from auction_analysis.building_doc_parser import parse_appraisal_bldg, parse_bldg_doc

    def _emd(addr: str):
        """주소에서 법정동(읍/면/동/가/리)만. '영등포구 영등포동7가 208 …' → '영등포동7가'."""
        for tok in (addr or "").split():
            if re.fullmatch(r"[가-힣][가-힣0-9]*(동\d+가|동|가|읍|면|리)", tok):
                return tok
        return None

    def _doc_is_ours(text: str, addr: str) -> bool:
        """그 문서가 **이 물건의** 것인가 — 법정동이 문서에 나오는지로 거른다.

        🔴A05|2025|12093|1(영등포동7가 포레나…)의 감정평가서가 쌍문동 다세대주택 문서였다.
          그대로 믿었으면 2025 → 1995 로 더 틀리게 바꿀 뻔했다.
        """
        e = _emd(addr)
        if not e or not text:
            return True                     # 판단 근거가 없으면 막지 않는다(기존 동작 유지)
        if e in text:
            return True
        base = re.sub(r"\d+가$", "", e)      # '영등포동7가' → '영등포동'
        return bool(base and base != e and base in text)

    con = psycopg.connect(os.environ["SUPABASE_DB_URL"], prepare_threshold=None,
                          connect_timeout=90, autocommit=True)
    cur = con.cursor()
    cur.execute("set statement_timeout = '110s'")
    #  대상: ①KB(지번확인·세대수 일치)와 3년 이상 다른 것 ②build_year 가 2024~2026 인 것
    #    (둘 다 이번 오독의 모양이다. 전량 재파싱은 18,655건이라 26시간이 걸려 범위를 좁힌다.)
    sql = """
    select distinct i.item_key, left(i.address,46) addr, i.build_year yr,
           i.households hh, c.households kb_hh,
           case when c.build_ym ~ '^[0-9]{6}$' then (left(c.build_ym,4))::int end kby,
           coalesce(i.kb_match_conf,0) conf
      from items i left join kb_complex c on c.complex_no=i.kb_complex_no::text
     where i.data_class='현황' and i.build_year is not null
       and ( (coalesce(i.kb_match_conf,0) >= 1.4 and c.build_ym ~ '^[0-9]{6}$'
              and i.households is not null and c.households is not null
              and i.households = c.households
              and abs(i.build_year-(left(c.build_ym,4))::int) >= 3)
          or i.build_year between 2024 and 2026 )
     order by i.item_key"""
    params: list = []
    if a.limit:
        sql += " limit %s"
        params.append(a.limit)
    cur.execute(sql, params)
    rows = cur.fetchall()
    print(f"대상 {len(rows):,}건 · 모드 {'반영' if apply else '비교만'}")

    t0 = time.time()
    chg = same = keep = skipped_doc = 0
    changed_keys: list = []
    by_src = {"감정": 0, "대장": 0, "KB": 0, "유지": 0}
    for idx, (ik, addr, yr, hh, kb_hh, kby, conf) in enumerate(rows, 1):
        new = None
        src = None
        #  ① 감정평가서(1순위)
        try:
            au = m.auction_db.media_url(ik, "감정평가서")
            if au:
                t = m._pdf_text_pages(au, 14, max_bytes=4_000_000)
                if _doc_is_ours(t, addr):          # 다른 물건의 문서면 쓰지 않는다
                    v = (parse_appraisal_bldg(t) or {}).get("build_year")
                    if v:
                        new, src = int(v), "감정"
                else:
                    skipped_doc += 1
        except Exception:
            pass
        #  ② 건축물대장 — '다른 신축 건물' 문서를 거른다
        if new is None:
            try:
                du = m.auction_db.media_url(ik, "건축물대장")
                if du:
                    dt = m._pdf_text_pages(du, 4)
                    if not _doc_is_ours(dt, addr):
                        dt = ""                    # 다른 물건의 문서
                    v = (parse_bldg_doc(dt) or {}).get("build_year") if dt else None
                    if v:
                        v = int(v)
                        bad = (kby and abs(v - kby) >= 10 and re.search(r"신축허가", dt or ""))
                        if not bad:
                            new, src = v, "대장"
            except Exception:
                pass
        #  ③ KB — 지번까지 확인되고 세대수가 일치할 때만
        if new is None and kby and conf >= 1.4 and hh is not None and hh == kb_hh:
            new, src = kby, "KB"
        #  ④ 없으면 기존 값 유지
        if new is None:
            keep += 1
            by_src["유지"] += 1
            continue
        by_src[src] += 1
        if new == yr:
            same += 1
            continue
        chg += 1
        changed_keys.append(ik)
        if chg <= 40 or not apply:
            print(f"  변경 {ik} {addr}\n       {yr} → {new} ({src}, KB {kby})")
        if apply:
            cur.execute("update items set build_year=%s where item_key=%s", (new, ik))
            #  🔴brief 를 **지우지 말고 고친다**(2026-10-08 되돌림 사고).
            #    지우면 서버가 그 자리에서 다시 계산해 넣고, 20분 스윕 r1(brief→items)이
            #    items 를 그 값으로 덮어써 교정이 되돌아간다. 같은 값으로 맞춰 두면 덮어써도 무해하다.
            cur.execute("""update api_cache
                              set data = jsonb_set(data, '{build_year}', to_jsonb(%s::text), true),
                                  updated_at = now()
                            where cache_key = %s""", (str(new), f"brief:{ik}"))
            if cur.rowcount == 0:        # brief 가 아예 없으면 다음 계산 때 새 파서로 들어온다
                pass
            cur.execute("delete from api_cache where cache_key = %s", (f"apt:{ik}",))
        if idx % 50 == 0:
            el = time.time() - t0
            print(f"  … {idx:,}/{len(rows):,}  {el/60:.1f}분 · 남은 {(len(rows)-idx)*el/idx/60:.0f}분")

    el = time.time() - t0
    print(f"\n═══ 결과 ═══")
    print(f"  변경 {chg:,} · 같음 {same:,} · 원천없어 유지 {keep:,}")
    print(f"  판정 출처: " + " · ".join(f"{k} {v:,}" for k, v in by_src.items()))
    print(f"  다른 물건의 문서로 보고 건너뛴 것: {skipped_doc:,}건")
    print(f"  소요 {el/60:.1f}분 · 건당 {el/max(1,len(rows)):.1f}초")
    if a.keys_out and changed_keys:
        with open(a.keys_out, "w", encoding="utf-8") as f:
            f.write("\n".join(changed_keys))
        print(f"  바꾼 키 {len(changed_keys):,}개 → {a.keys_out}")
    if apply:
        print("  🔴 디스크 brief 캐시(brief_cache.json)는 서버가 들고 있다 — 서버 재시작 필요")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
