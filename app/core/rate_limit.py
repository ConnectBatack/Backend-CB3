import asyncio
import ipaddress
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status

from app.core.config import get_settings


class SlidingWindowRateLimiter:
    """Limiteur de requêtes par fenêtre glissante avec délai progressif (Redis ou mémoire)."""

    def __init__(self) -> None:
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
        self._failures: dict[str, int] = defaultdict(int)
        self._redis = None
        self._redis_checked = False

    async def _get_redis(self):
        if self._redis_checked:
            return self._redis
        self._redis_checked = True
        settings = get_settings()
        if not settings.REDIS_URL or settings.APP_ENV == "test":
            return None
        try:
            import redis.asyncio as aioredis

            client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
            await client.ping()
            self._redis = client
        except Exception:
            self._redis = None
        return self._redis

    async def check(self, key: str, max_requests: int, window_seconds: int) -> None:
        now = time.time()
        redis_client = await self._get_redis()
        if redis_client is not None:
            try:
                pipe = redis_client.pipeline()
                redis_key = f"rl:{key}"
                pipe.zremrangebyscore(redis_key, 0, now - window_seconds)
                pipe.zadd(redis_key, {f"{now}:{id(pipe)}": now})
                pipe.zcard(redis_key)
                pipe.expire(redis_key, window_seconds + 1)
                _, _, count, _ = await pipe.execute()
                if count > max_requests:
                    raise HTTPException(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        detail="Trop de tentatives. Veuillez réessayer dans quelques minutes.",
                    )
                return
            except HTTPException:
                raise
            except Exception:
                pass

        bucket = self._buckets[key]
        cutoff = now - window_seconds
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= max_requests:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Trop de tentatives. Veuillez réessayer dans quelques minutes.",
            )
        bucket.append(now)

    async def record_login_failure_backoff(self, ip: str) -> None:
        """Applique un délai progressif par IP sans verrouiller le compte de l'utilisateur."""
        settings = get_settings()
        if settings.APP_ENV == "test":
            return
        self._failures[ip] += 1
        attempts = self._failures[ip]
        if attempts > 1:
            delay = min(4.0, 0.25 * (2 ** min(attempts - 2, 4)))
            await asyncio.sleep(delay)

    def reset_ip_failures(self, ip: str) -> None:
        self._failures.pop(ip, None)

    def reset(self) -> None:
        self._buckets.clear()
        self._failures.clear()


rate_limiter = SlidingWindowRateLimiter()


def _is_trusted_proxy(direct_ip: str, trusted: set[str]) -> bool:
    if direct_ip in trusted:
        return True
    try:
        addr = ipaddress.ip_address(direct_ip)
    except ValueError:
        return False
    for entry in trusted:
        if "/" not in entry:
            continue
        try:
            if addr in ipaddress.ip_network(entry, strict=False):
                return True
        except ValueError:
            continue
    return False


def client_ip(request: Request) -> str:
    """
    Extrait l'IP du client.
    N'accepte X-Forwarded-For que si TRUST_PROXY_HEADERS=true et que l'émetteur
    direct est dans TRUSTED_PROXIES (IP exacte ou sous-réseau CIDR).
    """
    settings = get_settings()
    direct_ip = request.client.host if request.client else "unknown"
    if not settings.TRUST_PROXY_HEADERS:
        return direct_ip
    trusted = settings.trusted_proxies_set
    if trusted and not _is_trusted_proxy(direct_ip, trusted):
        return direct_ip
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return direct_ip
