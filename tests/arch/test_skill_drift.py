"""Drift guard: the served skill *set* stays in lockstep across its three copies.

Every *served* skill has a single human-authored source at
``skills/<name>/SKILL.md`` and two generated mirrors that must never diverge
from it (or from each other):

- ``cli/internal/skillgen/content/<name>.md`` — embedded in the Go CLI binary
  (``go:embed content/*.md``) and written into agent runtimes by
  ``jentic skill init`` / ``jentic setup``.
- ``src/jentic_one/shared/web/content/<name>.md`` — packaged with the backend
  and served raw at ``GET /skills/<name>.md`` (#651), referenced from
  ``GET /llms.txt`` (#809).

``go:embed`` cannot reach outside the Go module tree and the wheel cannot ship
files outside ``src/jentic_one``, so a single physical file is impossible; the
generator ``tools/skills_sync.py`` keeps the copies identical from the one
source, and this test is the seam that fails CI if they drift. If it fails, run
``make skills`` to regenerate the mirrors from ``skills/<name>/SKILL.md``.

The served set (and its validation) is owned by ``tools.skills_sync`` so the
CLI embed loader, the backend allowlist, and this test share one definition and
cannot diverge.
"""

from __future__ import annotations

import pytest
from tools.skills_sync import (
    CLI_CONTENT,
    SERVED_SKILLS,
    WEB_CONTENT,
    SkillError,
    _source_path,
    _validate,
    sync,
)


@pytest.mark.arch
def test_served_skill_set_is_mirrored_and_valid() -> None:
    """Each served skill is present, valid, and byte-identical across both copies.

    Delegates to the generator's own ``--check`` so the test and the tool can
    never disagree about what "in sync" means.
    """
    assert sync(check=True) == 0, (
        "The served skill set has drifted or a source is invalid.\n"
        "Run `make skills` to regenerate the mirrors from skills/<name>/SKILL.md."
    )


@pytest.mark.arch
@pytest.mark.parametrize("name", SERVED_SKILLS)
def test_each_served_skill_source_exists_and_validates(name: str) -> None:
    """Every served skill has a source SKILL.md that satisfies the frontmatter spec."""
    src = _source_path(name)
    assert src.is_file(), f"missing served skill source: {src}"
    try:
        _validate(name, src.read_text(encoding="utf-8"))
    except SkillError as exc:  # pragma: no cover - failure path is the assertion
        pytest.fail(str(exc))


@pytest.mark.arch
@pytest.mark.parametrize("name", SERVED_SKILLS)
def test_each_served_skill_is_byte_identical_across_copies(name: str) -> None:
    """The CLI-embedded and backend-served copies match the source byte-for-byte."""
    source = _source_path(name).read_bytes()
    cli_copy = CLI_CONTENT / f"{name}.md"
    web_copy = WEB_CONTENT / f"{name}.md"
    assert cli_copy.read_bytes() == source, f"{cli_copy} drifted from source (run `make skills`)"
    assert web_copy.read_bytes() == source, f"{web_copy} drifted from source (run `make skills`)"
