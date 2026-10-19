"""Keep ``.secrets.baseline`` in slim form and gate new secrets against it.

detect-secrets matches a baseline entry on ``(filename, hashed_secret, type)``;
the stored ``line_number`` and the top-level ``generated_at`` timestamp play no
part in detection. They do, however, change whenever a file holding a known
test secret is edited or the baseline is regenerated, which turns the baseline
into a merge-conflict magnet. This tool stores the baseline without either
field, sorted deterministically, so it only changes when the *set* of
allowlisted findings changes.

Every scan runs ``detect-secrets scan --slim`` *without* ``--baseline``. With
``--baseline``, detect-secrets loads its plugins and filters from the baseline
file itself, so a change under review could switch off a detector by editing
the file the gate checks. Here the detector settings always come from the
installed detect-secrets defaults plus :data:`SCAN_ARGS`, and the committed
``version`` / ``plugins_used`` / ``filters_used`` must match them exactly.

Subcommands (``uv run python -m tools.secrets_baseline <cmd>``):

- ``update`` (``make secrets-baseline``): re-scan the repo and write the
  baseline from the result. It never reads the old file, so it also resolves a
  merge conflict on the baseline (conflict markers included). Run it after
  adding, removing, moving or deleting a test secret, or after upgrading
  detect-secrets.
- ``hook <files...>`` (lefthook pre-commit): scan the staged files. A finding
  not in the baseline fails the commit (exit 1). If a known finding is gone
  from a staged file, or a staged deletion removes a file the baseline lists,
  the baseline is trimmed and the commit fails with exit 3 so the change can
  be staged.
- ``check`` (``make detect-secrets``, CI): re-scan the repo without writing
  anything and fail if the committed baseline differs from the result: a new
  unallowlisted secret, a stale entry, detector settings that differ from the
  canonical ones, or a file that is not normalized.

Slim baselines do not support ``detect-secrets audit`` (it needs line
numbers). To audit, run a throwaway full scan into a scratch file.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_NAME = ".secrets.baseline"
BASELINE = REPO_ROOT / BASELINE_NAME

#: Repo-specific scan options. Git internals are skipped, and so is the
#: baseline itself (its ``hashed_secret`` values read as high-entropy strings).
SCAN_ARGS: tuple[str, ...] = (
    "--exclude-files",
    r"\.git/",
    "--exclude-files",
    rf"^{re.escape(BASELINE_NAME)}$",
)

#: Top-level fields that pin the detector configuration.
SETTINGS_KEYS = ("version", "plugins_used", "filters_used")
#: Per-entry fields that do not take part in baseline matching.
_VOLATILE_ENTRY_KEYS = ("line_number",)
#: Top-level fields that do not take part in baseline matching.
_VOLATILE_TOP_KEYS = ("generated_at",)

_FIX_HINT = "run `make secrets-baseline` and commit the result"
_NEW_PREFIX = "potential secret not in baseline"
_STALE_PREFIX = "stale baseline entry (no longer found)"
_FINDING_HINT = (
    "a real secret must be removed from the change; a test fixture can be marked "
    f"`pragma: allowlist secret` inline, or {_FIX_HINT}"
)


class ScanError(RuntimeError):
    """detect-secrets exited non-zero or printed something other than a baseline."""


def normalize(data: dict[str, Any]) -> dict[str, Any]:
    """Return the slim, deterministically ordered form of a baseline document.

    Drops ``generated_at`` and every ``line_number``, sorts files by name and
    each file's entries by ``(hashed_secret, type)`` (detect-secrets' own order
    when no line numbers are tracked). All other fields are kept verbatim.
    """
    out = {k: v for k, v in data.items() if k not in _VOLATILE_TOP_KEYS}
    results: dict[str, list[dict[str, Any]]] = {}
    for filename in sorted(data.get("results", {})):
        entries = [
            {k: v for k, v in entry.items() if k not in _VOLATILE_ENTRY_KEYS}
            for entry in data["results"][filename]
        ]
        entries.sort(key=lambda e: (e["hashed_secret"], e["type"]))
        if entries:
            results[filename] = entries
    out["results"] = results
    return out


def render(data: dict[str, Any]) -> str:
    """Serialize a baseline the way detect-secrets does (2-space JSON + newline)."""
    return json.dumps(data, indent=2) + "\n"


def normalize_text(text: str) -> str:
    """Normalize a serialized baseline."""
    return render(normalize(json.loads(text)))


def _entry_key(entry: dict[str, Any]) -> tuple[str, str]:
    return (entry["type"], entry["hashed_secret"])


def finding_keys(data: dict[str, Any]) -> set[tuple[str, str, str]]:
    """The ``(filename, type, hashed_secret)`` identities a baseline allowlists."""
    return {
        (filename, *_entry_key(entry))
        for filename, entries in data.get("results", {}).items()
        for entry in entries
    }


def compare(committed_text: str, scanned: dict[str, Any]) -> list[str]:
    """Explain how a committed baseline differs from a fresh scan.

    ``scanned`` is the parsed output of :func:`scan`. Returns one
    human-readable problem per line; an empty list means the committed file is
    exactly the normalized scan result.
    """
    try:
        committed = json.loads(committed_text)
    except json.JSONDecodeError as exc:
        return [f"{BASELINE_NAME} is not valid JSON ({exc}); unresolved merge conflict?"]

    expected = normalize(scanned)
    problems: list[str] = []

    for key in SETTINGS_KEYS:
        if committed.get(key) != expected.get(key):
            problems.append(
                f"`{key}` differs from the canonical detect-secrets "
                f"{expected.get('version')} settings (edited by hand, or "
                "detect-secrets was upgraded)"
            )

    committed_keys = finding_keys(committed)
    scanned_keys = finding_keys(expected)
    for filename, kind, _hash in sorted(scanned_keys - committed_keys):
        problems.append(f"{_NEW_PREFIX}: {filename} ({kind})")
    for filename, kind, _hash in sorted(committed_keys - scanned_keys):
        problems.append(f"{_STALE_PREFIX}: {filename} ({kind})")

    if not problems and committed_text != render(expected):
        problems.append(
            f"{BASELINE_NAME} is not in normalized slim form "
            "(line_number/generated_at present, entries out of order, or extra fields)"
        )
    return problems


def scan(paths: list[str] | None = None) -> dict[str, Any]:
    """Run a slim detect-secrets scan with the canonical settings.

    With no ``paths`` the whole repo is scanned (git-tracked files only). The
    committed baseline is never read, so its contents cannot change which
    detectors or filters run.
    """
    cmd = [sys.executable, "-m", "detect_secrets", "scan", "--slim", *SCAN_ARGS, *(paths or [])]
    proc = subprocess.run(cmd, cwd=REPO_ROOT, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ScanError(f"detect-secrets scan failed (exit {proc.returncode}): {proc.stderr}")
    try:
        data: dict[str, Any] = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ScanError(f"detect-secrets scan printed invalid JSON: {exc}") from exc
    return data


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, check=True, capture_output=True, text=True
    ).stdout


def _baseline_has_unstaged_changes() -> bool:
    return bool(_git("diff", "--name-only", "--", BASELINE_NAME).strip())


def _staged_deletions() -> set[str]:
    """Paths the index removes, including the old side of renames."""
    out = _git("diff", "--cached", "--name-only", "--no-renames", "--diff-filter=D")
    return {line for line in out.splitlines() if line}


def _err(message: str) -> None:
    print(f"secrets_baseline: {message}", file=sys.stderr)


def cmd_update() -> int:
    BASELINE.write_text(render(normalize(scan())), encoding="utf-8")
    print(f"secrets_baseline: updated {BASELINE_NAME} (slim form)")
    return 0


def cmd_hook(files: list[str]) -> int:
    if _baseline_has_unstaged_changes():
        _err(f"{BASELINE_NAME} has unstaged changes; `git add {BASELINE_NAME}` first")
        return 1

    original = BASELINE.read_text(encoding="utf-8")
    committed = json.loads(original)
    results: dict[str, list[dict[str, Any]]] = dict(committed.get("results", {}))

    targets = [f for f in files if f != BASELINE_NAME and (REPO_ROOT / f).is_file()]
    found = scan(targets).get("results", {}) if targets else {}

    new = [
        (filename, entry["type"])
        for filename in targets
        for entry in found.get(filename, [])
        if _entry_key(entry) not in {_entry_key(e) for e in results.get(filename, [])}
    ]
    if new:
        for filename, kind in sorted(set(new)):
            _err(f"{_NEW_PREFIX}: {filename} ({kind})")
        _err(_FINDING_HINT)
        return 1

    # Trim allowlist entries whose finding is gone from a staged file, and
    # entries for files the commit deletes (lefthook's {staged_files} omits them).
    for filename in targets:
        still_found = {_entry_key(e) for e in found.get(filename, [])}
        results[filename] = [e for e in results.get(filename, []) if _entry_key(e) in still_found]
    for filename in _staged_deletions():
        results.pop(filename, None)

    updated = render(normalize({**committed, "results": results}))
    if updated == normalize_text(original):
        return 0
    BASELINE.write_text(updated, encoding="utf-8")
    _err(f"removed stale entries from {BASELINE_NAME}; `git add {BASELINE_NAME}` and commit again")
    return 3


def cmd_check() -> int:
    committed_text = BASELINE.read_text(encoding="utf-8")
    problems = compare(committed_text, scan())
    if problems:
        for p in problems:
            _err(p)
        has_new = any(p.startswith(_NEW_PREFIX) for p in problems)
        _err(_FINDING_HINT if has_new else _FIX_HINT)
        return 1
    print(f"secrets_baseline: {BASELINE_NAME} matches a fresh scan")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("update", help="re-scan the repo and rewrite the baseline")
    hook = sub.add_parser("hook", help="pre-commit hook over the given files")
    hook.add_argument("files", nargs="*")
    sub.add_parser("check", help="fail if the baseline differs from a fresh scan")
    args = parser.parse_args(argv)

    try:
        if args.cmd == "update":
            return cmd_update()
        if args.cmd == "hook":
            return cmd_hook(args.files)
        return cmd_check()
    except ScanError as exc:
        _err(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
