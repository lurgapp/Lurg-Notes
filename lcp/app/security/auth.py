import time
from dataclasses import dataclass
from uuid import UUID

import httpx
import jwt

from ..config import Settings
from ..protocol.errors import LCPError


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: str | None
    kind: str
    security_version: int | None = None
    session_id: str | None = None
    expires_at: int | None = None


class AuthManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: httpx.AsyncClient | None = None
        self._active_checks: dict[tuple, float] = {}

    async def authenticate_access_token(self, access_token: str) -> Principal:
        if not self.settings.auth_jwt_secret:
            raise LCPError("LCP_AUTH_UNAVAILABLE", "Authentication is not configured", retryable=True)
        try:
            claims = jwt.decode(
                access_token,
                self.settings.auth_jwt_secret,
                algorithms=["HS256"],
                issuer=self.settings.auth_jwt_issuer,
                audience=self.settings.auth_jwt_audience,
                options={"require": ["sub", "exp", "iat", "sv", "sid"]},
            )
            if claims.get("type") != "access" or not claims.get("sub"):
                raise ValueError("wrong token type")
            user_id, session_id = str(UUID(claims["sub"])), str(UUID(claims["sid"]))
            version, expires = claims["sv"], claims["exp"]
            if type(version) is not int or version < 1 or type(expires) is not int:
                raise ValueError("wrong access claims")
        except Exception as exc:
            raise LCPError("LCP_AUTH_INVALID", "Access token is invalid or expired") from exc
        await self.validate_claims(user_id, version, session_id, expires, force=True)
        return Principal(user_id, "account", version, session_id, expires)

    async def validate_session(self, session, *, force: bool = False) -> None:
        if session.auth_kind == "guest" and session.user_id is None:
            return
        if not session.user_id or not session.security_version or not session.auth_session_id or not session.token_expires_at:
            raise LCPError("LCP_AUTH_INVALID", "Authenticate again with a current access token", close_code=4401)
        await self.validate_claims(
            session.user_id, session.security_version, session.auth_session_id, session.token_expires_at,
            force=force,
        )

    async def validate_claims(self, user_id, version, session_id, expires, *, force=False) -> None:
        if expires <= int(time.time()):
            raise LCPError("LCP_AUTH_INVALID", "Access session expired", close_code=4401)
        cache_key = (user_id, version, session_id, expires)
        if not force and self._active_checks.get(cache_key, 0) > time.monotonic():
            return
        if not self.settings.auth_session_status_url or not self.settings.lcp_internal_token:
            raise LCPError("LCP_AUTH_UNAVAILABLE", "Identity validation is unavailable", retryable=True, close_code=1013)
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(2, connect=1), trust_env=False)
        try:
            response = await self._client.post(
                self.settings.auth_session_status_url,
                headers={"X-Lurg-Internal-Token": self.settings.lcp_internal_token},
                json={"user_id": user_id, "security_version": version, "session_id": session_id,
                      "token_expires_at": expires},
            )
            response.raise_for_status()
            active = response.json().get("active")
            if type(active) is not bool:
                raise ValueError("Invalid identity response")
        except (httpx.HTTPError, ValueError, AttributeError) as exc:
            self._active_checks.pop(cache_key, None)
            raise LCPError(
                "LCP_AUTH_UNAVAILABLE", "Identity validation is unavailable", retryable=True, close_code=1013
            ) from exc
        if not active:
            self._active_checks.pop(cache_key, None)
            raise LCPError("LCP_AUTH_INVALID", "Access session revoked or unavailable", close_code=4401)
        if expires <= int(time.time()):
            self._active_checks.pop(cache_key, None)
            raise LCPError("LCP_AUTH_INVALID", "Access session expired", close_code=4401)
        now = time.monotonic()
        if len(self._active_checks) >= 2048:
            self._active_checks = {key: deadline for key, deadline in self._active_checks.items() if deadline > now}
            if len(self._active_checks) >= 2048:
                self._active_checks.clear()
        self._active_checks[cache_key] = now + min(5, max(0, self.settings.lcp_auth_check_cache_seconds))

    async def close(self):
        if self._client is not None:
            await self._client.aclose()
