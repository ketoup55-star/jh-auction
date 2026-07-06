# 부동산 경매 권리분석 엔진

법원경매 물건의 **등기부 권리 + 임차인 정보**를 입력받아
`말소기준권리 → 인수/소멸 → 임차인 대항력/배당`을 자동 판정한다.

> 데이터는 **원천(법원경매정보 / CODEF 등기부)** 에서 합법적으로 확보한다.
> 타 유료사이트의 가공 데이터를 복제하지 않는다.

## 구조
```
부동산경매/
├─ auction_analysis/
│  ├─ __init__.py            # 패키지 진입점 (analyze, 모델 export)
│  ├─ models.py              # 입출력 데이터 모델
│  ├─ engine.py              # 핵심 분석 로직 (말소기준·인수/소멸·대항력)
│  ├─ codef_adapter.py       # CODEF 등기부 응답 → AuctionProperty 정규화
│  ├─ collection_policy.py   # 수집 제외 정책 (선순위 가등기 등 특수물건 차단)
│  ├─ distribution.py        # 배당표 정밀 계산 (소액최우선·우선변제·안분)
│  ├─ listing.py             # 물건 카탈로그 모델 + 5종 주거 분류기
│  ├─ sources.py             # 데이터 소스 인터페이스 + MockSource(→ CODEF/법원 교체)
│  ├─ store.py               # SQLite 카탈로그 저장소 (검색 필터)
│  ├─ ingest.py              # 적재 파이프라인 (분류→5종필터→정책→저장)
│  ├─ auth.py                # 회원 인증·세션·관심물건 저장소 (pbkdf2, 의존성 無)
│  └─ supabase_source.py     # 스피드옥션 제휴 데이터(Supabase+R2) 연동 + 개인정보 마스킹 토글
├─ api/
│  ├─ main.py                # FastAPI 앱 (목록/검색/상세/즉석분석/배당재계산)
│  └─ serializers.py         # 분석·배당·물건 → JSON
├─ static/
│  ├─ index.html             # 메인 홈 (간편검색 → 검색페이지, 로그인상태 반영)
│  ├─ search.html            # 종합검색 화면 (필터 폼 + 통계 + 결과목록 + 관심♡)
│  ├─ detail.html            # 물건 상세 화면 (권리분석표+배당표+슬라이더+관심♡)
│  ├─ login.html             # 로그인 · 회원가입
│  ├─ mypage.html            # 마이페이지 (내 정보 + 관심물건 목록)
│  ├─ auctions.html          # ★ 실데이터 경매물건 검색·목록 (Supabase 제휴데이터)
│  └─ auction.html           # ★ 실데이터 물건 상세 (사진·정보·기일내역·서류)
├─ run_example.py            # 권리분석 3시나리오 예제
├─ test_codef_adapter.py     # CODEF 어댑터 검증
├─ test_collection_policy.py # 수집 정책 검증
├─ test_distribution.py      # 배당표 계산 검증
├─ test_ingest.py            # 적재 파이프라인 검증
├─ test_api.py               # REST API 검증
└─ README.md
```

## 실행
```powershell
python run_example.py
```

## 분석 로직 요약
1. **말소기준권리** = (근)저당·압류·가압류·담보가등기·경매개시 중 가장 빠른 등기일
   (전세권은 '건물 전부 + 배당요구/경매신청' 조건 만족 시 후보)
2. **인수/소멸**
   - 말소기준보다 후순위 권리 → **소멸** (단, 건물철거 가처분 등 예외)
   - 말소기준보다 선순위 용익/보전권리(지상권·가등기·가처분 등) → **인수**
3. **임차인**
   - 대항력 발생일(전입 익일) ≤ 말소기준일 → **대항력 O**
   - 대항력 O + 배당요구 안 함 → 보증금 전액 낙찰자 **인수**

## 한계 (정직한 고지)
- 자동 판정은 **일반물건 기준**. 가등기·가처분·선순위 전세권·지분경매·유치권·
  법정지상권 등 **특수물건은 `needs_expert_review` 플래그로 표시**하고 경고만 남긴다.
- 정확한 **배당표 계산**(확정일자 순위·소액임차인 최우선변제 등)은 다음 단계에서 구현.

## 수집 정책 (collection_policy.py)
위험한 특수물건을 DB 적재 전에 제외한다. 적재 파이프라인에서
`is_collectible(prop)` / `filter_collectible(props)` 로 거른다.
- [x] **선순위 소유권이전청구권가등기 → 수집 제외** (낙찰자 소유권 상실 위험)
- [ ] (예정) 유치권 신고 / 법정지상권 성립여지 / 지분경매 → 같은 틀에 추가

## 배당표 계산 (distribution.py)
매각가를 법정 배당순위로 분배하고 대항력 임차인의 미배당 잔여 → 낙찰자 인수액을 산출.
- 1단계 소액임차인 최우선변제 (매각가 1/2 한도, 부족 시 안분)
  - 소액 여부는 **'최선순위 담보물권 설정일' 기준 지역별 시행령 표**로 판정
- 2단계 우선변제 순위배당 (저당·전세·확정일자임차·조세를 기준일 순서로)
- 3단계 안분배당 (가압류·일반채권 비례)
- ⚠️ `LAW_SMALL_TENANT` 표는 주택임대차보호법 시행령 기준. **법제처 현행과 대조·유지보수 필수.**
- 미구현(주입으로 처리): 당해세 우선, 임금채권 최우선, 4대보험, 안분후흡수.

## 실행 (API 서버)
```powershell
uvicorn api.main:app --reload --port 4000
# 문서: http://127.0.0.1:4000/docs
```
| 엔드포인트 | 설명 |
|---|---|
| `GET /properties` | 목록/검색 (type·region·court·가격 필터) |
| `GET /properties/{case_no}` | 상세 + 권리분석 + 배당(최저가 가정) |
| `GET /properties/{case_no}/distribution?sale_price=` | 매각가별 배당 재계산(슬라이더용) |
| `POST /analyze` | 임의 등기/임차 입력 → 권리분석(+배당) |
| `POST /auth/signup` `/auth/login` `/auth/logout` `GET /auth/me` | 회원/세션(httponly 쿠키) |
| `GET /auth/kakao/login` `/auth/kakao/callback` | 카카오 간편로그인(OAuth2) |
| `GET/POST/DELETE /favorites[/{case_no}]` | 관심물건 (로그인 필요) |

### 카카오 로그인 설정 (실제 작동에 필요)
1. [카카오 개발자센터](https://developers.kakao.com) 앱 생성 → REST API 키 발급
2. 카카오 로그인 활성화 + Redirect URI 등록: `http://localhost:4011/auth/kakao/callback`
3. 동의항목: 닉네임(profile_nickname), 이메일(account_email)
4. 환경변수 설정 후 서버 실행:
   `KAKAO_REST_KEY=발급키` (선택: `KAKAO_REDIRECT_URI`)
   - 키 미설정 시 카카오 버튼 클릭하면 안내 메시지 표시(앱은 정상 동작)

메인 홈: `http://127.0.0.1:4000/`  (간편검색 → 검색페이지)
종합검색: `http://127.0.0.1:4000/static/search.html`
상세 화면: `http://127.0.0.1:4000/static/detail.html?case_no=2026타경1002`

검색 필터: 법원·소재지/명칭·경매종류·매각기일·현황용도(주거5종)·감정가·최저가·건물면적·유찰수·정렬

## 다음 단계 (TODO)
- [x] CODEF 등기부 응답 → `AuctionProperty` 정규화 어댑터 (실제 키 보정만 남음)
- [x] 배당표 정밀 계산 (소액임차인 최우선변제 + 확정일자 우선변제 순위)
- [x] 물건 데이터 적재 파이프라인 + 5종 주거 필터 + 검색 (MockSource 기반)
- [x] FastAPI REST 엔드포인트 (목록/검색/상세/즉석분석)
- [ ] `LAW_SMALL_TENANT` 과거 구간(2021·2018) 값 재확인 (현행은 검증 완료)
- [ ] CODEF 요약 응답 실제 샘플로 `codef_summary_to_entries` 키 확정 + 정식키
- [ ] `MockSource` → 실제 소스(법원경매정보/CODEF) 구현 교체
- [ ] 당해세/임금채권 등 특수 우선순위 정식 반영
- [ ] 프론트엔드 + Kakao 지도 연동 (기존 자산 재활용)
