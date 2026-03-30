"""API middleware: rate limiting, request logging."""

import time

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from sportshub.cache.client import RedisClient

logger = structlog.get_logger()

RATE_LIMIT_RPM = 60  # requests per minute
RATE_LIMIT_BURST = 10


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every request with method, path, status, and duration."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start = time.monotonic()
        response = await call_next(request)
        duration_ms = (time.monotonic() - start) * 1000

        logger.info(
            "http_request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=round(duration_ms, 1),
        )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple per-API-key rate limiting using Redis."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Skip rate limiting for health endpoint
        if request.url.path.endswith("/health"):
            return await call_next(request)

        api_key = request.headers.get("X-API-Key", "anonymous")
        redis: RedisClient = getattr(request.app.state, "redis", None)

        remaining = RATE_LIMIT_RPM
        if redis:
            cache_key = f"ratelimit:{api_key}"
            count = await redis.increment(cache_key, ttl=60)
            remaining = max(0, RATE_LIMIT_RPM - count)

            if count > RATE_LIMIT_RPM + RATE_LIMIT_BURST:
                return Response(
                    content='{"error":{"code":"RATE_LIMITED","message":"Too many requests"}}',
                    status_code=429,
                    media_type="application/json",
                    headers={
                        "X-RateLimit-Limit": str(RATE_LIMIT_RPM),
                        "X-RateLimit-Remaining": "0",
                        "Retry-After": "60",
                    },
                )

        response = await call_next(request)

        # Add rate limit headers
        response.headers["X-RateLimit-Limit"] = str(RATE_LIMIT_RPM)
        response.headers["X-RateLimit-Remaining"] = str(remaining)

        return response
