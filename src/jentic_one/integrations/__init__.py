"""Composition-layer integrations with external platforms.

Like ``wiring.py``, this package sits at the composition layer — outside any
surface package — so it may be wired into the app factories without creating a
surface-boundary violation. Modules here import only ``shared/*``, stdlib, and
the project's existing HTTP/logging dependencies (``httpx``, ``structlog``);
never broker/registry/admin/control.
"""
