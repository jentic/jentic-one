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


class DatabaseConsistencyError(Exception):
    """Raised when an ORM operation hits an invalid-state/consistency failure.

    Wraps ``sqlalchemy.exc.InvalidRequestError`` (the parent of
    ``MissingGreenlet``) so that an accidental async lazy load on a stale or
    detached instance surfaces as a known, mappable domain error with a generic
    client message rather than an opaque unhandled traceback. The raw SQLAlchemy
    message is preserved in ``detail`` for server-side diagnosis. See #642.
    """

    def __init__(self, detail: str = "") -> None:
        super().__init__(detail)
        self.detail = detail
