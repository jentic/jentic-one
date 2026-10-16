"""Password-login outcome counter, shared by every login door.

Defined in ``shared`` so the pre-auth limiter (which runs before any surface
service) and the admin auth service record outcomes on one instrument.
"""

from __future__ import annotations

from jentic_one.shared.metrics import get_meter

_meter = get_meter("admin")

login_counter = _meter.create_counter(
    "admin.auth.login",
    description="Login attempts by outcome",
)

__all__ = ["login_counter"]
