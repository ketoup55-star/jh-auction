# 부동산 경매 정보 사이트 — 프로젝트 핸드오프

> 다른 프로젝트/팀이 콜드 스타트로 이해하도록 정리한 인수인계 문서.
> 작성 기준일: 2026-06. 경로: `C:\Users\red85\부동산경매`

---

## 1. 한 줄 소개

스피드옥션 같은 **유료 법원경매 정보 사이트**를 만드는 프로젝트.
대상 물건 5종 = 아파트 / 빌라(다세대·연립) / 도시형생활주택 / 상가주택 / 다가구주택.
차별점: 물건마다 **권리분석(말소기준·인수/소멸)과 예상 배당**을 자동 계산하고, 위험물건을 자동으로 걸러낸다.

---

## 2. 기술 스택

| 레이어 | 기술 |
|---|---|
| 언어 | Python 3.14 |
| 웹 프레임워크 | FastAPI 0.115 (ASGI) + uvicorn |
| 검증 | Pydantic v2 (FastAPI 내장) |
| 외부 API 호출 | httpx |
| DB | SQLite (표준 `sqlite3`, ORM 없음) |
| 인증/해싱 | 표준 `hashlib.pbkdf2` (외부 의존 없음) |
| 프런트엔드 | 순수 HTML/CSS/Vanilla JS (React/Vue/Next **미사용**) |
| 빌드 | 없음 (Node/npm 불필요) |

> 의존성: `pip install fastapi uvicorn httpx` 정도. 나머지는 표준 라이브러리.
> 기존 자매 프로젝트(빌라 실거래 지도, 호스텔 판단 지도)와 동일 계열 스택.

---

## 3. 디렉터리 구조

```
부동산경매/
├─ auction_analysis/          # 도메인 로직 패키지 (프런트/웹과 무관, 순수 Python)
│  ├─ models.py               # 데이터 모델: Right, Tenant, AuctionProperty, AnalysisResult
│  ├─ engine.py               # ★ 권리분석 엔진 (말소기준→인수/소멸→임차인 대항력)
│  ├─ distribution.py         # ★ 배당표 계산 (소액최우선·우선변제·안분 + 소액임차인 시행령표)
│  ├─ collection_policy.py    # 수집 제외 정책 (선순위 가등기 등 위험물건 차단)
│  ├─ codef_adapter.py        # CODEF 등기부/명세서 응답 → AuctionProperty 변환
│  ├─ listing.py              # 물건 카탈로그 모델 + 5종 주거 분류기
│  ├─ sources.py              # 데이터 소스 인터페이스(AuctionSource) + MockSource
│  ├─ store.py                # SQLite 카탈로그 저장소 + 검색 필터
│  ├─ ingest.py               # 적재 파이프라인 (분류→5종필터→정책→저장)
│  └─ auth.py                 # 회원 인증·세션·관심물건 저장소
├─ api/
│  ├─ main.py                 # FastAPI 앱 (모든 엔드포인트, 정적파일 서빙)
│  └─ serializers.py          # 도메인 객체 → JSON + 통계 집계
├─ static/                    # 프런트엔드 (Vanilla JS)
│  ├─ index.html              # 메인 홈 (간편검색, 로그인 상태)
│  ├─ search.html             # 종합검색 (필터 + 통계 + 결과목록 + 관심♡)
│  ├─ detail.html             # 물건 상세 (권리분석표 + 배당표 + 매각가 슬라이더 + 관심♡)
│  ├─ login.html              # 로그인/회원가입 (카카오 간편로그인 포함)
│  └─ mypage.html             # 마이페이지 (내 정보 + 관심물건)
├─ test_*.py                  # 검증 스위트 6종 (pytest 아님, 단독 실행)
├─ README.md                  # 사용/실행 안내
└─ HANDOFF.md                 # (이 문서)
```

`★` = 이 프로젝트의 핵심 가치. 나머지는 그걸 감싸는 인프라.

---

## 4. 실행 방법

```bash
pip install fastapi uvicorn httpx
cd 부동산경매
python -m uvicorn api.main:app --host 127.0.0.1 --port 4011
# 홈: http://127.0.0.1:4011/   문서: /docs
```
- 시작 시 `MockSource`(샘플 6건)를 SQLite(:memory:)에 적재 → 5종 4건만 남음(2건은 정책/유형으로 제외).
- 회원 계정은 `auth.db`(파일)에 영속 저장.
- 테스트: `python test_api.py` 등 각 파일 단독 실행 (전부 통과 상태).

---

## 5. 핵심 도메인 로직 (가장 중요)

### 5.1 권리분석 (`engine.py`)
입력: 등기 권리 목록 + 임차인 → 출력: 말소기준권리, 권리별 인수/소멸, 임차인 대항력, 위험도.
1. **말소기준권리** = (근)저당·압류·가압류·담보가등기·경매개시 중 가장 빠른 등기일
2. **인수/소멸**: 말소기준보다 후순위 → 소멸 / 선순위 용익·보전권리(지상권·가등기·가처분 등) → 인수
3. **임차인**: 대항력 발생일(전입 익일) ≤ 말소기준일 → 대항력 O. 대항력 + 배당요구 안 함 → 보증금 전액 낙찰자 인수.
4. 특수물건(가등기·가처분 등)은 `needs_expert_review` 플래그 + 경고. 자동판정 맹신 금지.

### 5.2 배당표 (`distribution.py`)
매각가를 법정 순위로 분배 → 대항력 임차인의 미배당 잔여 = 낙찰자 인수액.
- 1단계 소액임차인 최우선변제(매각가 1/2 한도). **소액 여부는 '최선순위 담보물권 설정일' 기준 지역별 시행령표**(`LAW_SMALL_TENANT`)로 판정 — 2023.02.21 현행 구간 검증 완료.
- 2단계 우선변제 순위배당(저당·전세·확정일자임차·조세, 기준일 순)
- 3단계 안분배당(가압류·일반채권)
- 미구현(주입 처리): 당해세 우선·임금채권 최우선·4대보험·안분후흡수.

### 5.3 수집 정책 (`collection_policy.py`)
위험 특수물건을 DB 적재 전에 제외. `EXCLUSION_RULES`에 규칙 추가식.
- 현재: **선순위 소유권이전청구권가등기 제외**(후순위·담보가등기는 수집).
- 예정: 유치권·법정지상권·지분경매.

---

## 6. 데이터 소스 전략 (반드시 숙지)

### 핵심 설계: 소스 교체식
`AuctionSource` 추상 인터페이스 → 현재 `MockSource`(샘플). **실제 소스만 끼우면 됨.**
적재 파이프라인(`ingest`)·중복제거(case_no upsert)·필터는 그대로 재사용.

### 데이터 출처 결정 (법적 검토 완료)
- ❌ **스피드옥션 등 사설 유료사이트 크롤링 금지** — DB제작자 권리 침해 + 약관위반 + (남의 계정 사용 시) 정보통신망법 형사 리스크 + 부정경쟁방지법.
- ⚠️ **법원경매 공식사이트(courtauction.go.kr) 직접 크롤링** — 공공데이터라 저작권 리스크는 낮으나, 민원처리법 시행령상 매크로 이용 제한(차단) + 캡차 우회 시 정보통신망법 리스크 + 화면 개편 시 깨짐(운영 불안정). 권장 안 함.
- ✅ **공식 Open API = CODEF(=쿠콘)** — 합법·안정. CODEF는 쿠콘의 개발자용 API 브랜드(같은 데이터). 신규/소규모는 CODEF 셀프 가입이 현실적.
- ✅ **온비드(공매) OpenAPI** — 공공데이터포털 무료. 공매 데이터 보완용.

### 비용 통제: 하이브리드 온디맨드
전국을 미리 다 사두지 말고, **목록은 저가/무료 소스, 상세·권리분석은 사용자 클릭 시에만 CODEF 호출 + 결과 캐싱**.
→ 초기 비용 거의 0, 사용량 비례. (풀 프리로드 대비 수십 배 저렴)

> 현재 CODEF 정식키 미발급(데모키는 실데이터 불가). 쿠콘 요금 견적 대기 중.

---

## 7. API 엔드포인트 (`api/main.py`)

| 메서드·경로 | 설명 | 인증 |
|---|---|---|
| `GET /` | 메인 홈 HTML | - |
| `GET /api` | 서비스 정보(JSON) | - |
| `GET /properties` | 목록/검색 (type[]·region·court·keyword·auction_type·가격·감정가·면적·유찰·매각기일·sort) + 통계 | - |
| `GET /properties/{case_no}` | 상세 + 권리분석 + 배당(최저가 가정) | - |
| `GET /properties/{case_no}/distribution?sale_price=` | 매각가별 배당 재계산(슬라이더) | - |
| `POST /analyze` | 임의 등기/임차 입력 → 권리분석(+배당) | - |
| `POST /auth/signup` `/auth/login` `/auth/logout`, `GET /auth/me` | 이메일 회원/세션(httponly 쿠키 `sid`) | - |
| `GET /auth/kakao/login` `/auth/kakao/callback` | 카카오 간편로그인(OAuth2) | - |
| `GET/POST/DELETE /favorites[/{case_no}]`, `GET /favorites/{case_no}/status` | 관심물건 | 로그인 |

---

## 8. 데이터 모델 요약

- `Right`: type(RightType enum), reg_date, holder, amount, note — 등기 권리 1건
- `Tenant`: name, move_in_date, fixed_date, deposit, demanded_distribution — 임차인
- `AuctionProperty`: case_no + rights[] + tenants[] + 메타 — 권리분석 입력
- `Listing`: 카탈로그(court, residential_type, region, 감정가/최저가, 면적, 유찰, sale_date, status, auction_type, view_count, lat/lng) + rights/tenants — 검색/저장 단위
- `Region` enum(소액임차인 4구분), `ResidentialType` enum(주거 5종)
- 회원: users(email, password(pbkdf2), name, grade, provider/provider_id), sessions, favorites

---

## 9. 인증

- 이메일/비번(pbkdf2) + 세션(httponly 쿠키 `sid`, SQLite sessions 테이블).
- 카카오 OAuth2 간편로그인 구현됨. **실작동에 `KAKAO_REST_KEY` 환경변수 필요**(개발자센터 발급 + Redirect URI 등록). 키 없으면 안내 메시지.
- 소셜 회원은 provider/provider_id로 식별, 이메일/비번 로그인 불가.
- 등급 필드(무료/유료)만 존재 — **유료 기능 게이팅·결제는 미구현**.

---

## 10. 현재 상태 / 남은 일

### 완료
- 권리분석 엔진, 배당표 계산, 수집정책, CODEF 어댑터(키 보정만 남음)
- 적재 파이프라인 + 5종 필터 + SQLite 검색
- FastAPI REST API + 통계
- 프런트: 홈·종합검색·상세(매각가 슬라이더)·로그인·마이페이지
- 회원/세션/관심물건 + 카카오 로그인(키 대기)
- 테스트 6종 전부 통과, 브라우저 검증 완료

### 남은 일 (우선순위)
1. 🔴 **CODEF 정식키 발급** → `CodefSource` 구현(하이브리드 온디맨드 + 캐싱) → `MockSource` 교체
2. 카카오 `KAKAO_REST_KEY` 발급 → 실제 로그인 검증
3. CODEF 등기부/명세서 실제 응답 샘플로 `codef_adapter` 키 확정
4. 지도 화면(Kakao 지도 + V-World 지오코더로 주소→좌표)
5. 온비드(공매) 무료 API 연동
6. 배당 특수순위(당해세·임금채권), 유료 결제/게이팅
7. `LAW_SMALL_TENANT` 과거 구간(2021·2018) 값 재확인

---

## 11. 중요 제약·결정 (놓치면 안 됨)

- **데이터는 원천(CODEF/공식 API)에서만.** 사설 사이트 크롤링 금지(법적 리스크).
- **권리분석 자동판정은 일반물건 한정.** 특수물건은 경고 + 전문가 검토. 면책 고지 필수.
- **소액임차인 시행령표는 법 개정 시 갱신 필요**(법적 책임). 현행만 검증됨.
- `uvicorn --reload`는 CWD만 감시 → `--app-dir`로 다른 폴더 코드 수정 시 **수동 재시작** 필요.
- 카탈로그 DB는 `:memory:`(재시작 시 재적재), 회원 DB는 파일(`auth.db`) 영속.
