"""Git rules: the repository/ref selector a rule stores, and how it covers a git request.

A permission rule with ``match_mode="git"`` governs git smart-HTTP through the
broker's governed git pipeline (clone, fetch and push to github.com) instead
of matching a request path. Its ``path`` column holds the selector::

    <action>:<owner>/<repo>[:<ref glob>]

- ``action`` is ``fetch``, ``push`` or ``force``;
- ``owner`` and ``repo`` are GitHub names or globs (``*`` any run of
  characters, ``?`` one character), compared case-insensitively with any
  ``.git`` suffix removed;
- the optional ``ref glob`` starts with ``refs/heads/`` or ``refs/tags/``
  (``*`` matches any run of characters, ``/`` included; ``?`` one character
  other than ``/``). It is only valid on ``push`` and ``force``.

Examples: ``fetch:octo-org/*``, ``push:octo-org/app:refs/heads/agent/*``,
``force:octo-org/app:refs/heads/scratch/*``.

The actions are levels, ``fetch`` < ``push`` < ``force``: a fetch is a clone
or fetch (``git-upload-pack``); a push creates a ref or fast-forwards a
branch; a force is any other ref update, a deletion, or an update or deletion
of an existing tag (tags are immutable unless forced). An ``allow`` rule
covers its own level and every level below it, so a push rule also allows the
fetch git makes before a push (``push.negotiate``) and the clone it needs. A
``deny`` rule covers its own level and every level above it, so ``deny
push:…`` still lets the repository be fetched. A ``deny`` rule with a ref glob
only covers a request that names a ref (one ref update of a push); an
``allow`` rule's ref glob is ignored for a request that names none (a fetch,
or the ref advertisement before a push), so a push rule scoped to some
branches still lets git read the repository.

Git rules are deny-or-allow only: a held push would block git with no output
past an agent's tool timeout, so ``require-approval`` is refused at save time
and never matches at enforce time.

The selector lives in ``path`` with its own ``match_mode`` for the same reason
as a GraphQL selector (``shared.permissions.graphql_rules``): an evaluator that
does not know the mode compiles a non-empty ``path`` of an unknown mode to a
matcher that never matches, so a git ``allow`` rule grants nothing there.

Pure module: no FastAPI, httpx or DB imports.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

#: The ``match_mode`` of a git rule.
GIT_MATCH_MODE = "git"

#: Cap on the stored selector, matching the ``VARCHAR(1000)`` ``path`` column.
MAX_SELECTOR_LENGTH = 1000

#: The ref namespaces a push may update: branches and tags.
PUSHABLE_REF_PREFIXES: tuple[str, ...] = ("refs/heads/", "refs/tags/")


class GitAction(StrEnum):
    """What a git request does to a repository, as a rule level (lowest first)."""

    FETCH = "fetch"
    PUSH = "push"
    FORCE = "force"


_LEVEL: dict[GitAction, int] = {GitAction.FETCH: 0, GitAction.PUSH: 1, GitAction.FORCE: 2}

# GitHub owner and repository names: letters, digits, '-', '_', '.', plus the
# glob characters. The repository's ``.git`` suffix is stripped before matching.
_NAME_GLOB_RE = re.compile(r"[A-Za-z0-9._*?-]+")
# A ref glob: printable, no whitespace, no characters git forbids in a refname
# other than the glob characters themselves.
_REF_GLOB_RE = re.compile(r"refs/(heads|tags)/[^\s~^:\\\[\x00-\x1f\x7f]+")


@dataclass(frozen=True, slots=True)
class GitItem:
    """One thing a git request does, as rules are evaluated on it.

    ``owner`` and ``repo`` are normalized (:func:`normalize_owner_name`,
    :func:`normalize_repo_name`).
    ``ref`` is the full ref name a push updates, or ``None`` for a request that
    names no ref (a fetch, or the ref advertisement before a push).
    """

    action: GitAction
    owner: str
    repo: str
    ref: str | None = None


@dataclass(frozen=True, slots=True)
class GitSelector:
    """A compiled git rule selector."""

    action: GitAction
    owner: re.Pattern[str]
    repo: re.Pattern[str]
    ref: re.Pattern[str] | None = None

    def covers(self, item: GitItem, *, effect: str) -> bool:
        """Whether a rule with this selector and ``effect`` covers ``item`` (see the module doc)."""
        if self.owner.fullmatch(item.owner) is None or self.repo.fullmatch(item.repo) is None:
            return False
        effect = effect.lower()
        if effect == "allow":
            if _LEVEL[item.action] > _LEVEL[self.action]:
                return False
            return item.ref is None or self.ref is None or self.ref.fullmatch(item.ref) is not None
        if effect == "deny":
            if _LEVEL[item.action] < _LEVEL[self.action]:
                return False
            if self.ref is None:
                return True
            return item.ref is not None and self.ref.fullmatch(item.ref) is not None
        # ``require-approval`` (refused at save time) or an unknown effect.
        return False


def normalize_repo_name(name: str) -> str:
    """A GitHub owner or repository name as rules compare it: lowercased, ``.git`` removed."""
    lowered = name.strip().lower()
    return lowered.removesuffix(".git") if lowered.endswith(".git") else lowered


def normalize_owner_name(name: str) -> str:
    """A GitHub owner name as rules compare it: lowercased."""
    return name.strip().lower()


def _name_glob(normalized: str) -> re.Pattern[str]:
    pattern = "".join(
        ".*" if ch == "*" else "." if ch == "?" else re.escape(ch) for ch in normalized
    )
    return re.compile(pattern)


def _ref_glob(raw: str) -> re.Pattern[str]:
    pattern = "".join(".*" if ch == "*" else "[^/]" if ch == "?" else re.escape(ch) for ch in raw)
    return re.compile(pattern)


def selector_error(raw: str | None) -> str | None:
    """Why ``raw`` is not a valid git selector, or ``None`` when it is."""
    if raw is None or raw == "":
        return (
            "a 'git' rule needs a selector in 'path': '<fetch|push|force>:<owner>/<repo>' "
            "with an optional ':refs/heads/<glob>' or ':refs/tags/<glob>' on push and force"
        )
    if len(raw) > MAX_SELECTOR_LENGTH:
        return f"path exceeds maximum length ({MAX_SELECTOR_LENGTH} characters)"
    action, _, rest = raw.partition(":")
    if action not in {a.value for a in GitAction}:
        return f"unknown git action {action!r}; expected one of {[a.value for a in GitAction]}"
    repo_part, separator, ref_part = rest.partition(":")
    owner, slash, repo = repo_part.partition("/")
    if not slash or not owner or not repo or "/" in repo:
        return "the repository must be '<owner>/<repo>', e.g. 'push:octo-org/app'"
    if _NAME_GLOB_RE.fullmatch(owner) is None or _NAME_GLOB_RE.fullmatch(repo) is None:
        return (
            "owner and repository may only use letters, digits, '-', '_', '.' and the "
            "globs '*' and '?'"
        )
    if not separator:
        return None
    if action == GitAction.FETCH:
        return "a 'fetch' selector takes no ref: a fetch reads the whole repository"
    if _REF_GLOB_RE.fullmatch(ref_part) is None or ".." in ref_part:
        return (
            "the ref must be a glob under refs/heads/ or refs/tags/, e.g. "
            "'refs/heads/agent/*' or 'refs/tags/v*'"
        )
    return None


def parse_selector(raw: str | None) -> GitSelector | None:
    """Compile a stored selector; ``None`` when it is invalid (the caller fails closed)."""
    if raw is None or selector_error(raw) is not None:
        return None
    action, _, rest = raw.partition(":")
    repo_part, separator, ref_part = rest.partition(":")
    owner, _, repo = repo_part.partition("/")
    return GitSelector(
        action=GitAction(action),
        owner=_name_glob(normalize_owner_name(owner)),
        repo=_name_glob(normalize_repo_name(repo)),
        ref=_ref_glob(ref_part) if separator else None,
    )


__all__ = [
    "GIT_MATCH_MODE",
    "MAX_SELECTOR_LENGTH",
    "PUSHABLE_REF_PREFIXES",
    "GitAction",
    "GitItem",
    "GitSelector",
    "normalize_owner_name",
    "normalize_repo_name",
    "parse_selector",
    "selector_error",
]
