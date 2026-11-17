"""Limitadores de taxa (token bucket e janela deslizante). Relógio/sleep injetáveis para teste."""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable


class TokenBucket:
    def __init__(self, rate_per_sec: float, burst: int, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        if rate_per_sec <= 0 or burst < 1:
            raise ValueError("rate_per_sec>0 e burst>=1")
        self.rate, self.burst = float(rate_per_sec), float(burst)
        self._clock, self._sleep = clock, sleep
        self._tokens, self._last = float(burst), clock()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(self.burst, self._tokens + (now - self._last) * self.rate)
        self._last = now

    def acquire(self, n: float = 1.0) -> float:
        """Bloqueia até haver `n` tokens. Retorna quanto esperou (s)."""
        waited = 0.0
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= n:
                    self._tokens -= n
                    return waited
                need = (n - self._tokens) / self.rate
            self._sleep(need)
            waited += need

    def penalize(self, seconds: float) -> None:
        """Esvazia o balde e atrasa a reposição (ex.: servidor mandou esperar)."""
        with self._lock:
            self._tokens = 0.0
            self._last = self._clock() + max(0.0, seconds)


class SlidingWindow:
    """Conta eventos nos últimos `window` segundos (ex.: ordens por minuto)."""

    def __init__(self, limit: int, window: float = 60.0, clock: Callable[[], float] = time.monotonic):
        self.limit, self.window, self._clock = int(limit), float(window), clock
        self._events: deque = deque()

    def _trim(self) -> None:
        cutoff = self._clock() - self.window
        while self._events and self._events[0] <= cutoff:
            self._events.popleft()

    def count(self) -> int:
        self._trim()
        return len(self._events)

    def allow(self) -> bool:
        return self.count() < self.limit

    def record(self) -> None:
        self._events.append(self._clock())
