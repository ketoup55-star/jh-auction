# 진행물건 우선 예열 — 고코어 VM 실행 가이드

목적: 진행물건(신건·유찰·재진행·재매각, 약 **2.6만 건**)의 brief(여관/생숙·준공/세대)·시세·예상낙찰가·docs를
멀티프로세스로 빠르게 계산해 Supabase에 채운다. 클라우드 앱은 `CLOUD_READER=1`로 이걸 읽기만 한다.

---

## 1. VM 준비
- **사양**: Linux(Ubuntu 22.04+) **16~32 vCPU**, RAM 8~16GB, 디스크 20GB. (시간당 과금, 몇 시간 쓰고 삭제)
- 제공사 예: AWS EC2 `c7i.8xlarge`(32vCPU), GCP `c3-highcpu-16`, Vultr/Hetzner High-CPU 등 아무거나.
- **Supabase Pro 권장**(쓰기 처리량·statement timeout 여유). 무료면 동시 쓰기에서 느려질 수 있음.

## 2. 세팅 (VM 접속 후)
```bash
sudo apt update && sudo apt install -y python3 python3-pip git
git clone https://github.com/ketoup55-star/jh-auction.git
cd jh-auction
pip3 install -r requirements.txt

# .env 생성(로컬 .env 내용 그대로 복사) — 반드시 아래 키 포함:
#   SUPABASE_URL, SUPABASE_KEY(또는 ANON), SUPABASE_DB_URL(pooler), SUPABASE_SERVICE_KEY
#   ONBID_SERVICE_KEY(=data.go.kr), VWORLD_KEY, VWORLD_DOMAIN, KAKAO_REST_KEY, TMAP_APP_KEY
nano .env
```

## 3. 실행
```bash
chmod +x run_prewarm.sh

# (권장) 먼저 예상낙찰가만 — 순수계산이라 API 천장 없이 가장 빠름, 차익이 바로 채워짐
./run_prewarm.sh 16 expbid

# 그다음 나머지 전체(brief·시세·docs 포함). N=코어수.
./run_prewarm.sh 16 all
# 또는 나눠서:  ./run_prewarm.sh 16 brief    /    ./run_prewarm.sh 16 sise    /    ./run_prewarm.sh 16 docs
```
- `N`(첫 인자)=동시 프로세스 수. 코어 수와 같게. **API가 throttle(429) 나면 N을 줄인다.**
- 스크립트는 **2패스**(멱등 재실행)로 1패스 실패·타임아웃분까지 보강한다.

## 4. 모니터링
```bash
tail -f prewarm_logs/shard_0_p1.log          # 진행률·ETA·건/s
grep -h DONE prewarm_logs/*_p2.log           # 각 샤드 완료 확인
```

## 5. 끝나면
- **로그에 DONE 다 뜨면 완료.** VM은 **삭제**(과금 중단).
- 데이터는 Supabase에 있으므로 VM은 버려도 됨. 클라우드 앱이 즉시 읽음.

---

## 참고
- **멱등**: 이미 캐시된 건 건너뜀 → 중간에 끊겨도 다시 돌리면 이어서.
- **쓰기**: `DISABLE_LOCAL_CACHE=1`(런처가 자동 설정)로 로컬버퍼 없이 Supabase 직접 저장 → VM 삭제해도 유실 없음.
- **천장**: data.go.kr 일 100만 쿼터는 넉넉(2.6만×수콜 ≪ 100만). 유일 변수는 초당 rate → N으로 조절.
- **소요(대략)**: expbid N=16 ≈ 15~20분 / all N=16 ≈ 2~3시간(코어 2배면 절반).
