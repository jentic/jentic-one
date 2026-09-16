"""Flow-specific handlers for the connect-session state machine.

``ConnectSessionService`` owns the flow-agnostic state machine + the shared
finalise / identity-echo / catalog-auto-import sequence; each handler here
owns:

* setting up flow-specific storage at create time (``prepare``),
* the vendor conversation at confirm time (``begin``),
* reporting progress on each ``/status`` tick (``status``),
* any flow-specific cleanup inside the finalise txn (``on_finalise``).

Selection is ``handler_for(flow.kind)`` — a discriminator on the vendor's
``VendorFlowConfig``, mirroring how the config union itself is
discriminated.

``complete_from_callback`` is a concrete method on ``AuthCodeFlowHandler``
only; the callback router calls it directly (routing is auth-code-specific
by construction — only ``sid``-bearing state tokens reach that path).
"""

from jentic_one.control.services.integrations.flow_handlers.auth_code import (
    AuthCodeFlowHandler,
)
from jentic_one.control.services.integrations.flow_handlers.base import (
    AuthCodeBeginResult,
    AuthFlowHandler,
    BeginResult,
    DeviceAuthorizationBeginResult,
    StatusReport,
    SuccessTokens,
)
from jentic_one.control.services.integrations.flow_handlers.device_authorization import (
    DeviceAuthorizationHandler,
)

__all__ = [
    "AuthCodeBeginResult",
    "AuthCodeFlowHandler",
    "AuthFlowHandler",
    "BeginResult",
    "DeviceAuthorizationBeginResult",
    "DeviceAuthorizationHandler",
    "StatusReport",
    "SuccessTokens",
    "handler_for",
]

_HANDLERS: dict[str, type[AuthFlowHandler]] = {
    DeviceAuthorizationHandler.kind: DeviceAuthorizationHandler,
    AuthCodeFlowHandler.kind: AuthCodeFlowHandler,
}


def handler_for(kind: str) -> type[AuthFlowHandler]:
    """Resolve a flow handler class from a ``VendorFlowConfig.kind`` discriminator.

    Raises KeyError for an unknown kind — callers should already have refused
    unsupported flows at config-load time; this is a defensive fall-through.
    """
    try:
        return _HANDLERS[kind]
    except KeyError as exc:
        raise KeyError(f"unsupported flow kind: {kind!r}") from exc
