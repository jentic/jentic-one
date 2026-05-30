"""Abstract base class and data structures for security plugins."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SecurityVerdict:
    """Result of a security scan performed by a plugin."""

    is_safe: bool
    verdict: str  # "pass", "block", "flag", etc.
    decision_layer: str  # Which layer made the decision
    confidence_score: float
    plugin_name: str  # Which plugin produced this verdict
    error: str | None = None


class SecurityPlugin(ABC):
    """Abstract base class for security plugins."""

    name: str

    @abstractmethod
    async def scan_text(self, text: str) -> SecurityVerdict:
        """Scan arbitrary text for security threats."""

    def should_scan_ingress(self, path: str, method: str) -> bool:
        """Determine if this plugin should scan a given inbound request.

        Default is True. Concrete plugins can override to target specific
        endpoints (e.g. only /search).
        """
        return True

    def should_scan_egress(self, host: str, method: str) -> bool:
        """Determine if this plugin should scan a given outbound request.

        Default is True. Concrete plugins can override to target specific
        hosts or ignore certain domains.
        """
        return True
