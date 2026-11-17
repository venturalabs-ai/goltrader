"""Retry com backoff exponencial + jitter."""
from __future__ import annotations

import random
import time
from typing import Callable, Iterator, Optional, Type, TypeVar

T = TypeVar("T")


def backoff_delays(attempts: int, base: float = 0.5, factor: float = 2.0, max_delay: float = 30.0,
                   jitter: float = 0.25, rng: Callable[[], float] = random.random) -> Iterator[float]:
    for i in range(attempts):
        d = min(max_delay, base * (factor ** i))
        yield d * (1.0 - jitter + jitter * 2 * rng()) if jitter else d


def retry_call(fn: Callable[[], T], retries: int, is_retryable: Callable[[BaseException], bool],
               base: float = 0.5, max_delay: float = 30.0, sleep: Callable[[float], None] = time.sleep,
               rng: Callable[[], float] = random.random,
               on_retry: Optional[Callable[[int, BaseException, float], None]] = None) -> T:
    delays = list(backoff_delays(retries, base=base, max_delay=max_delay, rng=rng))
    attempt = 0
    while True:
        try:
            return fn()
        except BaseException as exc:  # noqa: BLE001
            if attempt >= retries or not is_retryable(exc):
                raise
            d = delays[attempt]
            if on_retry:
                on_retry(attempt + 1, exc, d)
            sleep(d)
            attempt += 1
