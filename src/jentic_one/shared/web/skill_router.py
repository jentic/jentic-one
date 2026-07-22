"""Public ``GET /skills/jentic.md`` — the onboarding skill served over HTTP.

The onboarding skill (the "how to use Jentic via the CLI" markdown) is embedded
in the Go CLI via ``//go:embed content/jentic.md`` and written to the local
device at install time. That only lands the skill on the machine that ran the
install; a remote agent that can reach the deployment has no first-class way to
fetch it (issue #651). This router serves the *same* markdown over HTTP so any
agent reachable to the service reads the exact version the service ships.

**Single source of truth / no drift.** The served bytes come from the *same
physical file* the CLI embeds — ``cli/internal/skillgen/content/jentic.md``. At
build time ``pyproject.toml`` force-includes it into the wheel at
``jentic_one/skills/jentic.md`` (alongside the ``openapi`` / ``static`` bundles),
so a released service reads it via ``importlib.resources``. In a source checkout
(editable install, tests) the packaged copy does not exist yet, so we fall back
to reading the CLI source file directly. Either way there is one committed file,
so the CLI-embedded and served copies cannot diverge. ``test_skill_router.py``
adds a belt-and-suspenders drift guard.

Hidden from the OpenAPI schema (``include_in_schema=False``): it is onboarding
content, not a product API, so it neither advertises in the spec nor requires a
platform bearer token — an onboarding agent must be able to fetch it before it
holds any credential.
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import Response

# Path the skill is served at. ``/SKILL.md`` is a convenience alias so an agent
# can guess the canonical uppercase convention; both return identical bytes.
SKILL_PATH = "/skills/jentic.md"
SKILL_ALIAS_PATH = "/SKILL.md"

# Markdown is served as ``text/markdown`` (RFC 7763) with UTF-8 so agents and
# browsers render it as text rather than downloading it as an octet-stream.
SKILL_MEDIA_TYPE = "text/markdown; charset=utf-8"

# CLI source file, relative to the repo root, that both the Go embed and (in a
# source checkout) this router read. Kept in lockstep with the ``force-include``
# mapping in ``pyproject.toml``.
_CLI_SKILL_RELPATH = Path("cli/internal/skillgen/content/jentic.md")

_cached_skill: str | None = None


def _read_packaged_skill() -> str | None:
    """Return the force-included skill from the installed wheel, or None.

    Present only in a built wheel (``pyproject.toml`` force-includes the CLI file
    to ``jentic_one/skills/jentic.md``); absent in a source checkout.
    """
    try:
        resource = importlib.resources.files("jentic_one") / "skills" / "jentic.md"
    except (ModuleNotFoundError, FileNotFoundError):
        return None
    if not resource.is_file():
        return None
    return resource.read_text(encoding="utf-8")


def _read_source_skill() -> str | None:
    """Return the CLI source skill from a source checkout, or None.

    Walks up from this module to find the repo root holding the CLI file. Used
    when running from a source tree (editable install, tests) where the wheel's
    packaged copy does not exist.
    """
    for parent in Path(__file__).resolve().parents:
        candidate = parent / _CLI_SKILL_RELPATH
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    return None


def load_skill() -> str:
    """Load the onboarding skill markdown (cached for the process lifetime).

    Prefers the wheel-packaged copy, falling back to the CLI source file in a
    checkout. Raises ``FileNotFoundError`` if neither exists, which is a build/
    packaging error rather than a runtime input problem.
    """
    global _cached_skill
    if _cached_skill is not None:
        return _cached_skill
    content = _read_packaged_skill() or _read_source_skill()
    if content is None:
        raise FileNotFoundError(
            "onboarding skill not found: expected the wheel-packaged "
            "jentic_one/skills/jentic.md or the source cli/internal/skillgen/"
            "content/jentic.md"
        )
    _cached_skill = content
    return content


def get_skill_router() -> APIRouter:
    """Router exposing the public, schema-hidden onboarding skill."""
    router = APIRouter()

    async def _serve_skill() -> Response:
        return Response(content=load_skill(), media_type=SKILL_MEDIA_TYPE)

    router.add_api_route(
        SKILL_PATH,
        _serve_skill,
        methods=["GET"],
        include_in_schema=False,
    )
    router.add_api_route(
        SKILL_ALIAS_PATH,
        _serve_skill,
        methods=["GET"],
        include_in_schema=False,
    )

    return router
