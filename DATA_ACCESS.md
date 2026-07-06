# 경매 데이터 연동 가이드 (DATA_ACCESS)

이 문서는 **스피드옥션 크롤러가 수집한 경매 데이터**를 다른 웹 프로젝트(예: `budongsan`, FastAPI)에서
사용하기 위한 연동 안내입니다. 이 파일을 그쪽 프로젝트의 Claude Code에게 주고 "이대로 연동해줘" 하면 됩니다.

---

## 1. 데이터 위치
- **텍스트(사건·가격·기일내역 등)** → Supabase(PostgreSQL)
- **사진·서류(PDF)** → Cloudflare R2 (공개 URL로 접근)

데이터는 클라우드에 있어 **파일 이전 불필요** — 접속 정보만 있으면 어디서든 읽습니다.
크롤러가 계속 채워넣으므로 **실시간으로 최신 데이터**를 봅니다.

## 2. 접속 정보 (.env 에 추가)
```
# Supabase
SUPABASE_URL=https://jakwbngokvlzehpjiozh.supabase.co
SUPABASE_ANON_KEY=sb_publishable_OAKI_mJcm8v9M4n1WLRotQ_wF0sl5p-
# 서버(백엔드)에서 직접 DB 읽을 때 사용 (비밀! 크롤러 프로젝트 .env의 SUPABASE_DB_URL 값을 그대로 복사)
SUPABASE_DB_URL=postgresql://postgres.xxx:비밀번호@aws-1-ap-northeast-2.pooler.supabase.com:6543/postgres

# Cloudflare R2 (사진·서류 공개 URL)
R2_PUBLIC_URL=https://pub-edb1dd3fca454e75b710b61210fb9cbe.r2.dev
```
> ⚠️ `SUPABASE_ANON_KEY`/`R2_PUBLIC_URL`은 공개 가능. `SUPABASE_DB_URL`은 비밀(서버 .env / 배포 환경변수에만, 깃 금지).
> CloudType 배포 시 위 값들을 환경변수로 등록.

## 3. 읽는 방식 (둘 중 택1 — 프로젝트 클로드가 판단)
- **(권장) 서버(FastAPI)가 `SUPABASE_DB_URL`로 직접 읽기**: psycopg로 쿼리. RLS 신경 안 써도 됨.
- (대안) 프론트엔드 JS가 `supabase-js` + anon키로 직접 읽기: 이 경우 아래 9번 RLS SQL 먼저 실행 필요.

## 4. 테이블 스키마

### `items` (물건 1건 = 1행, PK: `item_key`)
| 컬럼 | 의미 |
|---|---|
| item_key | 고유키 `법원코드\|연도\|사건일련\|물건번호` |
| case_no | 사건번호 (예: 2024-12345) |
| court_name | 법원/담당계 |
| usage_name | 용도(아파트/다세대/상가/차종 등) |
| search_group | 수집그룹(주거용/상가/차량외) |
| address | 소재지 |
| area_text | 면적(원문) / land_area, building_area |
| appraisal_price | 감정가(숫자) / appraisal_raw(원문) |
| min_price | 최저가(숫자) / min_price_raw(원문) |
| **sale_price** | **낙찰가(숫자)** / sale_rate(낙찰가율%) |
| **sale_2nd_price** | **2등 입찰가** / bid_count(입찰자수) |
| sell_date | 매각기일 | result | 결과 | fail_count | 유찰횟수 |
| creditor/debtor/owner | 채권자/채무자/소유자 |
| deposit/claim_amount | 보증금/청구금액 |
| case_received/dividend_deadline/decision_date | 사건접수/배당종기일/개시결정 |
| thumb_url | 목록 썸네일 URL |
| data_class | **현황 / 백데이터** (진행중 vs 과거) |
| status_reason | 진행중/낙찰완료/재매각/재진행 |
| detail_text | 세로보기 분석본문(권리관계·임차인·감정·물건현황). 부동산만, 차량외는 null |
| **changed_fields** | **이번 갱신에서 값이 바뀐 컬럼명 목록(jsonb 배열)** — 캐시 무효화 판단용 |
| **media_updated_at** | **문서·사진이 추가/교체된 시각**(timestamptz). 미디어 안 바뀌면 그대로 |
| first_seen/last_seen/updated_at | 수집 시각 |

#### 증분 갱신 — `changed_fields` / `media_updated_at` (캐시 무효화용)
물건이 갱신될 때마다 **무엇이 바뀌었는지**를 기록합니다. 무거운 캐시(권리분석·PDF·실거래시세)를 통째로 버리지 말고 바뀐 것만 무효화하세요.
- **신규 적재**: `changed_fields = ["__new__"]` (전부 새로 계산)
- **기존 갱신**: 실제로 값이 바뀐 컬럼만. 예 `["sell_date","result","fail_count","min_price"]` (진행정보만 바뀜 → 문서/시세 캐시 보존)
- **문서·사진 변경 시**: `media_updated_at` 갱신 + `changed_fields`에 `"media"` 포함 → 그 문서 캐시만 재파싱
- **변경 없음**: `changed_fields = []` (캐시 그대로)
- (참고: `hit_count`(조회수)는 노이즈라 변경감지에서 제외)

```sql
-- updated_at 대신 changed_fields로 정밀 분기
select item_key, changed_fields, media_updated_at, updated_at
from items where updated_at > :last_seen_ts;
-- 예) changed_fields가 ["sell_date","result"]뿐 → 진행정보만 화면 갱신, PDF/시세 캐시 유지
--     changed_fields에 "media" 또는 media_updated_at 상승 → 그 물건 문서 캐시만 무효화
--     changed_fields에 "address"/"area_text" → 실거래 추정시세 캐시만 무효화
```

### `auction_schedule` (기일내역, item_key FK · 회차 1건 = 1행)
물건의 회차별 진행 이력. **매각(낙찰)된 회차엔 낙찰정보가 채워짐**(유찰/허가/미납 회차는 null).

| 컬럼 | 의미 |
|---|---|
| round | 회차 (신건/2차/3차…) |
| sell_date | 매각기일 (또는 매각결정기일/대금지급기한) |
| min_price | 최저매각금액(원문 텍스트) |
| result | 결과 (유찰/매각/허가/미납/허가취소 등) |
| **sale_price** | **낙찰가**(숫자, 그 회차 매각가) |
| sale_rate | 낙찰가율(예: `91%`) |
| **bid_count** | **입찰자수** |
| sale_2nd_price | 2등 입찰가 |
| winner_name | 낙찰자명 |

> 재매각된 물건은 과거 매각 회차마다 낙찰가가 각각 들어감(예: 2차 낙찰 192,750,000 / 6차 낙찰 213,342,369). `item_key`로 조인, `order by id`로 회차순 정렬.

### `media` (사진·서류, item_key FK)
| 컬럼 | 의미 |
|---|---|
| kind | photo / 등기(집합·토지·건물) / 감정평가서 / 현황조사서 / 부동산표시 / 건축물대장 / 문건접수송달 / 기일내역문서 / 사건내역 / 매각물건명세서 — PDF는 파일, 표 형태 자료는 `text/html` 본문 |
| seq | 순번 |
| **r2_key** | R2 파일 경로 → 공개 URL = `{R2_PUBLIC_URL}/{r2_key}` |
| content_type / bytes | MIME / 크기 |

### `vehicle_specs` (차량외 물건의 차량/중기현황, item_key FK · 차량 1건 = 1행)
> 차량외(승용·SUV 등) 물건의 상세페이지 "차량/중기현황" 표를 구조화한 것. `items.search_group='차량외'`인 물건에만 존재. 같은 Supabase에 있으며 `item_key`로 `items`와 조인(사건번호는 `items.case_no`).

| 컬럼 | 의미 |
|---|---|
| item_key | 고유키 `법원\|연도\|사건일련\|물건번호` (PK, → `items.item_key`) |
| manufacturer | 제조사(브랜드) |
| model | 차종명(원문) |
| model_year | 연식(int) |
| fuel | 사용연료 (경유/휘발유/전기/하이브리드 등) |
| displacement_cc | 배기량(cc, int · 전기차는 0) |
| mileage_km | 주행거리(km, bigint) |
| transmission | 변속기 |
| color | 색상 |
| engine_type | 원동기형식 |
| plate_no | 등록번호 |
| reg_date | 등록일자 |
| vin | 차대번호 |
| inspection_period | 검사기간 |
| storage_location | 보관장소 |
| approval_no | 승인번호 |
| updated_at | 갱신시각 |

> ⚠️ 일부 물건은 등록자가 표 일부 칸을 비워둬 `mileage_km`/`fuel`/`model_year`가 null일 수 있음(원본 미입력). `items.search_group='차량외'`인데 `vehicle_specs` 행이 아예 없으면 차량표 자체가 미입력된 매물.

조인 예시:
```sql
select i.case_no, i.court_name, i.min_price, v.manufacturer, v.model,
       v.model_year, v.fuel, v.displacement_cc, v.mileage_km
from items i join vehicle_specs v on v.item_key = i.item_key
where i.search_group = '차량외'
order by i.sell_date;
```

### 권리분석 구조화 — `item_rights` / `item_tenants` (+ `items` 분석컬럼)
speedauction이 **이미 판정해둔** 권리분석(소멸/인수·대항력·소멸기준)을 구조화한 것. 부동산만(차량외 제외). `analyzed_at IS NOT NULL`이면 분석 데이터 있음 → 없으면 PDF/detail_text 폴백.

**`items` 추가 컬럼**
| 컬럼 | 의미 |
|---|---|
| rights_baseline_date | 말소(소멸)기준일 |
| total_debt | 채권총액 |
| appraisal_land / appraisal_land_pct | 감정 토지가 / 비율(%) |
| appraisal_building / appraisal_building_pct | 감정 건물가 / 비율(%) |
| analyzed_at | 구조화 완료 시각(=완료 플래그) |

**`item_rights`** (등기 권리목록, item_key FK)
| 컬럼 | 의미 |
|---|---|
| seq | 순번 |
| right_type | 권리종류(소유권/(근)저당/가압류/임의경매/압류/주택임차권/전세권 등) |
| reg_date | 등기일 | holder | 권리자 | amount | 금액(원) |
| **status** | **소멸 / 인수** (speedauction 판정값) |
| is_baseline | 소멸(말소)기준 권리 여부 |
| gubun | 건물/토지/집합 |

**`item_tenants`** (임차인, item_key FK)
| 컬럼 | 의미 |
|---|---|
| seq | 순번 | name | 임차인명 |
| **has_opposing_power** | **대항력 유무(true/false)** |
| move_in_date/fixed_date/dividend_date | 전입/확정/배당일 |
| deposit | 보증금 | tenant_right | 권리(주거임차인 등) | occupancy | 점유 |
| status | 인수/소멸/배당 판정(예: "전액매수인 인수예상") |
| **assume_amount** | **매수인 인수 예상 금액(원)** — 전액인수=보증금, 일부배당=미배당금, 소멸/대항없음=0 |
| dividend_amount / undistributed_amount | 배당금 / 미배당금(인수액 근거) |

> ⚠️ 판정값(status·has_opposing_power·is_baseline)은 **speedauction이 계산한 것을 그대로** 적재 — 사이트는 표시만 하면 됨(재계산 불필요). detail_text 원문 파싱이라 일부 필드 null 가능(best-effort).

조인 예시:
```sql
-- 한 물건의 권리분석 전체
select * from items where item_key = :k;
select * from item_rights  where item_key = :k order by seq;
select * from item_tenants where item_key = :k order by seq;
```

## 5. 미디어 URL 만드는 법
```python
media_url = f"{R2_PUBLIC_URL}/{row['r2_key']}"
# <img src=media_url>  또는 서류는 <a href=media_url>감정평가서</a>
```

## 6. 서버측(FastAPI + psycopg) 연동 예시 — `app/auction_source.py`
```python
import os, psycopg
from psycopg.rows import dict_row

DSN = os.environ["SUPABASE_DB_URL"]
R2 = os.environ["R2_PUBLIC_URL"].rstrip("/")

def _conn():
    return psycopg.connect(DSN, row_factory=dict_row, prepare_threshold=None)

def list_auctions(usage=None, group=None, limit=20, offset=0):
    sql = "select * from items where data_class='현황'"
    args = []
    if group: sql += " and search_group=%s"; args.append(group)
    if usage: sql += " and usage_name=%s"; args.append(usage)
    sql += " order by sell_date nulls last limit %s offset %s"; args += [limit, offset]
    with _conn() as c:
        return c.execute(sql, args).fetchall()

def get_auction(item_key):
    with _conn() as c:
        item = c.execute("select * from items where item_key=%s", (item_key,)).fetchone()
        if not item: return None
        sched = c.execute("select * from auction_schedule where item_key=%s order by id", (item_key,)).fetchall()
        media = c.execute("select kind, seq, r2_key, content_type from media where item_key=%s order by kind, seq", (item_key,)).fetchall()
        for m in media: m["url"] = f"{R2}/{m['r2_key']}"
        item["schedule"] = sched; item["media"] = media
        # 차량외 물건이면 차량 스펙도 함께(없으면 None)
        if item.get("search_group") == "차량외":
            item["vehicle"] = c.execute("select * from vehicle_specs where item_key=%s", (item_key,)).fetchone()
        return item
```
FastAPI 엔드포인트:
```python
from fastapi import APIRouter
from app import auction_source as A
router = APIRouter()

@router.get("/auctions")
def auctions(group: str = None, usage: str = None, limit: int = 20, offset: int = 0):
    return A.list_auctions(usage=usage, group=group, limit=limit, offset=offset)

@router.get("/auctions/{item_key:path}")
def auction(item_key: str):
    return A.get_auction(item_key) or {"error": "not found"}
```
> psycopg 미설치면: `pip install "psycopg[binary]"`

## 7. 프론트 직접 읽기 예시 (대안, supabase-js)
```js
import { createClient } from '@supabase/supabase-js'
const sb = createClient(SUPABASE_URL, SUPABASE_ANON_KEY)
const { data } = await sb.from('items').select('*').eq('data_class','현황').limit(20)
```
미디어: `${R2_PUBLIC_URL}/${row.r2_key}`

## 8. 자주 쓰는 쿼리
```sql
-- 지역+용도 필터 (현황)
select case_no, address, appraisal_price, min_price, sale_price, sell_date
from items where data_class='현황' and usage_name='아파트' and address like '%서울%'
order by sell_date;

-- 낙찰 통계 (백데이터)
select usage_name, count(*), avg(sale_price)
from items where data_class='백데이터' and sale_price is not null group by usage_name;
```

## 9. (프론트 직접 읽기 택할 때만) RLS 공개 읽기 정책
서버측(6번)으로 읽으면 **불필요**. 프론트에서 anon키로 직접 읽을 때만 Supabase SQL Editor에서 실행:
```sql
alter table items enable row level security;
create policy public_read_items on items for select to anon, authenticated using (true);
alter table auction_schedule enable row level security;
create policy public_read_schedule on auction_schedule for select to anon, authenticated using (true);
alter table media enable row level security;
create policy public_read_media on media for select to anon, authenticated using (true);
```
> `crawl_runs`, `status_history`(내부용)는 공개하지 말 것.

## 10. 참고
- `data_class='현황'` = 진행 중 물건(사진·서류 포함). `data_class='백데이터'` = 과거 매각분(미디어 없음, 낙찰가 있음).
- 데이터는 계속 수집/갱신 중 — 캐싱 시 적당한 TTL 권장.
- 미디어 누락 물건이 일부 있을 수 있음(수집 진행/예외분). `media` 행 유무로 분기.
