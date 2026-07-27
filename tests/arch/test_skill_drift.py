"""Drift guard: the served onboarding skill equals the CLI-embedded copy.

The canonical "how to use Jentic" skill exists in two places that must never
diverge:

- ``cli/internal/skillgen/content/jentic.md`` — embedded in the Go CLI binary
  (``go:embed``) and written into agent runtimes by ``jentic skill init`` /
  ``jentic bootstrap``.
- ``src/jentic_one/shared/web/content/jentic.md`` — packaged with the backend
  and served at ``GET /skills/jentic.md`` (#651), referenced from
  ``GET /llms.txt`` (#809).

``go:embed`` cannot reach outside the Go module tree and the wheel cannot ship
files outside ``src/jentic_one``, so a single physical file is not possible;
this test is the seam that keeps the two copies byte-identical. If it fails,
copy the edited file over the stale one — the CLI copy is edited first by
convention (skill content changes are CLI-reviewed), but either direction is
fine as long as they end up equal.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CLI_SKILL = REPO_ROOT / "cli" / "internal" / "skillgen" / "content" / "jentic.md"
SERVED_SKILL = REPO_ROOT / "src" / "jentic_one" / "shared" / "web" / "content" / "jentic.md"


@pytest.mark.arch
def test_served_skill_matches_cli_embedded_skill() -> None:
    """The backend-served skill must be byte-identical to the CLI embed."""
    assert CLI_SKILL.is_file(), f"missing CLI skill content: {CLI_SKILL}"
    assert SERVED_SKILL.is_file(), f"missing served skill content: {SERVED_SKILL}"
    if CLI_SKILL.read_bytes() != SERVED_SKILL.read_bytes():
        pytest.fail(
            "The onboarding skill has drifted between the CLI embed and the "
            "backend-served copy.\n"
            f"  CLI embed:  {CLI_SKILL}\n"
            f"  served:     {SERVED_SKILL}\n"
            "Fix: copy the edited file over the stale one so both are identical."
        )
