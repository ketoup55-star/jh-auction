#!/usr/bin/env bash
# 고코어 VM에서 진행물건 우선 예열 — N개 샤드 병렬 + 2패스(멱등 재실행으로 실패/타임아웃분 보강).
#
# 사용:
#   ./run_prewarm.sh              # N = 코어 수, 전체(all)
#   ./run_prewarm.sh 16           # 16 샤드, 전체
#   ./run_prewarm.sh 16 expbid    # 16 샤드, 예상낙찰가만(순수계산·가장 빠름)
#   ./run_prewarm.sh 12 brief,docs
#
# TYPES: all | brief | sise | expbid | docs (쉼표구분)
set -u
N="${1:-$(nproc)}"
TYPES="${2:-all}"
export DISABLE_LOCAL_CACHE=1 DISABLE_PREWARM=1 PYTHONIOENCODING=utf-8

mkdir -p prewarm_logs
echo "샤드 수 N=$N · types=$TYPES · 코어=$(nproc 2>/dev/null || echo '?')"

run_pass() {
  local pass=$1
  echo "===== PASS $pass 시작 ($(date '+%H:%M:%S')) ====="
  local pids=()
  for i in $(seq 0 $((N - 1))); do
    python prewarm_shard.py "$i" "$N" "$TYPES" > "prewarm_logs/shard_${i}_p${pass}.log" 2>&1 &
    pids+=($!)
  done
  for p in "${pids[@]}"; do wait "$p"; done
  echo "===== PASS $pass 완료 ($(date '+%H:%M:%S')) ====="
  # 각 샤드 마지막 줄 요약
  for i in $(seq 0 $((N - 1))); do tail -n 1 "prewarm_logs/shard_${i}_p${pass}.log"; done
}

run_pass 1
run_pass 2   # 멱등 재실행: 1패스 실패/타임아웃분만 다시(대부분 캐시라 빠름)
echo "전체 완료. 로그: prewarm_logs/  (실시간 모니터: tail -f prewarm_logs/shard_0_p1.log)"
