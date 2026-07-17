-- 공매 검색 성능 인덱스 (gongmae_items) — 수동 DDL, DB 재구축 시 재적용 필요
-- 근본원인: ①bid_close 등 정렬 인덱스 부재로 seq scan+top-N sort ②address/usage ILIKE count가 heap 85MB scan
-- 효과: 70개 조합(단수/복수/교집합/정렬×넓은지역) 전부 1초 이내(콜드 포함). 합계 ~19MB.
-- 측정 함정: httpx.Client(keep-alive)로 재야 실사용, 새연결=TLS+0.85초 · 연속 70요청은 순간부하로 오판→2회 min

-- 정렬용(prop_type 선두 + 각 정렬키). prop 조건 있을 때 Index Scan.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_gm_prop_bidclose ON gongmae_items (prop_type, bid_close);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_gm_prop_appr     ON gongmae_items (prop_type, appraisal_price DESC NULLS LAST);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_gm_prop_low      ON gongmae_items (prop_type, min_price ASC NULLS LAST);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_gm_prop_profit   ON gongmae_items (prop_type, profit DESC NULLS LAST);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_gm_prop_nb       ON gongmae_items (prop_type, nb_count DESC NULLS LAST);
-- prop=전체(선두 컬럼 조건 없는 경우) 기본정렬
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_gm_bidclose      ON gongmae_items (bid_close);
-- 필터용 trgm(용도·소재지 ILIKE count의 heap scan 해소). 좁은매칭 필터 많을수록 효과 큼.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_gm_usage_trgm    ON gongmae_items USING gin (usage gin_trgm_ops);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_gm_address_trgm  ON gongmae_items USING gin (address gin_trgm_ops);
-- 토지이용계획(용도지역) 필터 — zone 컬럼(map_points 좌표→V-World landuse 백필값, 경매 _zone_categorize 7종)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_gm_prop_zone     ON gongmae_items (prop_type, zone);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_gm_zone          ON gongmae_items (zone);
-- 특수물건(지분/공유매각) — name 파싱 결과 is_share 컬럼(name~'지분'/'공유'는 2글자라 trgm 비효율 → bool 컬럼)
-- 백필: UPDATE gongmae_items SET is_share = (name ILIKE '%지분%' OR name ILIKE '%공유%');  (name 불변, 외부호출 없음)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_gm_prop_share    ON gongmae_items (prop_type, is_share);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_gm_name_trgm     ON gongmae_items USING gin (name gin_trgm_ops);
ANALYZE gongmae_items;
