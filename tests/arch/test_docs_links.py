"""Drift guards for the markdown documentation tree.

Two failure modes rot a docs tree that nothing else catches:

- **Dead relative links.** A rename or removal leaves ``[text](old/path.md)``
  pointing at nothing. ``test_agent_docs_refs.py`` guards the two repo-root
  agent docs; this gate extends the same check to every tracked markdown file
  so no doc can ship a link (or image) to a path that does not exist.
- **Orphan docs.** A doc that no index links is invisible to readers who
  navigate from ``docs/README.md`` — it exists but is unreachable. The orphan
  gate walks the relative-link graph from ``docs/README.md`` and fails on any
  ``docs/**/*.md`` the traversal never reaches.

Both gates guard *referential* facts only — link targets, never prose. A
third gate validates anchors: every ``#fragment`` on a relative markdown
link (and every same-file ``(#…)`` link) must name a real heading in the
target file, using GitHub's slug rules, so a heading rename fails CI instead
of silently rotting every table-of-contents and cross-file section link.
"""

from __future__ import annotations

import re
import subprocess
from collections import deque
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

DOCS_ROOT = REPO_ROOT / "docs"

DOCS_INDEX = DOCS_ROOT / "README.md"

#: Directories that hold third-party or generated markdown, not our docs.
#: (Fallback filter — the primary enumeration is `git ls-files`.)
_EXCLUDED_DIR_NAMES = {".git", ".venv", "node_modules", "dist", ".ruff_cache", ".pytest_cache"}

#: Generated mirrors of ``skills/<name>/SKILL.md`` (see ``test_skill_drift.py``):
#: ``make skills`` keeps them byte-identical to their source, so their relative
#: links resolve from the *source's* directory (``skills/<name>/``), not their
#: own. The sources are checked; the by-design mirrors are exempt.
_SKILL_MIRROR_DIRS = frozenset(
    {
        REPO_ROOT / "cli" / "internal" / "skillgen" / "content",
        REPO_ROOT / "src" / "jentic_one" / "shared" / "web" / "content",
    }
)

#: Relative markdown link and image targets, e.g. ``[text](../guides/x.md)``
#: and ``![alt](img/diagram.png)``. Mirrors ``_MD_LINK_RE`` in
#: ``test_agent_docs_refs.py``: external (http/https/mailto), pure-anchor
#: (``#…``) and root-absolute (``/app`` — server routes, not files) targets
#: are excluded, and a trailing ``#fragment`` is stripped, not validated.
_MD_LINK_RE = re.compile(r"\]\((?!https?://|#|mailto:|/)([\w./-]+?)(?:#[^)]*)?\)")

#: The same relative targets, but only when they carry a ``#fragment`` —
#: captured separately so the fragment can be validated against the target
#: file's headings.
_MD_LINK_WITH_FRAGMENT_RE = re.compile(r"\]\((?!https?://|#|mailto:|/)([\w./-]+?)#([^)]+)\)")

#: Same-file anchor links, e.g. ``[contract](#the-contract)``.
_SAME_FILE_ANCHOR_RE = re.compile(r"\]\(#([^)]+)\)")

_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$")

#: Inline links inside a heading slug from their rendered text, not the URL.
_INLINE_LINK_TEXT_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")

#: GitHub's slugger drops everything except word characters (letters, digits,
#: underscore), whitespace, and hyphens, then maps each whitespace char to a
#: hyphen.
_NON_SLUG_CHARS_RE = re.compile(r"[^\w\s-]")


def _tracked_markdown_files() -> list[Path]:
    """Tracked ``*.md`` files, excluding the by-design skill mirrors.

    Enumerates via ``git ls-files`` so untracked scratch files never trip the
    guard; falls back to an rglob when git is unavailable (e.g. an sdist).
    """
    docs: list[Path] = []
    try:
        tracked = subprocess.run(
            ["git", "ls-files", "-z", "*.md"],
            cwd=REPO_ROOT,
            capture_output=True,
            check=True,
            text=True,
        ).stdout
        docs.extend(REPO_ROOT / name for name in tracked.split("\0") if name)
    except (OSError, subprocess.CalledProcessError):
        for path in REPO_ROOT.rglob("*.md"):
            if _EXCLUDED_DIR_NAMES.isdisjoint(part.lower() for part in path.parts):
                docs.append(path)
    return sorted(doc for doc in docs if doc.is_file() and doc.parent not in _SKILL_MIRROR_DIRS)


def _relative_targets(doc: Path) -> list[tuple[int, str]]:
    """``(lineno, target)`` for every relative link/image target in *doc*.

    Fenced code blocks are skipped: links there are examples, not references
    (they neither render nor navigate), so they must not be validated and
    must not count toward reachability.
    """
    targets: list[tuple[int, str]] = []
    in_fence = False
    for lineno, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for target in _MD_LINK_RE.findall(line):
            targets.append((lineno, target))
    return targets


def _tracked_paths() -> tuple[frozenset[Path], frozenset[Path]]:
    """All git-tracked files, plus every directory holding one.

    Target existence is judged against the *tracked* tree, not the local
    filesystem: a link to a gitignored file, or a wrong-case link on a
    case-insensitive filesystem, must fail here rather than only on a clean
    CI checkout. Falls back to empty sets when git is unavailable (callers
    then fall back to a filesystem check).
    """
    try:
        tracked = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=REPO_ROOT,
            capture_output=True,
            check=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return frozenset(), frozenset()
    files = frozenset(REPO_ROOT / name for name in tracked.split("\0") if name)
    dirs: set[Path] = set()
    for file in files:
        dirs.update(file.parents)
    return files, frozenset(dirs)


def _resolve(doc: Path, target: str) -> Path:
    """The filesystem path *target* names, resolved from *doc*'s directory."""
    return (doc.parent / target).resolve()


def _heading_slugs(doc: Path) -> frozenset[str]:
    """GitHub-style anchor slugs for every markdown heading in *doc*.

    Mirrors github-slugger: lowercase the rendered heading text, drop
    everything except word characters / whitespace / hyphens, map each
    whitespace character to a hyphen, and suffix duplicates ``-1``, ``-2``, …
    """
    counts: dict[str, int] = {}
    slugs: set[str] = set()
    in_fence = False
    for line in doc.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _HEADING_RE.match(line)
        if not match:
            continue
        text = _INLINE_LINK_TEXT_RE.sub(r"\1", match.group(1))
        slug = _NON_SLUG_CHARS_RE.sub("", text.lower())
        slug = re.sub(r"\s", "-", slug)
        n = counts.get(slug, 0)
        counts[slug] = n + 1
        slugs.add(slug if n == 0 else f"{slug}-{n}")
    return frozenset(slugs)


def _fragment_targets(doc: Path) -> list[tuple[int, Path, str]]:
    """``(lineno, target_file, fragment)`` for every anchored link in *doc*.

    Covers cross-file anchors (``other.md#section``) and same-file anchors
    (``#section``, resolved against *doc* itself). Fenced code blocks are
    skipped, matching :func:`_relative_targets`.
    """
    anchored: list[tuple[int, Path, str]] = []
    in_fence = False
    for lineno, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for target, fragment in _MD_LINK_WITH_FRAGMENT_RE.findall(line):
            anchored.append((lineno, _resolve(doc, target), fragment))
        for fragment in _SAME_FILE_ANCHOR_RE.findall(line):
            anchored.append((lineno, doc, fragment))
    return anchored


@pytest.mark.arch
def test_markdown_relative_links_resolve() -> None:
    """Every relative link or image in every tracked markdown file must resolve.

    Targets are resolved against the linking file's own directory (standard
    markdown semantics) and must name a git-tracked file or directory, so a
    moved or deleted file fails here with the file:line of every stale
    reference instead of shipping a dead link.
    """
    tracked_files, tracked_dirs = _tracked_paths()
    violations: list[str] = []
    for doc in _tracked_markdown_files():
        for lineno, target in _relative_targets(doc):
            resolved = _resolve(doc, target)
            if tracked_files or tracked_dirs:
                exists = resolved in tracked_files or resolved in tracked_dirs
            else:
                exists = resolved.exists()
            if not exists:
                violations.append(
                    f"{doc.relative_to(REPO_ROOT)}:{lineno} — links to {target!r}, which "
                    "is not a tracked file or directory (fix the link or restore the file)"
                )
    assert not violations, "Markdown files link to missing paths:\n" + "\n".join(violations)


@pytest.mark.arch
def test_markdown_anchor_links_resolve() -> None:
    """Every ``#fragment`` on a markdown link must name a real heading.

    Cross-file anchors are validated against the target file's headings and
    same-file anchors against the linking file's own, so a heading rename
    fails here with the file:line of every link still pointing at the old
    slug. Fragments are only checked when the target is a tracked markdown
    file (missing files are the link gate's job) and skipped when
    URL-encoded (``%``) — none exist today and decoding them is not worth
    the complexity.
    """
    slug_cache: dict[Path, frozenset[str]] = {}
    violations: list[str] = []
    for doc in _tracked_markdown_files():
        for lineno, target_file, fragment in _fragment_targets(doc):
            if target_file.suffix != ".md" or not target_file.is_file() or "%" in fragment:
                continue
            if target_file not in slug_cache:
                slug_cache[target_file] = _heading_slugs(target_file)
            if fragment not in slug_cache[target_file]:
                violations.append(
                    f"{doc.relative_to(REPO_ROOT)}:{lineno} — anchor "
                    f"{'#' + fragment!r} does not match any heading in "
                    f"{target_file.relative_to(REPO_ROOT)} (heading renamed? "
                    "update the link)"
                )
    assert not violations, "Markdown anchors point at missing headings:\n" + "\n".join(violations)


@pytest.mark.arch
def test_every_doc_is_reachable_from_the_docs_index() -> None:
    """Every ``docs/**/*.md`` must be reachable from ``docs/README.md``.

    Reachability follows relative links transitively through ``docs/`` (a
    README-per-folder chain satisfies it — e.g. the generated files under
    ``docs/reference/`` are reachable through that folder's README). A doc the
    traversal never reaches is invisible to anyone navigating from the index;
    this fails on it instead of letting it rot unlinked.
    """
    assert DOCS_INDEX.is_file(), f"{DOCS_INDEX.relative_to(REPO_ROOT)} is missing"

    all_docs = {doc for doc in _tracked_markdown_files() if DOCS_ROOT in doc.parents}

    reachable: set[Path] = set()
    queue: deque[Path] = deque([DOCS_INDEX])
    while queue:
        doc = queue.popleft()
        if doc in reachable:
            continue
        reachable.add(doc)
        for _, target in _relative_targets(doc):
            resolved = _resolve(doc, target)
            # A directory link reads as a link to its README on GitHub.
            if resolved.is_dir():
                resolved = resolved / "README.md"
            if (
                DOCS_ROOT in resolved.parents
                and resolved.suffix == ".md"
                and resolved.is_file()
                and resolved not in reachable
            ):
                queue.append(resolved)

    orphans = sorted(all_docs - reachable)
    violations = [
        f"{doc.relative_to(REPO_ROOT)} — not reachable from docs/README.md "
        "(link it from its section README or docs/README.md)"
        for doc in orphans
    ]
    assert not violations, "Orphaned docs (unlinked from the index):\n" + "\n".join(violations)
