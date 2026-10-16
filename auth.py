"""
Owner login for the website.

Who can do what:
- Owner   : everything (generate, edit, approve, publish).
- Visitor : with PUBLIC_DEMO=true, can look at results and use a limited demo chat
            (questions only, no actions). With PUBLIC_DEMO=false, sees nothing.

How you become the owner:
- Running locally (HOST=127.0.0.1) with no ADMIN_PASSWORD -> automatically,
  because nobody else can reach the website.
- Otherwise -> log in with ADMIN_PASSWORD. You get a signed cookie for 7 days.
"""
import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

import config

COOKIE = "seo_owner"
SESSION_SECONDS = 7 * 24 * 3600

# The cookie is signed with this key. Including the password means that changing
# ADMIN_PASSWORD logs everyone out.
_key = ((config.SESSION_SECRET or secrets.token_hex(32)) + config.ADMIN_PASSWORD).encode()


def _sign(text: str) -> str:
    return hmac.new(_key, text.encode(), hashlib.sha256).hexdigest()


def make_session_token() -> str:
    expires = str(int(time.time()) + SESSION_SECONDS)
    return f"{expires}.{_sign(expires)}"


def _token_is_valid(token: str) -> bool:
    expires, _, signature = token.partition(".")
    if not expires.isdigit() or not hmac.compare_digest(signature, _sign(expires)):
        return False
    return int(expires) > time.time()


def check_password(password: str) -> bool:
    return bool(config.ADMIN_PASSWORD) and hmac.compare_digest(
        password.encode(), config.ADMIN_PASSWORD.encode()
    )


def is_owner(request: Request) -> bool:
    if config.DEMO_ONLY:
        return False  # hosted demo (e.g. Vercel): read-only for everyone
    if config.IS_LOCAL and not config.ADMIN_PASSWORD:
        return True
    if not config.ADMIN_PASSWORD:
        return False  # online without a password = pure demo, nobody can change anything
    return _token_is_valid(request.cookies.get(COOKIE, ""))


def can_view(request: Request) -> bool:
    return config.PUBLIC_DEMO or is_owner(request)


# ---------- FastAPI dependencies ----------
def require_owner(request: Request) -> None:
    if not is_owner(request):
        raise HTTPException(401, "This is a read-only demo. Only the owner can do this - please log in.")


def require_viewer(request: Request) -> None:
    if not can_view(request):
        raise HTTPException(401, "Please log in to see this.")


# ---------- simple rate limits (in memory) ----------
class RateLimiter:
    """Allow at most `max_hits` per `seconds` for each key (e.g. an IP address)."""

    def __init__(self, max_hits: int, seconds: int):
        self.max_hits, self.seconds = max_hits, seconds
        self.hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.time()
        q = self.hits[key]
        while q and q[0] <= now - self.seconds:
            q.popleft()
        if len(q) >= self.max_hits:
            return False
        q.append(now)
        return True


login_attempts = RateLimiter(5, 15 * 60)       # 5 login tries per 15 minutes per IP
visitor_chat = RateLimiter(10, 3600)           # 10 demo chat messages per hour per IP
visitor_chat_total = RateLimiter(100, 3600)    # 100 demo chat messages per hour in total
public_audit = RateLimiter(5, 3600)            # 5 free store audits per hour per IP
public_audit_total = RateLimiter(40, 3600)     # 40 free store audits per hour in total


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"
