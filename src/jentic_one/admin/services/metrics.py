"""Admin service metrics — counters for auth outcomes and audit events."""

from __future__ import annotations

from jentic_one.shared.audit import audit_events_counter
from jentic_one.shared.auth.login_metrics import login_counter

__all__ = ["audit_events_counter", "login_counter"]
