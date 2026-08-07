from __future__ import annotations

import logging

import pytest

from release_intelligence.security.logging import (
    AccessLogQueryRedactionFilter,
    install_access_log_redaction,
)


def test_uvicorn_access_log_keeps_only_method_path_and_status(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("uvicorn.access")
    install_access_log_redaction()
    install_access_log_redaction()

    with caplog.at_level(logging.INFO, logger="uvicorn.access"):
        logger.info(
            '%s - "%s %s HTTP/%s" %d',
            "127.0.0.1:54321",
            "GET",
            "/api/auth/github/callback?code=oauth-code-secret&state=state-secret"
            "&client_secret=github-client-secret&token=gho-user-token"
            "&database_url=postgresql://user:db-password@database/app",
            "1.1",
            400,
        )

    messages = "\n".join(caplog.messages)
    assert "GET /api/auth/github/callback HTTP/1.1" in messages
    assert "400" in messages
    for secret in (
        "oauth-code-secret",
        "state-secret",
        "github-client-secret",
        "gho-user-token",
        "postgresql://",
        "db-password",
    ):
        assert secret not in messages
    assert "?" not in messages
    assert sum(
        isinstance(item, AccessLogQueryRedactionFilter) for item in logger.filters
    ) == 1
