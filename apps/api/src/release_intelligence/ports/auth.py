class AuthPersistenceError(Exception):
    """Sanitized authentication persistence failure safe across the API boundary."""

    def __init__(self) -> None:
        super().__init__("Authentication persistence unavailable")
