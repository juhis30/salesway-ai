"""Rate limits counted in the database, so every server process shares them and restarts don't reset them.

Fixed windows: at most `limit` requests per key in each window (a minute, an hour). One upsert per
check. At very high traffic, the same interface can move to Redis.
"""

from datetime import datetime, timezone

from fastapi import HTTPException, Request, status
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from app.models import RateLimitCounter


class RateLimiter:
    def __init__(self, name: str, limit: int, window_seconds: int = 60, message: str = "Too many requests, please wait a minute"):
        self.name = name
        self.limit = limit
        self.window = window_seconds
        self.message = message

    def check(self, db: Session, key: str) -> None:
        """Count one request for `key`; refuse it with 429 if the window's limit is used up.

        Commits, so the count sticks even if the request fails later. Call it before other work.
        """
        now = datetime.now(timezone.utc).timestamp()
        window_start = datetime.fromtimestamp(now - now % self.window, timezone.utc)
        dialect = postgresql if db.get_bind().dialect.name == "postgresql" else sqlite
        insert = dialect.insert(RateLimitCounter).values(key=f"{self.name}:{key}"[:200], window_start=window_start, count=1)
        count = db.execute(
            insert.on_conflict_do_update(index_elements=["key", "window_start"], set_={"count": RateLimitCounter.count + 1})
            .returning(RateLimitCounter.count)
        ).scalar_one()
        db.commit()
        if count > self.limit:
            retry_after = int(self.window - now % self.window) + 1
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, self.message, headers={"Retry-After": str(retry_after)})


def client_ip(request: Request) -> str:
    """The caller's address. Behind Render's proxy this is right only because uvicorn runs with
    --proxy-headers --forwarded-allow-ips='*' (render.yaml), which takes it from X-Forwarded-For."""
    return request.client.host if request.client else "unknown"
