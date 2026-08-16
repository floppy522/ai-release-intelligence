from __future__ import annotations

import logging
import re
from typing import cast

UVICORN_ACCESS_LOGGER = "uvicorn.access"
UVICORN_ACCESS_TEMPLATE = '%s - "%s %s HTTP/%s" %d'
SAFE_ACCESS_LOG_MESSAGE = "access log redacted"
HTTP_METHODS = frozenset(
    {"CONNECT", "DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT", "TRACE"}
)
HTTP_VERSIONS = frozenset({"1.0", "1.1", "2", "3"})
MAX_REQUEST_TARGET_LENGTH = 8192
MAX_PATH_LENGTH = 2048
SAFE_APPLICATION_LOG_MESSAGE = "application event redacted"
SAFE_APPLICATION_MESSAGES = frozenset(
    {
        "AI explanation generated",
        "AI explanation unavailable",
        "Failed analysis audit could not be persisted",
    }
)
SAFE_APPLICATION_FIELDS = frozenset(
    {
        "ai_model",
        "ai_latency_seconds",
        "ai_input_tokens",
        "ai_output_tokens",
        "ai_cost",
    }
)
_DECIMAL = re.compile(r"^(?:0|[1-9][0-9]{0,9})(?:\.[0-9]{1,6})?$")
_STANDARD_LOG_RECORD_FIELDS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
)


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


class ApplicationLogRedactionFilter(logging.Filter):
    """Fail closed for application logs and retain only approved metrics."""

    def filter(self, record: logging.LogRecord) -> bool:
        record_values = cast(dict[object, object], record.__dict__)
        extra_fields = set(record_values) - _STANDARD_LOG_RECORD_FIELDS
        if not self._is_safe_message(record) or not self._safe_fields(
            record, extra_fields
        ):
            self._redact(record, extra_fields)
            return True
        for field in extra_fields - SAFE_APPLICATION_FIELDS:
            record_values.pop(field, None)
        return True

    @staticmethod
    def _is_safe_message(record: logging.LogRecord) -> bool:
        return (
            type(record.msg) is str
            and record.msg in SAFE_APPLICATION_MESSAGES
            and not record.args
            and record.exc_info is None
            and record.exc_text is None
            and record.stack_info is None
        )

    @staticmethod
    def _safe_fields(record: logging.LogRecord, fields: set[object]) -> bool:
        for field in SAFE_APPLICATION_FIELDS:
            if field not in fields:
                continue
            value = getattr(record, field)
            if field == "ai_model":
                if type(value) is not str or value != "gpt-5.6":
                    return False
            elif field in {"ai_input_tokens", "ai_output_tokens"}:
                if type(value) is not int or not 0 <= value <= 10_000_000:
                    return False
            elif type(value) is not str or _DECIMAL.fullmatch(value) is None:
                return False
        return True

    @staticmethod
    def _redact(record: logging.LogRecord, extra_fields: set[object]) -> None:
        record.msg = SAFE_APPLICATION_LOG_MESSAGE
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        record_values = cast(dict[object, object], record.__dict__)
        for field in extra_fields:
            record_values.pop(field, None)


def get_safe_logger(name: str) -> logging.Logger:
    """Return a logger protected from raw values, exceptions, and surprise extras."""
    logger = logging.getLogger(name)
    if not any(
        isinstance(item, ApplicationLogRedactionFilter) for item in logger.filters
    ):
        logger.addFilter(ApplicationLogRedactionFilter())
    return logger


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
