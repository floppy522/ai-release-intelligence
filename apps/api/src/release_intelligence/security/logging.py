from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any, cast

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
SENSITIVE_DEPENDENCY_PREFIXES = (
    "httpx",
    "httpcore",
    "openai",
    "sqlalchemy",
)
_DECIMAL = re.compile(r"^(?:0|[1-9][0-9]{0,9})(?:\.[0-9]{1,6})?$")
_STANDARD_LOG_RECORD_FIELDS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
)
_FORMATTER_LOG_RECORD_FIELDS = frozenset({"asctime", "message"})


class _DependencyRecordValues(dict[str, Any]):
    """Prevent late ``extra`` values from reaching a dependency formatter."""

    def __setitem__(self, key: str, value: Any) -> None:
        if type(key) is str and (
            key in _STANDARD_LOG_RECORD_FIELDS or key in _FORMATTER_LOG_RECORD_FIELDS
        ):
            super().__setitem__(key, value)
            return
        if type(key) is str:
            super().__setitem__(key, SAFE_APPLICATION_LOG_MESSAGE)


class _DependencyLogRecordFactory:
    """Chain the active factory while redacting dependency records at creation."""

    def __init__(self, previous: Callable[..., logging.LogRecord]) -> None:
        self.previous = previous

    def __call__(self, *args: Any, **kwargs: Any) -> logging.LogRecord:
        name = args[0] if args else kwargs.get("name")
        if not _is_sensitive_dependency_name(name):
            return self.previous(*args, **kwargs)
        try:
            record = self.previous(*args, **kwargs)
            return _redact_dependency_record(record, args, kwargs)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:  # noqa: BLE001 - dependency logging must fail closed
            return _fallback_dependency_record(args, kwargs)


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


def install_application_log_redaction() -> None:
    """Redact dependency descendants before any handler can format a record."""
    current = logging.getLogRecordFactory()
    if isinstance(current, _DependencyLogRecordFactory):
        return
    logging.setLogRecordFactory(_DependencyLogRecordFactory(current))


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


def _is_sensitive_dependency_name(name: object) -> bool:
    return type(name) is str and any(
        name == prefix or name.startswith(f"{prefix}.")
        for prefix in SENSITIVE_DEPENDENCY_PREFIXES
    )


def _redact_dependency_record(
    record: object, args: tuple[object, ...], kwargs: dict[str, object]
) -> logging.LogRecord:
    if not isinstance(record, logging.LogRecord):
        return _fallback_dependency_record(args, kwargs)
    record_values = record.__dict__
    if type(record_values) is not dict:
        return _fallback_dependency_record(args, kwargs)

    safe_values = _DependencyRecordValues()
    for field, value in record_values.items():
        if type(field) is str and field in _STANDARD_LOG_RECORD_FIELDS:
            dict.__setitem__(safe_values, field, value)
        elif type(field) is str:
            dict.__setitem__(safe_values, field, SAFE_APPLICATION_LOG_MESSAGE)
    record.__dict__ = safe_values
    record.msg = SAFE_APPLICATION_LOG_MESSAGE
    record.args = ()
    record.exc_info = None
    record.exc_text = None
    record.stack_info = None
    return record


def _fallback_dependency_record(
    args: tuple[object, ...], kwargs: dict[str, object]
) -> logging.LogRecord:
    name = _safe_factory_argument(args, kwargs, 0, "name", "dependency")
    level = _safe_factory_argument(args, kwargs, 1, "level", logging.ERROR)
    pathname = _safe_factory_argument(args, kwargs, 2, "pathname", "")
    lineno = _safe_factory_argument(args, kwargs, 3, "lineno", 0)
    func = _safe_factory_argument(args, kwargs, 7, "func", None)
    if type(name) is not str:
        name = "dependency"
    if type(level) is not int:
        level = logging.ERROR
    if type(pathname) is not str:
        pathname = ""
    if type(lineno) is not int:
        lineno = 0
    if type(func) is not str:
        func = None
    record = logging.LogRecord(
        name,
        level,
        pathname,
        lineno,
        SAFE_APPLICATION_LOG_MESSAGE,
        (),
        None,
        func,
        None,
    )
    record.__dict__ = _DependencyRecordValues(record.__dict__)
    return record


def _safe_factory_argument(
    args: tuple[object, ...],
    kwargs: dict[str, object],
    position: int,
    key: str,
    default: object,
) -> object:
    if len(args) > position:
        return args[position]
    return kwargs.get(key, default)
