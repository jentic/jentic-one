"""CI pass markers: skip a CI job whose exact inputs already passed.

Every job listed in :data:`JOBS` declares its inputs as one or more
``dorny/paths-filter`` groups in ``.github/workflows/ci.yml`` (the ``changes``
job's ``filters`` block, the same globs that already decide whether a PR runs
the job). The *inputs hash* of a job is the SHA-256 of the ``git ls-files -s``
lines (mode, blob SHA, path) of every tracked file those globs match, plus the
workflow, the composite actions, this module, and the runner image version.

When a job passes, it saves an empty Actions cache entry under
``ci-pass-<job>-<hash>``. The ``changes`` job runs :func:`lookup` first: when a
marker for the current hash exists on a ref this run trusts, the job is
skipped. Trusted refs are the run's own ref and the default branch (only runs
on the default branch can write there). So a re-push or rebase of a PR that
left a job's inputs unchanged skips it, and a push to ``main`` that didn't
change a job's inputs since that job last passed on ``main`` (a docs-only or
UI-only merge, for the backend jobs) skips it. A PR's markers never satisfy
``main``: they live on the PR's ref. Nightly (``schedule``), manual
(``workflow_dispatch``) and release (``CI_FULL_RUN``) runs never skip.

Lookups fail open: any API or git error means "run the job".

Usage (from the repository root)::

    python tools/ci_pass_markers.py lookup   # writes `skips` / `keys` to $GITHUB_OUTPUT
    python tools/ci_pass_markers.py hash test-unit
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from functools import cache
from pathlib import Path

import yaml

WORKFLOW = Path(".github/workflows/ci.yml")

#: Job (or matrix leg) name -> the paths-filter groups that are its inputs.
#: Names are the marker ids ci.yml reads back from ``skips`` / ``keys``.
JOBS: Mapping[str, Sequence[str]] = {
    "test-unit": ("unit-tests",),
    "test-arch": ("unit-tests",),
    "test-integration-postgres": ("integration-tests",),
    "test-integration-sqlite": ("integration-tests",),
    "ui": ("ui",),
    "ui-test-1": ("ui",),
    "ui-test-2": ("ui",),
    "ui-test-3": ("ui",),
    "ui-e2e": ("ui",),
    "cli": ("cli",),
}

#: Globs every job's hash includes: a change to how CI runs invalidates every marker.
COMMON_INPUTS: Sequence[str] = (
    ".github/workflows/ci.yml",
    ".github/actions/**",
    "tools/ci_pass_markers.py",
)

#: Events that always run every job (the backstop for any input a filter misses).
NEVER_SKIP_EVENTS = frozenset({"schedule", "workflow_dispatch"})

KEY_PREFIX = "ci-pass"


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a paths-filter (picomatch) glob into an anchored regex.

    ``**`` spans directories (``a/**/b`` also matches ``a/b``), ``*`` and ``?``
    stay within one path segment. Every other character is literal.
    """
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def load_filters(workflow: Path = WORKFLOW) -> dict[str, list[str]]:
    """The ``changes`` job's paths-filter groups: group name -> globs."""
    doc = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    for step in doc["jobs"]["changes"]["steps"]:
        if str(step.get("uses", "")).startswith("dorny/paths-filter"):
            filters = yaml.safe_load(step["with"]["filters"])
            return {name: [str(g) for g in globs] for name, globs in filters.items()}
    raise ValueError(f"no dorny/paths-filter step in the changes job of {workflow}")


@cache
def _tracked_files() -> tuple[str, ...]:
    """``git ls-files -s`` lines (``<mode> <sha> <stage>\\t<path>``) for HEAD's index."""
    result = subprocess.run(["git", "ls-files", "-s"], check=True, capture_output=True, text=True)
    return tuple(result.stdout.splitlines())


def inputs_hash(globs: Iterable[str], tracked: Iterable[str], salt: str = "") -> str:
    """SHA-256 over the tracked-file lines whose path matches any of ``globs``."""
    patterns = [glob_to_regex(g) for g in globs]
    digest = hashlib.sha256(salt.encode())
    for line in sorted(tracked):
        path = line.split("\t", 1)[1]
        if any(p.match(path) for p in patterns):
            digest.update(line.encode())
            digest.update(b"\n")
    return digest.hexdigest()


def job_hash(job: str, filters: Mapping[str, Sequence[str]]) -> str:
    """The inputs hash of ``job`` (its filter groups + :data:`COMMON_INPUTS`)."""
    globs = [g for group in JOBS[job] for g in filters[group]] + list(COMMON_INPUTS)
    # The runner image version (`ImageVersion`, set on GitHub-hosted runners):
    # a new image (toolchain, OS libs) re-runs everything.
    salt = os.environ.get("ImageVersion", "")  # noqa: SIM112
    return inputs_hash(globs, _tracked_files(), salt=salt)


def marker_key(job: str, digest: str) -> str:
    return f"{KEY_PREFIX}-{job}-{digest}"


def _marker_exists(repo: str, token: str, key: str, refs: Sequence[str]) -> bool:
    """True when the Actions cache holds ``key`` exactly, on one of ``refs``."""
    query = urllib.parse.urlencode({"key": key, "per_page": 100})
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/actions/caches?{query}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        body = json.load(response)
    return any(
        entry.get("key") == key and entry.get("ref") in refs
        for entry in body.get("actions_caches", [])
    )


def readable_refs(env: Mapping[str, str]) -> list[str]:
    """Refs whose caches this run can restore: its own ref and the default branch."""
    refs = [env.get("GITHUB_REF", "")]
    base = env.get("GITHUB_BASE_REF")
    if base:
        refs.append(f"refs/heads/{base}")
    default = env.get("DEFAULT_BRANCH")
    if default:
        refs.append(f"refs/heads/{default}")
    return [r for r in dict.fromkeys(refs) if r]


def lookup(env: Mapping[str, str]) -> tuple[dict[str, bool], dict[str, str]]:
    """Per-job ``(skips, keys)``: whether a pass marker exists, and the key to save."""
    filters = load_filters()
    keys = {job: marker_key(job, job_hash(job, filters)) for job in JOBS}
    skips = dict.fromkeys(JOBS, False)
    if env.get("GITHUB_EVENT_NAME") in NEVER_SKIP_EVENTS or env.get("CI_FULL_RUN") == "true":
        return skips, keys
    repo, token = env.get("GITHUB_REPOSITORY"), env.get("GITHUB_TOKEN")
    if not repo or not token:
        return skips, keys
    refs = readable_refs(env)
    for job, key in keys.items():
        try:
            skips[job] = _marker_exists(repo, token, key, refs)
        except Exception as exc:  # fail open: an unreachable API means run the job
            print(f"::warning::pass-marker lookup failed for {job}: {exc}", file=sys.stderr)
    return skips, keys


def main(argv: Sequence[str]) -> int:
    if len(argv) >= 1 and argv[0] == "lookup":
        skips, keys = lookup(os.environ)
        for job, skip in skips.items():
            print(f"{job}: {'skip (already passed)' if skip else 'run'}  [{keys[job]}]")
        output = os.environ.get("GITHUB_OUTPUT")
        if output:
            with open(output, "a", encoding="utf-8") as fh:
                fh.write(f"skips={json.dumps(skips)}\n")
                fh.write(f"keys={json.dumps(keys)}\n")
        return 0
    if len(argv) == 2 and argv[0] == "hash" and argv[1] in JOBS:
        print(marker_key(argv[1], job_hash(argv[1], load_filters())))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
