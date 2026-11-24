"""Free text an agent writes for a person to read, cleaned before it is stored.

An agent's explanation of a held call is shown to the reviewer deciding it, so
what is stored must render the same everywhere and never fail to encode. The
text is the agent's own claim: it is cleaned, never trusted.
"""

from __future__ import annotations

import unicodedata

#: The same cap as an agent's reason on a connect request (``connect_sessions.reason``).
MAX_AGENT_TEXT_LEN = 1024

#: The longest text accepted before cleaning; longer input is refused, not cut.
MAX_AGENT_TEXT_INPUT_LEN = 4096

#: Code points a reviewer-facing text never needs: controls (``Cc``), format
#: characters (``Cf``: bidi overrides, zero-width marks — the same set
#: ``summary_label`` drops from event summaries), lone surrogates (``Cs``, which
#: no database or JSON encoder accepts), private-use (``Co``) and unassigned
#: (``Cn``) code points.
_DROPPED_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn"})

#: Format characters text needs to read right, kept: the zero-width joiner and
#: non-joiner (emoji sequences, Persian and Indic scripts). Neither reorders
#: nor hides text.
_KEPT_FORMAT = frozenset({"\u200c", "\u200d"})

#: Carriage returns and Unicode line and paragraph separators read as line breaks.
_LINE_BREAKS = str.maketrans({"\r": "\n", "\u2028": "\n", "\u2029": "\n"})


def clean_agent_text(raw: str | None) -> str | None:
    """The cleaned text, or ``None`` when nothing readable is left.

    Line endings and Unicode line separators become ``\\n``; control and
    format characters (other than newline, tab and the zero-width joiner and
    non-joiner), lone surrogates, private-use and unassigned code points are
    dropped; the result is trimmed and capped at :data:`MAX_AGENT_TEXT_LEN`
    characters.
    """
    if not raw:
        return None
    text = raw.replace("\r\n", "\n").translate(_LINE_BREAKS)
    text = "".join(
        ch
        for ch in text
        if ch in "\n\t" or ch in _KEPT_FORMAT or unicodedata.category(ch) not in _DROPPED_CATEGORIES
    )
    text = text.strip()[:MAX_AGENT_TEXT_LEN].rstrip()
    return text or None
