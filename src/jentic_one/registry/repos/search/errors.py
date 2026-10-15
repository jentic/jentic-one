"""Errors for the search capability."""

from __future__ import annotations


class SearchUnsupportedError(RuntimeError):
    """Raised when the active search strategy cannot satisfy the requested search.

    Covers both resolve time (no strategy registered for the backend/mode, or a
    strategy's ``from_config`` rejects the configuration) and query time (a
    strategy's ``search_operations`` cannot run, e.g. its provider is down or
    misconfigured). ``SearchService`` maps it to ``SearchUnavailableError`` at
    both points, so strategies stay within the repos-layer error taxonomy.

    The message is client-facing: ``str(exc)`` becomes the HTTP 501
    ``detail`` and the MCP ``search_apis`` tool error text verbatim. Pass a
    generic, client-safe message and log internal detail (provider hosts,
    config paths, env-var names, upstream exception text) server-side
    (CWE-209).
    """
