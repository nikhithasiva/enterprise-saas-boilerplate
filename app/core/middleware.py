from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware
from typing import Callable
import time
from collections import defaultdict
import structlog

logger = structlog.get_logger()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Simple in-memory rate limiting middleware.

    For production, consider using Redis-based rate limiting for distributed systems.
    This implementation uses a sliding window approach.
    """

    def __init__(self, app, calls: int = 100, period: int = 60):
        super().__init__(app)
        self.calls = calls  # Number of calls allowed
        self.period = period  # Time period in seconds
        self.clients = defaultdict(list)  # Store timestamps per client IP

    async def dispatch(self, request: Request, call_next: Callable):
        # Get client IP
        client_ip = request.client.host

        # Get current timestamp
        now = time.time()

        # Clean old timestamps outside the time window
        self.clients[client_ip] = [
            timestamp for timestamp in self.clients[client_ip]
            if now - timestamp < self.period
        ]

        # Check rate limit
        if len(self.clients[client_ip]) >= self.calls:
            logger.warning(
                "Rate limit exceeded",
                client_ip=client_ip,
                path=request.url.path
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Maximum {self.calls} requests per {self.period} seconds."
            )

        # Add current timestamp
        self.clients[client_ip].append(now)

        # Continue processing request
        response = await call_next(request)

        # Add rate limit headers
        response.headers["X-RateLimit-Limit"] = str(self.calls)
        response.headers["X-RateLimit-Remaining"] = str(
            self.calls - len(self.clients[client_ip])
        )
        response.headers["X-RateLimit-Reset"] = str(
            int(now + self.period)
        )

        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Add security headers to all responses for OWASP compliance.
    """

    async def dispatch(self, request: Request, call_next: Callable):
        response = await call_next(request)

        # OWASP recommended security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Content-Security-Policy"] = "default-src 'self'"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"

        return response
