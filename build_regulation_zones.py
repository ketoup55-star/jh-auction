"""
규제지역/토지거래허가구역 경계 폴리곤 → static/data/regulation_zones.json 생성.
대상(2026.7 기준, 대출규제 docx): 서울 전역 + 경기 15곳. (규제=토허 사실상 동일 범위)
V-World 행정경계: 서울=시도(LT_C_ADSIDO_INFO), 경기=시군구/구(LT_C_ADSIGG_INFO).
경기 구 이름 중복 방지: sig_cd 가 '41'(경기)로 시작하는 것만.
"""
import os, json
import httpx

env = {}
for line in open(os.path.join(os.path.dirname(__file__), ".env"), encoding="utf-8"):
    line = line.strip()
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1); env[k] = v.strip()
K, DOM = env["VWORLD_KEY"], env.get("VWORLD_DOMAIN", "")

def fetch(data, attr, val):
    r = httpx.get("https://api.vworld.kr/req/data",
                  params={"service": "data", "version": "2.0", "request": "GetFeature",
                          "data": data, "key": K, "domain": DOM,
                          "attrFilter": f"{attr}:like:{val}", "crs": "EPSG:4326",
                          "format": "json", "size": "20", "geometry": "true"}, timeout=25)
    try:
        return r.json()["response"]["result"]["featureCollection"]["features"]
    except Exception:
        return []

zones = []
# 서울 전역(시도 1폴리곤)
for f in fetch("LT_C_ADSIDO_INFO", "ctp_kor_nm", "서울특별시"):
    zones.append({"name": "서울특별시", "kind": "both", "geometry": f["geometry"]}); break

# 경기 시 단위(구 없는 시) — sig_kor_nm 정확히 일치
for nm in ["과천시", "광명시", "의왕시", "하남시", "구리시"]:
    for f in fetch("LT_C_ADSIGG_INFO", "sig_kor_nm", nm):
        if f.get("properties", {}).get("sig_kor_nm") == nm:
            zones.append({"name": nm, "kind": "both", "geometry": f["geometry"]}); break
    else:
        print("  [경고] 못 찾음:", nm)
# 경기 구 단위 — 시 접두로 조회 후 대상 구만(권선·만안·처인·화성 나머지 제외)
GG_GU_FULL = {"성남시 수정구", "성남시 중원구", "성남시 분당구",
              "수원시 영통구", "수원시 장안구", "수원시 팔달구",
              "안양시 동안구", "용인시 수지구", "용인시 기흥구", "화성시 동탄구"}
got = set()
for si in ["성남시", "수원시", "안양시", "용인시", "화성시"]:
    for f in fetch("LT_C_ADSIGG_INFO", "sig_kor_nm", si):
        nm = f.get("properties", {}).get("sig_kor_nm", "")
        if nm in GG_GU_FULL:
            zones.append({"name": nm, "kind": "both", "geometry": f["geometry"]}); got.add(nm)
for miss in GG_GU_FULL - got:
    print("  [경고] 못 찾음:", miss)

out = os.path.join(os.path.dirname(__file__), "static", "data", "regulation_zones.json")
os.makedirs(os.path.dirname(out), exist_ok=True)
json.dump({"zones": zones}, open(out, "w", encoding="utf-8"), ensure_ascii=False)
print(f"저장: {len(zones)}개 zone → {out}")
for z in zones:
    print(f"  - {z['name']} ({z['geometry']['type']})")
