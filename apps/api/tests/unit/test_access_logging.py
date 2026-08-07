from __future__ import annotations

import logging

import pytest

from release_intelligence.security.logging import (
    AccessLogQueryRedactionFilter,
    install_access_log_redaction,
)

CANONICAL_TEMPLATE = '%s - "%s %s HTTP/%s" %d'


def test_uvicorn_access_log_keeps_only_method_path_and_status(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("uvicorn.access")
    install_access_log_redaction()
    install_access_log_redaction()

    with caplog.at_level(logging.INFO, logger="uvicorn.access"):
        logger.info(
            CANONICAL_TEMPLATE,
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


@pytest.mark.parametrize(
    ("message", "arguments", "sentinel"),
    (
        (
            "unexpected %s %s %s %s %s",
            ("wrong-template-secret", "GET", "/safe", "1.1", 200),
            "wrong-template-secret",
        ),
        (
            CANONICAL_TEMPLATE,
            ("six-arg-secret", "GET", "/safe", "1.1", 200, "extra-secret"),
            "six-arg-secret",
        ),
        ("%(value)s", {"value": "mapping-secret"}, "mapping-secret"),
        (
            CANONICAL_TEMPLATE,
            ("-", "SECRET-METHOD", "/safe", "1.1", 200),
            "SECRET-METHOD",
        ),
        (
            CANONICAL_TEMPLATE,
            ("-", "GET", "/safe", "secret-version", 200),
            "secret-version",
        ),
        (
            CANONICAL_TEMPLATE,
            ("-", "GET", "/safe", "1.1", "secret-status"),
            "secret-status",
        ),
        (
            CANONICAL_TEMPLATE,
            ("-", "GET", "/safe", "1.1", 99),
            "/safe",
        ),
        (
            CANONICAL_TEMPLATE,
            ("-", "GET", "/safe", "1.1", 600),
            "/safe",
        ),
        (
            CANONICAL_TEMPLATE,
            ("-", "GET", "not-a-path-secret", "1.1", 200),
            "not-a-path-secret",
        ),
        (
            CANONICAL_TEMPLATE,
            ("-", "GET", "/safe\ncontrol-path-secret", "1.1", 200),
            "control-path-secret",
        ),
    ),
)
def test_access_filter_fails_closed_for_noncanonical_records(
    caplog: pytest.LogCaptureFixture,
    message: str,
    arguments: tuple[object, ...] | dict[str, object],
    sentinel: str,
) -> None:
    access_filter = AccessLogQueryRedactionFilter()
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        message,
        (),
        None,
    )
    record.args = arguments

    assert access_filter.filter(record) is True
    assert record.getMessage() == "access log redacted"
    assert sentinel not in record.getMessage()

    logger = logging.getLogger("uvicorn.access")
    install_access_log_redaction()
    with caplog.at_level(logging.INFO, logger="uvicorn.access"):
        if isinstance(arguments, dict):
            logger.info(message, arguments)
        else:
            logger.info(message, *arguments)

    emitted = "\n".join(caplog.messages)
    assert "access log redacted" in emitted
    assert sentinel not in emitted


def test_access_filter_discards_client_and_query_secrets_from_valid_record(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("uvicorn.access")
    install_access_log_redaction()

    with caplog.at_level(logging.INFO, logger="uvicorn.access"):
        logger.info(
            CANONICAL_TEMPLATE,
            "client-address-secret",
            "POST",
            "/api/auth/github/callback?code=query-secret&state=state-secret",
            "2",
            204,
        )

    emitted = "\n".join(caplog.messages)
    assert '- - "POST /api/auth/github/callback HTTP/2" 204' in emitted
    for secret in ("client-address-secret", "query-secret", "state-secret"):
        assert secret not in emitted
