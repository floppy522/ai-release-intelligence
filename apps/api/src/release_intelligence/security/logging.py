from __future__ import annotations

import logging

UVICORN_ACCESS_LOGGER = "uvicorn.access"


class AccessLogQueryRedactionFilter(logging.Filter):
    """Keep Uvicorn access logs useful without retaining URL query secrets."""

    def filter(self, record: logging.LogRecord) -> bool:
        arguments = record.args
        if isinstance(arguments, tuple) and len(arguments) >= 5:
            request_target = arguments[2]
            if isinstance(request_target, str):
                path = request_target.partition("?")[0]
                record.args = ("-", arguments[1], path, arguments[3], arguments[4])
                return True
        record.msg = "access log redacted"
        record.args = ()
        return True


def install_access_log_redaction() -> None:
    """Install the process-wide filter once, even across lifespan restarts."""
    logger = logging.getLogger(UVICORN_ACCESS_LOGGER)
    if not any(
        isinstance(item, AccessLogQueryRedactionFilter) for item in logger.filters
    ):
        logger.addFilter(AccessLogQueryRedactionFilter())
