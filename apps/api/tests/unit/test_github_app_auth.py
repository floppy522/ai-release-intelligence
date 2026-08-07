from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from pydantic import SecretStr, ValidationError

from release_intelligence.adapters.github.auth import (
    GitHubAppTokenProvider,
    GitHubAuthorizationError,
    GitHubOAuthGateway,
    GitHubUpstreamError,
)
from release_intelligence.config import AppSettings


class FakeResponse:
    def __init__(
        self,
        payload: dict[str, object],
        error: httpx.HTTPStatusError | None = None,
    ) -> None:
        self._payload = payload
        self._error = error
        self.raise_for_status_called = False

    def raise_for_status(self) -> None:
        self.raise_for_status_called = True
        if self._error is not None:
            raise self._error

    def json(self) -> dict[str, object]:
        return self._payload


class FakeGitHubClient:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []
        self.response = FakeResponse({"token": "ghs_installation-secret"})

    async def post(self, path: str, **kwargs: Any) -> FakeResponse:
        self.requests.append({"path": path, **kwargs})
        return self.response

    async def get(self, path: str, **kwargs: Any) -> FakeResponse:
        self.requests.append({"path": path, **kwargs})
        return self.response


@pytest.fixture
def private_key() -> SecretStr:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return SecretStr(pem)


def _decode_segment(segment: str) -> dict[str, object]:
    padding = "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(segment + padding))


def _status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request(
        "POST", "https://github.com/login/oauth/access_token?code=secret-code"
    )
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("secret-code", request=request, response=response)


def test_app_jwt_uses_rs256_and_ten_minute_maximum_lifetime(
    private_key: SecretStr,
) -> None:
    now = datetime(2026, 8, 7, 16, 0, tzinfo=UTC)
    provider = GitHubAppTokenProvider(
        app_id="4242",
        private_key=private_key,
        client=FakeGitHubClient(),
        clock=lambda: now,
    )

    encoded = provider.create_app_jwt()
    header_segment, claims_segment, signature_segment = encoded.split(".")
    header = _decode_segment(header_segment)
    claims = _decode_segment(claims_segment)

    assert header == {"alg": "RS256", "typ": "JWT"}
    assert claims["iss"] == "4242"
    assert claims["exp"] - claims["iat"] <= 600  # type: ignore[operator]
    assert claims["iat"] == int(now.timestamp())
    assert signature_segment
    loaded_key = serialization.load_pem_private_key(
        private_key.get_secret_value().encode(), password=None
    )
    assert isinstance(loaded_key, rsa.RSAPrivateKey)
    signature = base64.urlsafe_b64decode(
        signature_segment + "=" * (-len(signature_segment) % 4)
    )
    loaded_key.public_key().verify(
        signature,
        f"{header_segment}.{claims_segment}".encode(),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )


async def test_installation_token_is_secret_and_never_persisted(
    private_key: SecretStr,
) -> None:
    client = FakeGitHubClient()
    provider = GitHubAppTokenProvider(
        app_id="4242",
        private_key=private_key,
        client=client,
        clock=lambda: datetime(2026, 8, 7, 16, 0, tzinfo=UTC),
    )

    token = await provider.installation_token(123)

    assert isinstance(token, SecretStr)
    assert token.get_secret_value() == "ghs_installation-secret"
    assert len(client.requests) == 1
    request = client.requests[0]
    assert request["path"] == "/app/installations/123/access_tokens"
    assert str(request["headers"]).find("ghs_installation-secret") == -1
    assert not hasattr(provider, "persistence")
    assert client.response.raise_for_status_called is True


async def test_installation_token_rejects_malformed_github_response(
    private_key: SecretStr,
) -> None:
    client = FakeGitHubClient()
    client.response = FakeResponse({"expires_at": "2026-08-07T17:00:00Z"})
    provider = GitHubAppTokenProvider(
        app_id="4242",
        private_key=private_key,
        client=client,
    )

    with pytest.raises(GitHubUpstreamError, match="GitHub authentication unavailable"):
        await provider.installation_token(123)


@pytest.mark.parametrize(
    ("status_code", "error_type"),
    (
        (401, GitHubAuthorizationError),
        (503, GitHubUpstreamError),
    ),
)
async def test_oauth_exchange_classifies_http_failures_without_secret_chains(
    status_code: int,
    error_type: type[Exception],
) -> None:
    client = FakeGitHubClient()
    client.response = FakeResponse({}, _status_error(status_code))
    gateway = GitHubOAuthGateway(
        client_id="client-id",
        client_secret=SecretStr("client-secret"),
        client=client,
    )

    with pytest.raises(error_type) as raised:
        await gateway.exchange_code("secret-code")

    assert "secret-code" not in str(raised.value)
    assert "client-secret" not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("oauth_state_ttl_seconds", 0),
        ("oauth_state_ttl_seconds", 3601),
        ("session_ttl_seconds", 0),
        ("session_ttl_seconds", 604801),
    ),
)
def test_auth_ttls_are_positive_and_bounded(
    private_key: SecretStr,
    field: str,
    value: int,
) -> None:
    values: dict[str, object] = {
        "database_url": "postgresql+asyncpg://postgres:postgres@localhost/test",
        "credential_encryption_key": Fernet.generate_key().decode(),
        "github_app_id": "4242",
        "github_private_key_pem": private_key,
        "github_client_id": "client-id",
        "github_client_secret": "client-secret",
        "session_ttl_seconds": 3600,
        "oauth_state_ttl_seconds": 120,
    }
    values[field] = value

    with pytest.raises(ValidationError):
        AppSettings(**values)
