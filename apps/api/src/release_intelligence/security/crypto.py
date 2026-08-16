from __future__ import annotations

import hashlib
import hmac
import secrets

from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr


class CredentialCipher:
    """Encrypt long-lived credentials with a deployment-owned Fernet key."""

    def __init__(self, deployment_key: SecretStr) -> None:
        try:
            self._fernet = Fernet(deployment_key.get_secret_value().encode())
        except (TypeError, ValueError) as error:
            raise ValueError(
                "credential encryption key must be a Fernet key"
            ) from error

    def encrypt(self, credential: SecretStr) -> str:
        return self._fernet.encrypt(credential.get_secret_value().encode()).decode()

    def decrypt(self, ciphertext: str) -> SecretStr:
        try:
            plaintext = self._fernet.decrypt(ciphertext.encode()).decode()
        except (InvalidToken, UnicodeDecodeError) as error:
            raise ValueError("credential ciphertext is invalid") from error
        return SecretStr(plaintext)


def generate_opaque_token() -> str:
    """Return enough entropy for OAuth state, session, and CSRF bearer values."""
    return secrets.token_urlsafe(32)


def csrf_token_for_session(session_token: str) -> str:
    """Derive domain-separated CSRF material from an opaque session bearer."""
    return hashlib.sha256(
        f"release-intelligence:csrf:{session_token}".encode()
    ).hexdigest()


def token_digest(token: str) -> str:
    """Persist only a one-way digest of high-entropy bearer material."""
    return hashlib.sha256(token.encode()).hexdigest()


def digest_matches(token: str, expected_digest: str) -> bool:
    return hmac.compare_digest(token_digest(token), expected_digest)
