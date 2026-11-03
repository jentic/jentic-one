"""Operation-discovery errors shared by the registry and the broker.

Resolving a request (an operation id, or METHOD+URL through the URL index) to a
registered operation fails the same few ways on every surface that does it. The
registry raises these from its inspect and URL-lookup services; the broker
raises :class:`OperationNotFoundError` for an unregistered upstream URL and
receives the others from the in-process registry resolver. Each surface renders
them in its own problem+json shape: the registry through
``make_service_error_handler``, the broker through its taxonomy handler
(``broker/core/problem.py``), which adds ``error_origin``.

Pure module — no FastAPI or DB imports.
"""

from __future__ import annotations


class DiscoveryError(Exception):
    """Base for failures to resolve a request to a registered operation."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class OperationNotFoundError(DiscoveryError):
    """No registered operation matches the identifier or URL.

    ``detail`` replaces the default message when the miss has no single
    identifier to name (e.g. an unregistered upstream URL, which can carry
    secrets in its query string and is never echoed back).
    """

    def __init__(self, identifier: str | None = None, *, detail: str | None = None) -> None:
        if detail is None:
            detail = (
                f"Operation '{identifier}' not found"
                if identifier is not None
                else "Operation not found"
            )
        super().__init__(detail)
        self.identifier = identifier


class MethodNotAllowedError(DiscoveryError):
    """The URL matches an operation, but not for the requested HTTP method."""

    def __init__(self, allowed_methods: list[str]) -> None:
        super().__init__(f"Method not allowed; allowed: {', '.join(allowed_methods)}")
        self.allowed_methods = allowed_methods


class AmbiguousMatchError(DiscoveryError):
    """More than one URL index entry matches with equal specificity."""

    def __init__(self, candidate_count: int) -> None:
        super().__init__(
            f"Ambiguous URL match: {candidate_count} candidates with equal specificity"
        )
        self.candidate_count = candidate_count


class TooManyCandidatesError(DiscoveryError):
    """The URL index returns more candidates than can be ranked safely."""

    def __init__(self) -> None:
        super().__init__("URL index returned too many candidates")
