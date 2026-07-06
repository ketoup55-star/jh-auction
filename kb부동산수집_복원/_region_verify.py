# -*- coding: utf-8 -*-
"""지역 API(stutCdFilter) parity 검증.

로그인으로 인증헤더 캡처 → 우성(11433) 법정동(4413310400)에 아파트 매매를
지역 API로 호출 → (a) 동작 여부 (b) 우성 매물 수 (c) 필드 범위 (d) 법정동 내
단지 수(묶음 이득) 를 단지 API 기준(108건/131필드)과 비교한다.
결과는 _region_verify.log.
"""
from __future__ import annotations
import os, sys, json, datetime, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
LOG = os.path.join(HERE, "_region_verify.log")

# 우성(complex_no=11433)
COMPLEX_NO = "11433"
LAT, LNG, BUBCODE = 36.837085, 127.132555, "4413310400"
PROPLIST_MAIN_COUNT = 108   # 앞서 propList/main 으로 확인한 우성 매매 총건수

# propList/main 이 주는 131필드(앞서 kb_listing.raw 로 확보)
REF_KEYS = set(['방수','번호','nodupCnt','totalCnt','건물명','단지명','매매가','매물명','사용년','사용월','사용일','연면적','욕실수','월세가','융자금','전세가','총층수','x좌표값','y좌표값','wgs84경도','wgs84위도','건물동명','건물호명','건축면적','계약면적','공급면적','대지면적','방향구분','사용년차','우대금리','읍면동명','전용면적','중복개수','지하구분','지하층수','평당단가','해당층수','카테고리2','가주소여부','건폐율내용','기타매물명','동영상여부','등록년월일','방향구분명','법정동코드','사용승인일','순계약면적','순공급면적','순전용면적','승강기유무','시군구주소','용적률내용','월세보증금','융자금여부','이미지구분','재건축여부','중개업소명','총지상층수','최대매매가','최대월세가','최대전세가','최소매매가','최소월세가','최소전세가','해당층구분','건물최고층수','관심단지여부','매물거래구분','매물상태구분','매물유입구분','매물일련번호','매물종별구분','면적일련번호','복층여부표시','상세번지내용','우대금리여부','융자여부구분','이미지파일명','전자계약여부','주거유형구분','중개업소주소','지번노출여부','클린주택여부','특징광고내용','현관구조내용','단지이미지개수','리브온매물여부','매물거래구분명','매물상태구분명','매물유입구분명','매물이미지개수','매물이미지구분','매물종별구분명','매물확인년월일','비대면대출여부','이미지도메인URL','입주가능일내용','입주가능일타입','조합원분양여부','주거유형구분명','주택형타입내용','최대월세보증금','최소월세보증금','층노출동의여부','해당층공개여부','건축물용도코드명','단지기본일련번호','단지이미지파일명','매물알림수신여부','매물유입구분코드','매물이미지파일명','매물종별그룹구분','중개업소대표자명','중개업소전화번호','확인매물유형구분','매물종별그룹구분명','매물특징유형구분명','입주가능일협의여부','중개업소사업자구분','확인매물유형구분명','중개업소사업자구분명','중개업소시세조사여부','허위매물처리결과구분','이미지디렉토리경로내용','중개업소대표사진파일명','허위매물처리결과구분명','중개업소대표자휴대폰번호','단지이미지디렉토리경로내용','매물이미지디렉토리경로내용'])
KEY_COLS = ['매물일련번호','매물거래구분명','매매가','전용면적','공급면적','해당층수','총층수','건물동명','건물호명','방향구분명','방수','욕실수','평당단가','중개업소명','중개업소주소','중개업소전화번호','중개업소대표자명','중개업소대표자휴대폰번호','매물유입구분명','등록년월일','매물확인년월일','특징광고내용','매물이미지개수','중복개수','단지기본일련번호','단지명']

def log(m):
    line = f"[{datetime.datetime.now():%H:%M:%S}] {m}"
    open(LOG, "a", encoding="utf-8").write(line + "\n"); print(line)

def run(email, password):
    open(LOG, "w", encoding="utf-8").close()
    log("=== 지역 API parity 검증 ===")
    try:
        import fetch
        log("로그인 + 인증헤더 캡처 중...")
        headers = fetch.login(email, password, headless=False)
        log(f"  헤더 캡처 완료 (keys={len(headers)}). authorization 포함={'authorization' in {k.lower() for k in headers}}")

        log(f"지역 API(stutCdFilter)로 우성 법정동({BUBCODE}) 아파트 매매 수집...")
        props = fetch.get_all_properties(
            lat=LAT, lng=LNG, property_type="01", transaction_type="1",
            lawd_code=BUBCODE, request_headers=headers)
        log(f"  지역 API 반환 매물 총 {len(props)}건 (법정동 전체 아파트 매매)")

        # 단지별 그룹
        from collections import Counter
        by_complex = Counter(str(p.get("단지기본일련번호")) for p in props)
        by_name = Counter(p.get("단지명") for p in props)
        log(f"  법정동 내 distinct 단지: {len(by_complex)}개 (= 묶음호출 1회로 커버되는 단지 수)")
        log(f"  상위 단지: " + ", ".join(f'{n}({c})' for n,c in by_name.most_common(6)))

        # 우성(11433) 매물만
        usung = [p for p in props if str(p.get("단지기본일련번호")) == COMPLEX_NO
                 or (p.get("단지명") or "").startswith("우성")]
        log(f"  >>> 우성 매물: 지역API {len(usung)}건  vs  단지API(propList/main) {PROPLIST_MAIN_COUNT}건")

        if props:
            sk = set(props[0].keys())
            log(f"  지역API 필드 수: {len(sk)} (단지API 131)")
            missing = sorted(REF_KEYS - sk)
            log(f"  단지API 대비 누락 필드({len(missing)}): {missing[:40]}")
            miss_key = [k for k in KEY_COLS if k not in sk]
            log(f"  핵심 적재컬럼 {len(KEY_COLS)}개 중 누락: {miss_key if miss_key else '없음(전부 존재)'}")
            log("  샘플 매물: " + json.dumps({k: props[0].get(k) for k in
                ['단지명','건물동명','건물호명','매매가','전용면적','해당층수','중개업소명','중개업소전화번호']}, ensure_ascii=False))
        log("=== 검증 종료 ===")
    except Exception as e:
        log(f"ERROR: {e}"); log(traceback.format_exc())

def main():
    import tkinter as tk
    root = tk.Tk(); root.title("지역API 검증"); root.geometry("340x180")
    tk.Label(root, text="카카오 이메일").pack(anchor="w", padx=12, pady=(12,0))
    e1 = tk.Entry(root, width=38); e1.pack(padx=12)
    tk.Label(root, text="카카오 비밀번호").pack(anchor="w", padx=12, pady=(8,0))
    e2 = tk.Entry(root, width=38, show="*"); e2.pack(padx=12)
    st = tk.Label(root, text="", fg="blue"); st.pack(pady=4)
    def go():
        st.config(text="실행 중..."); root.update()
        run(e1.get().strip(), e2.get()); st.config(text="완료. _region_verify.log 확인")
    tk.Button(root, text="검증 시작", command=go).pack(pady=8)
    root.mainloop()

if __name__ == "__main__":
    main()
