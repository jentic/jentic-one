"""Mirror the human-authored skill set into the two ``content/`` copies.

The single source of truth for every *served* skill is ``skills/<name>/SKILL.md``
at the repo root. Two byte-identical copies of each must exist so both delivery
surfaces can ship them:

- ``cli/internal/skillgen/content/<name>.md`` — embedded into the Go binary via
  ``//go:embed content/*.md`` (``go:embed`` cannot reach outside the Go module).
- ``src/jentic_one/shared/web/content/<name>.md`` — shipped in the Python wheel
  (the wheel cannot include files outside ``src/jentic_one``) and served raw at
  ``GET /skills/<name>.md``.

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


def _targets(name: str) -> tuple[Path, Path]:
    return CLI_CONTENT / f"{name}.md", WEB_CONTENT / f"{name}.md"


def sync(*, check: bool) -> int:
    """Mirror (or, with ``check``, verify) each served skill into both copies.

    Returns a process exit code: 0 when everything is in sync (or was written),
    1 when ``check`` finds a stale/mismatched copy or a validation failure.
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
        except SkillError as exc:
            problems.append(str(exc))
            continue
        payload = text.encode("utf-8")
        for target in _targets(name):
            if check:
                if not target.is_file() or target.read_bytes() != payload:
                    problems.append(
                        f"out of date: {target.relative_to(REPO_ROOT)} (run `make skills`)"
                    )
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)

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
