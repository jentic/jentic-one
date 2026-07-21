"""Domain-level database exceptions."""


class DatabaseIntegrityError(Exception):
    """Raised when a transaction fails due to an integrity constraint violation."""

    def __init__(self, detail: str = "") -> None:
        super().__init__(detail)
        self.detail = detail


class DatabaseUnavailableError(Exception):
    """Raised when a transient database failure persists after retries."""

    def __init__(self, detail: str = "") -> None:
        super().__init__(detail)
        self.detail = detail


class DatabaseDataError(Exception):
    """Raised when a write fails because a value does not fit its column.

    Wraps SQLAlchemy ``DataError`` (e.g. Postgres ``StringDataRightTruncation``
    when a value exceeds a ``VARCHAR`` length). This is a client-fixable input
    problem, not a server fault — handlers map it to a 400.
    """

    def __init__(self, detail: str = "") -> None:
        super().__init__(detail)
        self.detail = detail
