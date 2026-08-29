from __future__ import annotations

import base64
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from pydantic import SecretStr

from release_intelligence.adapters.github.rate_limits import is_rate_limited, reset_at
from release_intelligence.ports.github import (
    GitHubHttpClient,
    GitHubPartialData,
    GitHubRateLimited,
    GitHubUnauthorized,
)


class GitHubAuthorizationError(Exception):
    """A caller-safe invalid OAuth authorization result."""

    def __init__(self) -> None:
        super().__init__("GitHub authorization was invalid")


class GitHubUpstreamError(Exception):
    """A caller-safe GitHub availability or response-contract failure."""

    def __init__(self) -> None:
        super().__init__("GitHub authentication unavailable")


@dataclass(frozen=True)
class GitHubOAuthIdentity:
    user_id: str
    login: str


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


class GitHubAppTokenProvider:
    """Mint short-lived GitHub App credentials without a persistence boundary."""

    def __init__(
        self,
        *,
        app_id: str,
        private_key: SecretStr,
        client: GitHubHttpClient,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        loaded_key = serialization.load_pem_private_key(
            private_key.get_secret_value().encode(), password=None
        )
        if not isinstance(loaded_key, rsa.RSAPrivateKey):
            raise TypeError("GitHub App private key must be RSA")
        self._app_id = app_id
        self._private_key = loaded_key
        self._client = client
        self._clock = clock or (lambda: datetime.now(UTC))

    def create_app_jwt(self) -> str:
        issued_at = self._clock()
        if issued_at.tzinfo is None:
            raise ValueError("clock must return a timezone-aware datetime")
        issued_timestamp = int(issued_at.astimezone(UTC).timestamp())
        header = {"alg": "RS256", "typ": "JWT"}
        claims = {
            "iat": issued_timestamp,
            "exp": int((issued_at + timedelta(minutes=10)).timestamp()),
            "iss": self._app_id,
        }
        segments = (
            _base64url(json.dumps(header, separators=(",", ":")).encode()),
            _base64url(json.dumps(claims, separators=(",", ":")).encode()),
        )
        signing_input = ".".join(segments).encode()
        signature = self._private_key.sign(
            signing_input,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return f"{segments[0]}.{segments[1]}.{_base64url(signature)}"

    async def installation_token(self, installation_id: int) -> SecretStr:
        token: object | None = None
        try:
            response = await self._client.post(
                f"/app/installations/{installation_id}/access_tokens",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {self.create_app_jwt()}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            response.raise_for_status()
            token = response.json().get("token")
        except httpx.HTTPStatusError as error:
            error_response = error.response
            payload: object = None
            try:
                payload = error_response.json()
            except (TypeError, ValueError):
                payload = None
            if is_rate_limited(
                error_response.status_code, error_response.headers, payload
            ):
                raise GitHubRateLimited(reset_at(error_response.headers)) from None
            if error_response.status_code in (401, 403, 404):
                raise GitHubUnauthorized() from None
            raise GitHubPartialData() from None
        except httpx.TransportError:
            raise GitHubPartialData() from None
        except (TypeError, ValueError, AttributeError):
            raise GitHubPartialData() from None
        if not isinstance(token, str) or not token:
            raise GitHubPartialData()
        return SecretStr(token)


class GitHubOAuthGateway:
    """GitHub OAuth identity exchange; user tokens stay on the server."""

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: SecretStr,
        client: GitHubHttpClient,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._client = client

    def authorization_url(self, state: str) -> str:
        query = urlencode(
            {"client_id": self._client_id, "state": state, "scope": "read:user"}
        )
        return f"https://github.com/login/oauth/authorize?{query}"

    async def exchange_code(self, code: str) -> SecretStr:
        token: object | None = None
        failure: Exception | None = None
        try:
            response = await self._client.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"},
                json={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret.get_secret_value(),
                    "code": code,
                },
            )
            response.raise_for_status()
            token = response.json().get("access_token")
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 429 or error.response.status_code >= 500:
                failure = GitHubUpstreamError()
            else:
                failure = GitHubAuthorizationError()
        except httpx.TransportError:
            failure = GitHubUpstreamError()
        except (TypeError, ValueError, AttributeError):
            failure = GitHubAuthorizationError()
        if failure is not None:
            raise failure
        if not isinstance(token, str) or not token:
            raise GitHubAuthorizationError()
        return SecretStr(token)

    async def current_user(self, token: SecretStr) -> GitHubOAuthIdentity:
        payload: Mapping[str, object] | None = None
        failure: Exception | None = None
        try:
            response = await self._client.get(
                "/user",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {token.get_secret_value()}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as error:
            if error.response.status_code in (401, 403):
                failure = GitHubAuthorizationError()
            else:
                failure = GitHubUpstreamError()
        except (httpx.TransportError, TypeError, ValueError, AttributeError):
            failure = GitHubUpstreamError()
        if failure is not None:
            raise failure
        if payload is None:
            raise GitHubUpstreamError()
        user_id = payload.get("id")
        login = payload.get("login")
        if not isinstance(user_id, int) or not isinstance(login, str) or not login:
            raise GitHubUpstreamError() from None
        return GitHubOAuthIdentity(user_id=f"github:{user_id}", login=login)
