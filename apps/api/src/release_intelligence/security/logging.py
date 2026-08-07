from __future__ import annotations

import logging

UVICORN_ACCESS_LOGGER = "uvicorn.access"
UVICORN_ACCESS_TEMPLATE = '%s - "%s %s HTTP/%s" %d'
SAFE_ACCESS_LOG_MESSAGE = "access log redacted"
HTTP_METHODS = frozenset(
    {"CONNECT", "DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT", "TRACE"}
)
HTTP_VERSIONS = frozenset({"1.0", "1.1", "2", "3"})
MAX_REQUEST_TARGET_LENGTH = 8192
MAX_PATH_LENGTH = 2048


class AccessLogQueryRedactionFilter(logging.Filter):
    """Keep Uvicorn access logs useful without retaining URL query secrets."""

    def filter(self, record: logging.LogRecord) -> bool:
        arguments = record.args
        if (
            type(record.msg) is str
            and record.msg == UVICORN_ACCESS_TEMPLATE
            and type(arguments) is tuple
            and len(arguments) == 5
        ):
            client, method, request_target, http_version, status_code = arguments
            if (
                type(client) is str
                and type(method) is str
                and method in HTTP_METHODS
                and type(request_target) is str
                and _is_sane_request_target(request_target)
                and type(http_version) is str
                and http_version in HTTP_VERSIONS
                and type(status_code) is int
                and 100 <= status_code <= 599
            ):
                path = request_target.partition("?")[0]
                record.args = ("-", method, path, http_version, status_code)
                return True
        record.msg = SAFE_ACCESS_LOG_MESSAGE
        record.args = ()
        return True


def install_access_log_redaction() -> None:
    """Install the process-wide filter once, even across lifespan restarts."""
    logger = logging.getLogger(UVICORN_ACCESS_LOGGER)
    if not any(
        isinstance(item, AccessLogQueryRedactionFilter) for item in logger.filters
    ):
        logger.addFilter(AccessLogQueryRedactionFilter())


def _is_sane_request_target(request_target: str) -> bool:
    if (
        not request_target.startswith("/")
        or len(request_target) > MAX_REQUEST_TARGET_LENGTH
        or "#" in request_target
        or any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in request_target
        )
    ):
        return False
    path = request_target.partition("?")[0]
    return bool(path) and len(path) <= MAX_PATH_LENGTH
