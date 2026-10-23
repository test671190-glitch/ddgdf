"""Ограничение частоты по ключу (IP-адрес, пользователь): ведро токенов в памяти."""

import ipaddress
import time

# Больше записей не держим: при атаке с огромного числа адресов словарь не съест память
MAX_TRACKED = 50_000


def ip_key(ip: str) -> str:
    """IPv6 считаем по сети /64: у одного клиента их миллиарды адресов, и по отдельному
    адресу он обходил бы лимит, просто меняя адрес."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if addr.version == 6:
        return str(ipaddress.ip_network(f"{addr}/64", strict=False))
    return ip


class RateLimiter:
    """Не больше rate событий в секунду на ключ, кратковременно — до burst подряд."""

    def __init__(self, rate: float, burst: float | None = None) -> None:
        self.rate = rate
        self.burst = float(burst if burst is not None else rate)
        self._buckets: dict[str, tuple[float, float]] = {}  # ключ -> (токены, время)
        self._cleaned = time.monotonic()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        tokens, last = self._buckets.get(key, (self.burst, now))
        tokens = min(self.burst, tokens + (now - last) * self.rate)
        allowed = tokens >= 1
        self._buckets[key] = (tokens - 1 if allowed else tokens, now)
        if now - self._cleaned > 60 or len(self._buckets) > MAX_TRACKED:
            self._cleanup(now)
        return allowed

    def _cleanup(self, now: float) -> None:
        # ведро, которое успело наполниться, ничем не отличается от отсутствующего
        full_after = self.burst / self.rate
        self._buckets = {k: v for k, v in self._buckets.items() if now - v[1] < full_after}
        if len(self._buckets) > MAX_TRACKED:
            # атака с огромного числа ключей: лучше на миг забыть всех, чем упасть по памяти
            self._buckets.clear()
        self._cleaned = now
