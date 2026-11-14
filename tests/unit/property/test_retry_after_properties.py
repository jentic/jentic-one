"""Property-based tests for the shared Retry-After parser."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from jentic_one.shared.http.retry_after import MAX_RETRY_AFTER_S, parse_retry_after

_RETRY_AFTER_INPUT = st.one_of(
    st.text(),
    st.text(alphabet="0123456789.+-: ,GMTUTCEPSJanFebFriMon\t\r\n"),
)


@given(raw=_RETRY_AFTER_INPUT)
def test_parse_retry_after_total_and_bounded(raw: str) -> None:
    """parse_retry_after never raises and returns None or an int in [0, MAX_RETRY_AFTER_S]."""
    result = parse_retry_after(raw, now_epoch=1_700_000_000.0)
    assert result is None or (isinstance(result, int) and 0 <= result <= MAX_RETRY_AFTER_S)
