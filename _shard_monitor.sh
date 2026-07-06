cd "C:/Users/red85/부동산경매"
rows(){ python -c "import sqlite3; print(sqlite3.connect('file:cache_$1.db?mode=ro',uri=True).execute('SELECT COUNT(*) FROM kv').fetchone()[0])" 2>/dev/null; }
prev=0; flat=0
echo "=== 샤드 백로그 모니터 시작(75초 간격) ==="
for i in $(seq 1 60); do
  a=$(rows appraisal); s=$(rows summary); an=$(rows analysis)
  d=$((a-prev))
  echo "[poll $i] appraisal=$a (+$d)  summary=$s  analysis=$an"
  if [ "$i" -gt 5 ] && [ "$d" -le 0 ]; then flat=$((flat+1)); else flat=0; fi
  if [ "$flat" -ge 3 ]; then echo "=== appraisal 증가 3회 연속 멈춤 → 백로그 완료 추정 ==="; break; fi
  prev=$a
  sleep 75
done
echo "=== 최종: appraisal=$(rows appraisal)  summary=$(rows summary)  analysis=$(rows analysis) ==="
