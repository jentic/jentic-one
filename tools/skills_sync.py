"""Mirror the human-authored skill set into the two ``content/`` copies.

The single source of truth for every *served* skill is ``skills/<name>/SKILL.md``
at the repo root, plus its optional ``skills/<name>/references/*.md`` files
(level-3 progressive disclosure — lane/variant material the router SKILL.md
points at). Two byte-identical copies of each must exist so both delivery
surfaces can ship them:

- ``cli/internal/skillgen/content/<name>.md`` (+ ``content/<name>/references/``)
  — embedded into the Go binary via
  ``//go:embed content/*.md content/*/references/*.md`` (``go:embed`` cannot
  reach outside the Go module).
- ``src/jentic_one/shared/web/content/<name>.md`` (+ ``content/<name>/references/``)
  — shipped in the Python wheel
  (the wheel cannot include files outside ``src/jentic_one``) and served raw at
  ``GET /skills/<name>.md`` / ``GET /skills/<name>/references/<file>``.

Because ``go:embed`` and the wheel each need a copy *inside their own tree*, a
single shared file is impossible; this generator keeps the copies in lockstep
from the one source, and ``tests/arch/test_skill_drift.py`` fails CI if they
ever drift.

Run ``python -m tools.skills_sync`` to regenerate the copies (``make skills``),
or ``python -m tools.skills_sync --check`` to verify they are up to date without
writing (used by the arch test).

``SERVED_SKILLS`` is the canonical served set. ``init-design`` lives under
``skills/`` but is a human design-workflow doc, not an agent-facing flow skill,
so it is deliberately excluded — it is not mirrored, served, or validated here.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

#: The served skill set (single source of truth, shared with the backend
#: allowlist and the drift test). ``init-design`` is intentionally excluded.
SERVED_SKILLS: tuple[str, ...] = ("jentic", "contribute-spec-fix", "import-new-api")

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "skills"
CLI_CONTENT = REPO_ROOT / "cli" / "internal" / "skillgen" / "content"
WEB_CONTENT = REPO_ROOT / "src" / "jentic_one" / "shared" / "web" / "content"

#: Agent Skills ``name`` grammar (Anthropic spec): 1-64 chars, lowercase
#: alphanumerics and single interior hyphens, no leading/trailing hyphen.
NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$")

#: Reference filename grammar: the skill-name stem grammar + ``.md``. A skill's
#: optional ``skills/<name>/references/*.md`` files are level-3 progressive
#: disclosure (plain markdown, NO frontmatter), mirrored verbatim into
#: ``content/<name>/references/`` in both trees and served/embedded alongside
#: the skill.
REFERENCE_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?\.md$")

#: SKILL.md size discipline (Agent Skills progressive-disclosure guidance):
#: a router SKILL.md must stay under 500 lines — split lane/variant material
#: into ``references/`` (as the ``jentic`` skill does) once it grows. Warn
#: early at 300 so growth is visible before it becomes a failure.
SKILL_MAX_LINES = 500
SKILL_WARN_LINES = 300

#: Grandfathered pre-existing oversize skills: name -> its frozen line count.
#: ``contribute-spec-fix`` predates the line-count rule at 503 lines; it may
#: not grow past its recorded size (any growth must instead split it into
#: references). Do NOT add new entries — split the skill instead.
SKILL_LINE_EXEMPTIONS: dict[str, int] = {"contribute-spec-fix": 503}

#: BaseURL is a *render-time* concern (the CLI interpolates the control-plane
#: URL when it renders; the backend serves the file raw). A placeholder in the
#: source would therefore leak literally on the served surface and break the
#: byte-identity invariant, so the source files must never contain one.
_BASEURL_PLACEHOLDERS = ("{{baseurl}}", "{{ base_url }}", "{baseurl}", "{base_url}")


class SkillError(Exception):
    """A served skill's source is missing or violates the frontmatter contract."""


def _source_path(name: str) -> Path:
    return SKILLS_DIR / name / "SKILL.md"


def _parse_frontmatter(text: str, name: str) -> dict[str, str]:
    """Extract simple ``key: value`` frontmatter pairs; validate name/description.

    Only the flat top-level scalars we care about (``name``, ``description``,
    ``version``) are read — nested keys like ``metadata:`` are ignored here; the
    Go parser owns the full structure. This is enough to enforce the spec limits
    the served set must satisfy.
    """
    if not text.startswith("---\n"):
        raise SkillError(f"{name}: SKILL.md must start with YAML frontmatter (---)")
    end = text.find("\n---", 4)
    if end < 0:
        raise SkillError(f"{name}: unterminated frontmatter")
    fm: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if line.startswith((" ", "\t")) or ":" not in line:
            continue  # nested (metadata:) or continuation — not a top-level scalar
        key, _, value = line.partition(":")
        fm[key.strip()] = value.strip()
    return fm


def _validate(name: str, text: str) -> None:
    """Fail closed if a served skill violates the Agent Skills frontmatter spec."""
    if not NAME_RE.match(name):
        raise SkillError(
            f"{name!r} is not a valid skill name "
            "(1-64 chars, lowercase [a-z0-9-], no leading/trailing hyphen)"
        )
    fm = _parse_frontmatter(text, name)
    fm_name = fm.get("name", "")
    if fm_name != name:
        raise SkillError(f"{name}: frontmatter name={fm_name!r} must match the directory name")
    desc = fm.get("description", "")
    if not (1 <= len(desc) <= 1024):
        raise SkillError(
            f"{name}: description must be 1-1024 chars (Agent Skills spec), got {len(desc)}"
        )
    lowered = text.lower()
    for ph in _BASEURL_PLACEHOLDERS:
        if ph in lowered:
            raise SkillError(
                f"{name}: source contains a BaseURL placeholder {ph!r}; BaseURL is a "
                "render-time concern (CLI-only) and must never appear in the file"
            )
    lines = text.count("\n") + (0 if text.endswith("\n") else 1)
    ceiling = SKILL_LINE_EXEMPTIONS.get(name, SKILL_MAX_LINES)
    if lines > ceiling:
        raise SkillError(
            f"{name}: SKILL.md is {lines} lines (max {ceiling}); split lane/variant "
            "material into skills/<name>/references/*.md (progressive disclosure)"
        )
    if lines > SKILL_WARN_LINES and name not in SKILL_LINE_EXEMPTIONS:
        print(
            f"skills_sync: warning: {name}: SKILL.md is {lines} lines "
            f"(> {SKILL_WARN_LINES}); consider splitting into references/ before it "
            f"reaches the {SKILL_MAX_LINES}-line ceiling",
            file=sys.stderr,
        )


def _references_dir(name: str) -> Path:
    return SKILLS_DIR / name / "references"


def source_references(name: str) -> tuple[str, ...]:
    """The reference filenames authored for one skill, sorted; empty when none.

    Filenames are validated against :data:`REFERENCE_RE` — a bad name is a
    :class:`SkillError`, not a silent skip, so a typo cannot quietly drop a
    reference from every served surface.
    """
    ref_dir = _references_dir(name)
    if not ref_dir.is_dir():
        return ()
    names: list[str] = []
    for entry in sorted(ref_dir.iterdir()):
        if not entry.is_file():
            continue
        if not REFERENCE_RE.fullmatch(entry.name):
            raise SkillError(
                f"{name}: reference {entry.name!r} violates the filename grammar "
                "(lowercase [a-z0-9-] stem + .md, no leading/trailing hyphen)"
            )
        names.append(entry.name)
    return tuple(names)


def _targets(name: str) -> tuple[Path, Path]:
    return CLI_CONTENT / f"{name}.md", WEB_CONTENT / f"{name}.md"


def _reference_target_dirs(name: str) -> tuple[Path, Path]:
    return CLI_CONTENT / name / "references", WEB_CONTENT / name / "references"


def _mirror_file(target: Path, payload: bytes, *, check: bool, problems: list[str]) -> None:
    """Write (or, with ``check``, verify) one mirrored file."""
    if check:
        if not target.is_file() or target.read_bytes() != payload:
            problems.append(f"out of date: {target.relative_to(REPO_ROOT)} (run `make skills`)")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)


def _prune_orphan_references(
    name: str, wanted: tuple[str, ...], *, check: bool, problems: list[str]
) -> None:
    """Remove (or flag) mirrored reference files whose source no longer exists.

    A reference deleted from ``skills/<name>/references/`` must disappear from
    both mirror trees too — a stale mirror would keep serving/embedding a
    document the source no longer owns.
    """
    for ref_dir in _reference_target_dirs(name):
        if not ref_dir.is_dir():
            continue
        for entry in sorted(ref_dir.iterdir()):
            if entry.is_file() and entry.name not in wanted:
                if check:
                    problems.append(
                        f"orphaned mirror: {entry.relative_to(REPO_ROOT)} "
                        "(source reference removed; run `make skills`)"
                    )
                else:
                    entry.unlink()
        if not check and not any(ref_dir.iterdir()):
            ref_dir.rmdir()
            if not any(ref_dir.parent.iterdir()):
                ref_dir.parent.rmdir()


def sync(*, check: bool) -> int:
    """Mirror (or, with ``check``, verify) each served skill into both copies.

    A skill is its ``SKILL.md`` (mirrored flat as ``content/<name>.md``) plus
    any ``references/*.md`` (mirrored as ``content/<name>/references/<file>``
    in both trees). Returns a process exit code: 0 when everything is in sync
    (or was written), 1 when ``check`` finds a stale/mismatched copy or a
    validation failure.
    """
    problems: list[str] = []
    for name in SERVED_SKILLS:
        src = _source_path(name)
        if not src.is_file():
            problems.append(f"missing source: {src}")
            continue
        text = src.read_text(encoding="utf-8")
        try:
            _validate(name, text)
            references = source_references(name)
        except SkillError as exc:
            problems.append(str(exc))
            continue
        payload = text.encode("utf-8")
        for target in _targets(name):
            _mirror_file(target, payload, check=check, problems=problems)
        for ref in references:
            ref_payload = (_references_dir(name) / ref).read_bytes()
            for ref_dir in _reference_target_dirs(name):
                _mirror_file(ref_dir / ref, ref_payload, check=check, problems=problems)
        _prune_orphan_references(name, references, check=check, problems=problems)

    if problems:
        for p in problems:
            print(f"skills_sync: {p}", file=sys.stderr)
        return 1
    if not check:
        print(f"skills_sync: mirrored {len(SERVED_SKILLS)} skills into both content/ dirs")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the copies are up to date without writing (exit 1 if stale)",
    )
    args = parser.parse_args()
    return sync(check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
