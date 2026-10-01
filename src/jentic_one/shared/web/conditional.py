"""HTTP conditional-request helpers shared by routers that emit ``ETag``."""

from __future__ import annotations


def etag_matches(if_none_match: str, etag: str) -> bool:
    """RFC 9110 ``If-None-Match`` comparison (weak comparison, ``*`` honoured).

    Digest ETags are content-derived and strong, but clients may echo them back
    weakened (``W/"…"``) through caches, so compare opaque-tags only. As a
    compatibility arm, the bare (unquoted) digest is accepted too — integrators
    often hold the digest from a response body, and rejecting the obvious
    ``If-None-Match: <digest>`` form would silently disable change-polling with
    no error.
    """
    candidates = [v.strip() for v in if_none_match.split(",")]
    if "*" in candidates:
        return True
    opaque = {v.removeprefix("W/") for v in candidates}
    return etag in opaque or etag.strip('"') in opaque
