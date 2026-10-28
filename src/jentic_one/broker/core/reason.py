"""The agent's stated reason for a call, read off its ``Jentic-Reason`` header.

The value is the agent's own free text, which Jentic's clients send as
percent-encoded UTF-8 (a header carries ASCII only). A client that sends raw
UTF-8 still works: Starlette decodes header bytes as latin-1, so the value is
turned back into its original bytes before it is decoded. The reason is
advisory — shown to a reviewer when the call is held — so reading it never
raises and never blocks a call.
"""

from __future__ import annotations

import unicodedata
from urllib.parse import unquote

#: The same cap as an agent's reason on a connect request (``connect_sessions.reason``).
MAX_AGENT_REASON_LEN = 1024


def parse_agent_reason(raw: str | None) -> str | None:
    """The cleaned reason, or ``None`` when the header is absent or carries no text.

    Percent-decoding uses ``unquote`` (not ``unquote_plus``): a literal ``+`` is
    the agent's text, not a space. Control characters other than newline and
    tab are dropped, line endings are normalised to ``\\n``, and the result is
    trimmed and capped at :data:`MAX_AGENT_REASON_LEN` characters.
    """
    if not raw:
        return None
    try:
        text = raw.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        text = raw
    text = unquote(text, errors="replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch) != "Cc")
    text = text.strip()[:MAX_AGENT_REASON_LEN].rstrip()
    return text or None
