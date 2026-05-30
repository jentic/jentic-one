"""Security plugin framework for Jentic Mini.

Provides a vendor-neutral plugin registry that security integrations
(e.g. prompt-injection scanners) can register against.  Core Jentic
code interacts only with the generic ``security_registry`` singleton —
concrete plugin implementations live in their own modules.
"""

from src.security.plugin import SecurityPlugin, SecurityVerdict
from src.security.registry import SecurityRegistry, security_registry


__all__ = [
    "SecurityPlugin",
    "SecurityRegistry",
    "SecurityVerdict",
    "security_registry",
]
