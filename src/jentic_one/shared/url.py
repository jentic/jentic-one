"""Shared URL utilities for server-variable substitution."""

from __future__ import annotations

import re
from urllib.parse import quote, urlsplit

# A ``{name}`` OpenAPI server-variable placeholder. Names follow the OpenAPI
# variable-name grammar (letters, digits, underscores, hyphens, dots) so an
# empty ``{}`` or a stray brace in a query value is not mistaken for one.
_SERVER_VAR_PLACEHOLDER = re.compile(r"\{[A-Za-z0-9_.-]+\}")


def apply_server_variables(url: str, variables: dict[str, str]) -> str:
    """Substitute OpenAPI server-variable values into a URL template.

    Each ``{name}`` placeholder in the URL is replaced with the URL-encoded
    value from *variables*. Unmatched placeholders (e.g. path parameters
    resolved elsewhere) are left intact.
    """
    result = url
    for name, value in variables.items():
        placeholder = "{" + name + "}"
        result = result.replace(placeholder, quote(value, safe=""))
    return result


def has_host_server_variable(url: str) -> bool:
    """Whether *url*'s scheme/host carries an unsubstituted ``{name}`` template.

    A region-split API (e.g. ``https://{region}.posthog.com``) reaches the broker
    with the placeholder still in the **host** because the caller derives the URL
    from the spec's templated server. Only the scheme + netloc are inspected so an
    ordinary path parameter (``/users/{id}``) or a ``{...}`` inside the query never
    triggers a false positive — those are not server variables.
    """
    parts = urlsplit(url)
    host = f"{parts.scheme}://{parts.netloc}" if parts.netloc else url.split("/", 1)[0]
    return bool(_SERVER_VAR_PLACEHOLDER.search(host))
