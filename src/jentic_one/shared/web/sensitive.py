"""Marker for secret-carrying schema fields: ``x-sensitive: true``.

The Jentic CLI derives its Layer-1 (typed, exact) output redaction from the
OpenAPI spec: ``make generate-api`` in ``cli/`` turns every schema property
carrying the ``x-sensitive: true`` extension into an entry in the generated
``SensitiveFields`` table, which the CLI registers with its redactor at
startup. An annotated field is therefore guaranteed to render as
``[REDACTED]`` in CLI output regardless of naming heuristics.

Usage — pass :data:`SENSITIVE` as (or merge it into) ``json_schema_extra`` on
the *field*, never on a nested inline object (the generated table is flat per
component schema; the CLI's spec generator fails loud on nested annotations):

    from jentic_one.shared.web.sensitive import SENSITIVE

    class LoginRequest(BaseModel):
        password: str = Field(min_length=1, json_schema_extra=SENSITIVE)

The CLI's architecture sweep (``cli/tests/arch`` ``Test1H``) fails on any NEW
secret-shaped property name that lacks this annotation, so annotate every
field that carries a secret value (tokens, passwords, keys) at creation time.
"""

from __future__ import annotations

from typing import Final

from pydantic.config import JsonDict

SENSITIVE: Final[JsonDict] = {"x-sensitive": True}
