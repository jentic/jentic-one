"""Pure helpers for the catalog logo cache (no I/O).

The registry only serves raster images it has positively identified from their
leading bytes. The upstream ``Content-Type`` is ignored: SVG (scriptable) and
anything unrecognised is refused, so the endpoint can never be coaxed into
serving active content from the control-plane origin.
"""

from __future__ import annotations

from datetime import datetime

#: ``catalog_logos.status`` values (see the model docstring).
LOGO_STATUS_OK = "ok"
LOGO_STATUS_UNSUPPORTED = "unsupported"
LOGO_STATUS_ERROR = "error"

#: A failed fetch is retried after at most this long, even when the configured
#: max-age is longer, so a transient upstream outage doesn't hide a logo for days.
ERROR_RETRY_SECONDS = 3600

#: Upper bound on one logo fetch. Logos are fetched inline on the request path,
#: so a slow upstream must not hold the request for the full spec-fetch timeout.
FETCH_TIMEOUT_SECONDS = 10.0


def sniff_image_type(content: bytes) -> str | None:
    """Return the media type for PNG/JPEG/GIF/WebP bytes, else ``None``."""
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return None


def is_fresh(status: str, fetched_at: datetime, *, now: datetime, max_age_seconds: int) -> bool:
    """Whether a cache row's last fetch outcome can be reused without refetching."""
    window = max_age_seconds
    if status == LOGO_STATUS_ERROR:
        window = min(window, ERROR_RETRY_SECONDS)
    return (now - fetched_at).total_seconds() < window
