import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status

from app.core.config import get_settings


class SlidingWindowRateLimiter:
    """Limiteur de requêtes par fenêtre glissante (Redis si disponible, sinon mémoire locale)."""

    def __init__(self) -> None:
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
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
                # Repli silencieux sur le stockage en mémoire locale si Redis est indisponible
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

    def reset(self) -> None:
        self._buckets.clear()


rate_limiter = SlidingWindowRateLimiter()


def client_ip(request: Request) -> str:
    """Extrait l'adresse IP du client (avec prise en charge X-Forwarded-For)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

