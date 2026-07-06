# -*- coding: utf-8 -*-
"""Phase B: 신규 복구된 55건(매칭됐지만 매물 미수집)의 매매 매물 수집·적재.
   카카오 로그인 창 → 토큰 획득 → 각 단지 매매 수집 → kb_listing/kb_complex/items 적재.
   결과는 _phase_b.log."""
import os, sys, time, json

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("SUPABASE_DB_URL",
    "postgresql://postgres.jakwbngokvlzehpjiozh:%40Q%21W%40E%23R%24T%25@aws-1-ap-northeast-2.pooler.supabase.com:6543/postgres")
import kb_crawler as k
k.DB_URL = os.environ["SUPABASE_DB_URL"]

LOG = os.path.join(HERE, "_phase_b.log")

def log(m):
    open(LOG, "a", encoding="utf-8").write(f"[{time.strftime('%H:%M:%S')}] {m}\n")
    print(m, flush=True)

def run(email, password):
    open(LOG, "w", encoding="utf-8").close()
    k.KB_EMAIL, k.KB_PW = email, password
    log("카카오 로그인 → 토큰 획득...")
    try:
        token = k.AUTH.get_token()
    except Exception as e:
        log(f"로그인 실패: {e}"); return
    log(f"토큰 OK (len={len(token)}).")

    con = k._db_connect(); con.autocommit = False; cur = con.cursor()
    cur.execute("""select m.item_key, i.address from kb_item_match m join items i on i.item_key=m.item_key
                   where m.status='matched' and i.kb_synced_at is null order by m.item_key""")
    rows = cur.fetchall()
    log(f"대상 {len(rows)}건 수집 시작")
    stat = {"done": 0, "listings": 0, "zero": 0, "errors": 0}
    for item_key, address in rows:
        try:
            m = k.match_address(address or "")
            cno = m.get("complex_no")
            if not cno:
                stat["errors"] += 1; log(f"  ! {item_key} 재매칭 실패"); continue
            counts = k.kb_count_by_trade(cno)
            listings = k.kb_list_complex_all(cno, trade_code="1")
            counts["매매건수"] = len(listings)
            k._upsert_complex(cur, cno, m["best_raw"], counts)
            ids = []
            for p in listings:
                lid = k._upsert_listing(cur, p, cno, item_key)
                if lid is not None:
                    ids.append(lid); stat["listings"] += 1
            k._deactivate_missing(cur, cno, item_key, ids)
            k._update_item_summary(cur, item_key, cno, m.get("confidence"), counts, listings)
            con.commit()
            stat["done"] += 1
            if not listings:
                stat["zero"] += 1
            log(f"  {item_key} → {m.get('kb_name')} 매매 {len(listings)}건")
        except Exception as e:
            con.rollback(); stat["errors"] += 1; log(f"  ! {item_key} ERR {str(e)[:100]}")
    cur.close(); con.close()
    log(f"Phase B 완료: {stat}")

def main():
    import tkinter as tk
    root = tk.Tk(); root.title("Phase B 수집"); root.geometry("340x180")
    tk.Label(root, text="카카오 이메일").pack(anchor="w", padx=12, pady=(12, 0))
    e1 = tk.Entry(root, width=38); e1.pack(padx=12)
    tk.Label(root, text="카카오 비밀번호").pack(anchor="w", padx=12, pady=(8, 0))
    e2 = tk.Entry(root, width=38, show="*"); e2.pack(padx=12)
    st = tk.Label(root, text="", fg="blue"); st.pack(pady=4)
    def go():
        st.config(text="실행 중... (완료까지 창 유지)"); root.update()
        run(e1.get().strip(), e2.get()); st.config(text="완료. _phase_b.log 확인")
    tk.Button(root, text="로그인하고 55건 수집", command=go).pack(pady=8)
    root.mainloop()

if __name__ == "__main__":
    main()
