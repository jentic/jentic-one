"""Central registry that orchestrates all registered security plugins."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from src.security.plugin import SecurityPlugin, SecurityVerdict

log = logging.getLogger("jentic.security")


class SecurityRegistry:
    """Singleton registry for security plugins.

    Plugins register themselves at application startup.  The ingress
    middleware and egress broker hook call 'scan_ingress' /
    'scan_egress' which iterate over registered plugins in order.

    First-block-wins: the first plugin returning 'is_safe=False'
    short-circuits and its verdict is returned immediately.
    """

    def __init__(self) -> None:
        self._plugins: list[SecurityPlugin] = []

    # Registration

    def register(self, plugin: SecurityPlugin) -> None:
        """Register a security plugin."""
        self._plugins.append(plugin)
        log.info("Security plugin registered: %s", plugin.name)

    def deregister(self, plugin_name: str) -> None:
        """Remove a plugin by name (primarily for testing)."""
        self._plugins = [p for p in self._plugins if p.name != plugin_name]

    def has_plugins(self) -> bool:
        """Return True if at least one plugin is registered."""
        return bool(self._plugins)

    # Scanning

    async def scan_ingress(self, text: str, path: str, method: str) -> SecurityVerdict | None:
        """Run registered plugins against inbound text.

        Returns the first blocking verdict, or None if all pass.
        """
        for plugin in self._plugins:
            if not plugin.should_scan_ingress(path, method):
                continue
            verdict = await plugin.scan_text(text)
            if not verdict.is_safe:
                return verdict
        return None

    async def scan_egress(self, text: str, host: str, method: str) -> SecurityVerdict | None:
        """Run registered plugins against outbound text.

        Returns the first blocking verdict, or None if all pass.
        """
        for plugin in self._plugins:
            if not plugin.should_scan_egress(host, method):
                continue
            verdict = await plugin.scan_text(text)
            if not verdict.is_safe:
                return verdict
        return None


# Module-level singleton used across the application.
security_registry = SecurityRegistry()
