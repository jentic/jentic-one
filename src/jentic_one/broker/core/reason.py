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

#: Code points a reviewer-facing text never needs: controls (``Cc``), format
#: characters (``Cf``: bidi overrides, zero-width marks — the same set
#: ``summary_label`` drops from event summaries), lone surrogates (``Cs``, which
#: no database or JSON encoder accepts), private-use (``Co``) and unassigned
#: (``Cn``) code points.
_DROPPED_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn"})

#: Line and paragraph separators (``Zl``/``Zp``) read as line breaks.
_LINE_BREAKS = str.maketrans({"\r": "\n", "\u2028": "\n", "\u2029": "\n"})


def parse_agent_reason(raw: str | None) -> str | None:
    """The cleaned reason, or ``None`` when the header is absent or carries no text.

    Percent-decoding uses ``unquote`` (not ``unquote_plus``): a literal ``+`` is
    the agent's text, not a space. Line endings and Unicode line separators
    become ``\\n``; control and format characters (other than newline and
    tab), lone surrogates, private-use and unassigned code points are dropped,
    so what is stored renders the same everywhere and never fails to encode;
    the result is trimmed and capped at :data:`MAX_AGENT_REASON_LEN` characters.
    """
    if not raw:
        return None
    try:
        text = raw.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        text = raw
    text = unquote(text, errors="replace")
    text = text.replace("\r\n", "\n").translate(_LINE_BREAKS)
    text = "".join(
        ch for ch in text if ch in "\n\t" or unicodedata.category(ch) not in _DROPPED_CATEGORIES
    )
    text = text.strip()[:MAX_AGENT_REASON_LEN].rstrip()
    return text or None
