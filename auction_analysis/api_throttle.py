"""data.go.kr 계열 OpenAPI 호출 속도 제한(프로세스 전역 토큰버킷).

2026-09-17 실측: 세대수 재계산을 6스레드로 돌리면 K-apt 단지목록(getSigunguAptList4)이 HTTP 429(초당 요청 제한)를
돌려주고, 재시도 없이 None → 표제부 폴백으로 흘러 K-apt 1,060세대가 총괄표제부 884로 바뀌는 식의 '조용한 품질 저하'가
생겼다. 모든 data.go.kr 호출(K-apt 목록/기본/상세, 건축물대장 표제부/총괄) 직전에 throttle()을 부른다.
"""
import threading
import time

_RATE = 5.0          # 초당 허용 호출 수(전역)
_BURST = 5.0
_lock = threading.Lock()
_tokens = _BURST
_last = time.monotonic()


def throttle() -> None:
    """토큰이 생길 때까지 잠깐 대기(초당 _RATE개)."""
    global _tokens, _last
    while True:
        with _lock:
            now = time.monotonic()
            _tokens = min(_BURST, _tokens + (now - _last) * _RATE)
            _last = now
            if _tokens >= 1.0:
                _tokens -= 1.0
                return
            wait = (1.0 - _tokens) / _RATE
        time.sleep(wait)
