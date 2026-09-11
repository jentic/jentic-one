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
import tools.skills_sync as skills_sync
from tools.skills_sync import (
    CLI_CONTENT,
    SERVED_SKILLS,
    SKILL_MAX_LINES,
    WEB_CONTENT,
    SkillError,
    _source_path,
    _validate,
    source_references,
    sync,
)

#: Every (skill, reference) pair the source tree ships, for parametrization.
_REFERENCE_PAIRS = [(name, ref) for name in SERVED_SKILLS for ref in source_references(name)]


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


@pytest.mark.arch
@pytest.mark.parametrize(("name", "ref"), _REFERENCE_PAIRS)
def test_each_reference_is_byte_identical_across_copies(name: str, ref: str) -> None:
    """Reference files are pinned across the triangle exactly like skill bytes."""
    source = (_source_path(name).parent / "references" / ref).read_bytes()
    cli_copy = CLI_CONTENT / name / "references" / ref
    web_copy = WEB_CONTENT / name / "references" / ref
    assert cli_copy.read_bytes() == source, f"{cli_copy} drifted from source (run `make skills`)"
    assert web_copy.read_bytes() == source, f"{web_copy} drifted from source (run `make skills`)"


@pytest.mark.arch
def test_jentic_ships_the_lane_references() -> None:
    """The jentic skill ships the three lane files; cli.md is in BOTH mirrors.

    ``cli.md`` is deliberately present in the mirror trees (HTTP serves every
    reference — the raw neutral channel) even though the MCP resource listings
    skip it (``CLI_ONLY_REFERENCES`` / ``skillgen.CLIOnlyReference``); the
    lane filter is a serving decision at the MCP doors, never a mirroring one.
    """
    assert source_references("jentic") == ("cli.md", "mcp.md", "recovery.md")
    for tree in (CLI_CONTENT, WEB_CONTENT):
        assert (tree / "jentic" / "references" / "cli.md").is_file()


@pytest.mark.arch
@pytest.mark.parametrize(("name", "ref"), _REFERENCE_PAIRS)
def test_references_are_plain_markdown_with_h1(name: str, ref: str) -> None:
    """References carry NO frontmatter and open with a one-line H1.

    They are level-3 progressive disclosure, not skills: the Agent-Skills spec
    requires frontmatter on SKILL.md only, and the opening "read this when…"
    H1 is what orients a model landing on the file cold.
    """
    text = (_source_path(name).parent / "references" / ref).read_text(encoding="utf-8")
    assert not text.lstrip("\n").startswith("---"), f"{name}/{ref} must not carry frontmatter"
    assert text.lstrip("\n").startswith("# "), f"{name}/{ref} must open with an H1"


@pytest.mark.arch
def test_validate_enforces_the_line_ceiling() -> None:
    """A SKILL.md over the 500-line ceiling fails validation (fail closed).

    ``contribute-spec-fix`` is grandfathered at its frozen pre-rule size; any
    growth past that trips the error too, so the exemption cannot rot into a
    loophole.
    """
    fm = "---\nname: jentic\ndescription: d\n---\n"
    oversize = fm + "line\n" * (SKILL_MAX_LINES + 1)
    with pytest.raises(SkillError, match="lines"):
        _validate("jentic", oversize)
    # The grandfathered skill still validates at its current size…
    _validate("contribute-spec-fix", _source_path("contribute-spec-fix").read_text("utf-8"))
    # …but may not grow.
    grown = "---\nname: contribute-spec-fix\ndescription: d\n---\n" + "line\n" * 600
    with pytest.raises(SkillError, match="lines"):
        _validate("contribute-spec-fix", grown)


@pytest.mark.arch
def test_source_references_rejects_bad_filenames(tmp_path, monkeypatch) -> None:
    """A reference violating the filename grammar is an error, not a skip."""
    ref_dir = tmp_path / "bad-skill" / "references"
    ref_dir.mkdir(parents=True)
    (ref_dir / "Bad_Name.md").write_text("# nope\n", encoding="utf-8")
    monkeypatch.setattr(skills_sync, "SKILLS_DIR", tmp_path)
    with pytest.raises(SkillError, match="filename grammar"):
        skills_sync.source_references("bad-skill")


@pytest.mark.arch
def test_source_references_rejects_directories(tmp_path, monkeypatch) -> None:
    """A directory under references/ is an error (fail closed), never a skip.

    The mirrors and serving surfaces are flat (one level of references/*.md);
    silently skipping a stray subdirectory would quietly drop whatever was
    authored inside it from every surface.
    """
    ref_dir = tmp_path / "bad-skill" / "references"
    (ref_dir / "nested").mkdir(parents=True)
    monkeypatch.setattr(skills_sync, "SKILLS_DIR", tmp_path)
    with pytest.raises(SkillError, match="non-file entry"):
        skills_sync.source_references("bad-skill")


@pytest.mark.arch
def test_prune_orphan_references_removes_stale_mirrors(tmp_path, monkeypatch) -> None:
    """A mirrored reference whose source is gone is flagged (check) or deleted (write).

    A stale mirror would keep serving/embedding a document the source no
    longer owns, so the prune must fire in both trees.
    """
    cli = tmp_path / "cli" / "content"
    web = tmp_path / "web" / "content"
    for tree in (cli, web):
        ref_dir = tree / "some-skill" / "references"
        ref_dir.mkdir(parents=True)
        (ref_dir / "kept.md").write_text("# keep\n", encoding="utf-8")
        (ref_dir / "orphan.md").write_text("# stale\n", encoding="utf-8")
    monkeypatch.setattr(skills_sync, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(skills_sync, "CLI_CONTENT", cli)
    monkeypatch.setattr(skills_sync, "WEB_CONTENT", web)

    # Check mode flags the orphans (one per tree) without deleting anything.
    problems: list[str] = []
    skills_sync._prune_orphan_references("some-skill", ("kept.md",), check=True, problems=problems)
    assert len(problems) == 2
    assert all("orphaned mirror" in p for p in problems)
    for tree in (cli, web):
        assert (tree / "some-skill" / "references" / "orphan.md").is_file()

    # Write mode deletes the orphans and keeps the wanted file in both trees.
    problems = []
    skills_sync._prune_orphan_references("some-skill", ("kept.md",), check=False, problems=problems)
    assert problems == []
    for tree in (cli, web):
        assert not (tree / "some-skill" / "references" / "orphan.md").exists()
        assert (tree / "some-skill" / "references" / "kept.md").is_file()
