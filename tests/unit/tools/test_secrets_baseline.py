"""Tests for tools/secrets_baseline.py — the slim ``.secrets.baseline`` normalizer and gate.

The detect-secrets and git subprocesses are the out-of-process boundary; they
are replaced with canned results so these stay pure unit tests.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tools import secrets_baseline as sb

_SETTINGS: dict[str, Any] = {
    "version": "1.5.0",
    "plugins_used": [{"name": "GitHubTokenDetector"}, {"name": "KeywordDetector"}],
    "filters_used": [{"path": "detect_secrets.filters.allowlist.is_line_allowlisted"}],
}


def _entry(filename: str, hashed: str, kind: str, line: int | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "type": kind,
        "filename": filename,
        "hashed_secret": hashed,
        "is_verified": False,
    }
    if line is not None:
        entry["line_number"] = line
    return entry


def _full_baseline() -> dict[str, Any]:
    """A baseline as detect-secrets writes it: line numbers, timestamp, line order."""
    return {
        **json.loads(json.dumps(_SETTINGS)),
        "results": {
            "z.py": [_entry("z.py", "h2", "Secret Keyword", 40)],
            "a.py": [
                _entry("a.py", "h9", "Secret Keyword", 3),
                _entry("a.py", "h1", "Hex High Entropy String", 12),
            ],
        },
        "generated_at": "2026-01-01T00:00:00Z",
    }


@pytest.fixture()
def baseline_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the tool at a temp repo holding a committed slim baseline."""
    path = tmp_path / sb.BASELINE_NAME
    path.write_text(sb.normalize_text(json.dumps(_full_baseline())), encoding="utf-8")
    for name in ("a.py", "z.py"):
        (tmp_path / name).write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(sb, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(sb, "BASELINE", path)
    monkeypatch.setattr(sb, "_baseline_has_unstaged_changes", lambda: False)
    monkeypatch.setattr(sb, "_staged_deletions", lambda: set())
    return path


def _fake_scan(monkeypatch: pytest.MonkeyPatch, data: dict[str, Any]) -> list[list[str] | None]:
    calls: list[list[str] | None] = []

    def scan(paths: list[str] | None = None) -> dict[str, Any]:
        calls.append(paths)
        if paths is None:
            return data
        return {**data, "results": {f: e for f, e in data["results"].items() if f in paths}}

    monkeypatch.setattr(sb, "scan", scan)
    return calls


def test_normalize_strips_volatile_fields_and_sorts() -> None:
    """Normalizing drops line numbers and the timestamp and orders files and entries."""
    out = sb.normalize(_full_baseline())

    assert "generated_at" not in out
    assert list(out["results"]) == ["a.py", "z.py"]
    assert [e["hashed_secret"] for e in out["results"]["a.py"]] == ["h1", "h9"]
    assert all("line_number" not in e for es in out["results"].values() for e in es)
    assert {k: out[k] for k in sb.SETTINGS_KEYS} == _SETTINGS
    assert sb.finding_keys(out) == sb.finding_keys(_full_baseline())


def test_normalize_text_is_idempotent() -> None:
    """A normalized baseline normalizes to itself byte-for-byte."""
    once = sb.normalize_text(json.dumps(_full_baseline()))
    assert sb.normalize_text(once) == once
    assert once.endswith("}\n")


def test_compare_matching_slim_baseline_has_no_problems() -> None:
    """A committed slim baseline equal to the scan (whatever its line numbers) passes."""
    committed = sb.normalize_text(json.dumps(_full_baseline()))
    shifted = _full_baseline()
    shifted["results"]["a.py"][0]["line_number"] = 300
    assert sb.compare(committed, shifted) == []


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda d: d["results"]["a.py"].append(_entry("a.py", "h5", "Secret Keyword", 7)),
            "potential secret not in baseline: a.py (Secret Keyword)",
        ),
        (
            lambda d: d["results"].pop("z.py"),
            "stale baseline entry (no longer found): z.py (Secret Keyword)",
        ),
    ],
)
def test_compare_reports_new_and_stale_findings(mutate: Any, expected: str) -> None:
    """A scan with an extra or a missing finding fails with a specific message."""
    committed = sb.normalize_text(json.dumps(_full_baseline()))
    scanned = _full_baseline()
    mutate(scanned)
    assert sb.compare(committed, scanned) == [expected]


@pytest.mark.parametrize(
    ("key", "mutate"),
    [
        ("plugins_used", lambda d: d["plugins_used"].pop(0)),
        ("plugins_used", lambda d: d["plugins_used"].append({"name": "X", "limit": 9.0})),
        ("filters_used", lambda d: d["filters_used"].append({"path": "x.should_exclude_line"})),
        ("version", lambda d: d.update(version="1.4.0")),
    ],
)
def test_compare_rejects_settings_that_differ_from_the_scan(key: str, mutate: Any) -> None:
    """Detector settings in the committed file must equal the canonical scan settings.

    A change under review must not be able to switch off a detector (or loosen
    a filter) by editing the file the gate checks.
    """
    tampered = sb.normalize(_full_baseline())
    mutate(tampered)
    problems = sb.compare(sb.render(tampered), _full_baseline())
    assert len(problems) == 1
    assert problems[0].startswith(f"`{key}` differs from the canonical detect-secrets")


def test_compare_rejects_non_slim_committed_baseline() -> None:
    """A committed baseline still carrying line numbers or a timestamp fails."""
    committed = sb.render(_full_baseline())
    problems = sb.compare(committed, _full_baseline())
    assert len(problems) == 1
    assert "not in normalized slim form" in problems[0]


def test_compare_reports_unparseable_baseline() -> None:
    """Conflict markers left in the baseline produce a pointed message, not a traceback."""
    committed = "<<<<<<< HEAD\n" + sb.normalize_text(json.dumps(_full_baseline()))
    problems = sb.compare(committed, _full_baseline())
    assert len(problems) == 1
    assert "unresolved merge conflict" in problems[0]


def test_scan_never_loads_the_committed_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    """The scan runs without ``--baseline``, so the file under test cannot set detectors."""
    seen: list[list[str]] = []

    def run(cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(_full_baseline()), stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    sb.scan(["a.py"])

    (cmd,) = seen
    assert "--baseline" not in cmd
    assert "--slim" in cmd
    assert cmd[-1] == "a.py"


def test_scan_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-zero detect-secrets exit surfaces as ScanError."""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **_: subprocess.CompletedProcess(cmd, 2, stdout="", stderr="boom"),
    )
    with pytest.raises(sb.ScanError, match="boom"):
        sb.scan()


def test_check_is_read_only(baseline_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``check`` fails on a new finding and leaves the committed file untouched."""
    before = baseline_file.read_text(encoding="utf-8")
    scanned = _full_baseline()
    scanned["results"]["a.py"].append(_entry("a.py", "h5", "Secret Keyword"))
    _fake_scan(monkeypatch, scanned)

    assert sb.cmd_check() == 1
    assert baseline_file.read_text(encoding="utf-8") == before


def test_update_writes_slim_scan_without_reading_old_file(
    baseline_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``update`` regenerates from the scan alone, so it also clears conflict markers."""
    baseline_file.write_text("<<<<<<< HEAD\nnot json\n", encoding="utf-8")
    _fake_scan(monkeypatch, _full_baseline())

    assert sb.cmd_update() == 0
    assert baseline_file.read_text(encoding="utf-8") == sb.normalize_text(
        json.dumps(_full_baseline())
    )


def test_hook_passes_on_shifted_line_numbers(
    baseline_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Editing a file that holds a known fixture leaves the baseline alone."""
    before = baseline_file.read_text(encoding="utf-8")
    shifted = _full_baseline()
    shifted["results"]["a.py"][0]["line_number"] = 300
    calls = _fake_scan(monkeypatch, shifted)

    assert sb.cmd_hook(["a.py"]) == 0
    assert calls == [["a.py"]]
    assert baseline_file.read_text(encoding="utf-8") == before


def test_hook_rejects_new_finding(baseline_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A staged file with an unallowlisted secret fails the commit without touching the file."""
    before = baseline_file.read_text(encoding="utf-8")
    scanned = _full_baseline()
    scanned["results"]["a.py"].append(_entry("a.py", "h5", "GitHub Token"))
    _fake_scan(monkeypatch, scanned)

    assert sb.cmd_hook(["a.py"]) == 1
    assert baseline_file.read_text(encoding="utf-8") == before


def test_hook_trims_removed_fixture(baseline_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A fixture removed from a staged file is trimmed; exit 3 asks for a re-stage."""
    scanned = _full_baseline()
    scanned["results"]["a.py"] = [_entry("a.py", "h1", "Hex High Entropy String")]
    _fake_scan(monkeypatch, scanned)

    assert sb.cmd_hook(["a.py"]) == 3
    after = json.loads(baseline_file.read_text(encoding="utf-8"))
    assert sb.finding_keys(after) == {
        ("a.py", "Hex High Entropy String", "h1"),
        ("z.py", "Secret Keyword", "h2"),
    }
    assert sb.normalize_text(baseline_file.read_text(encoding="utf-8")) == (
        baseline_file.read_text(encoding="utf-8")
    )


def test_hook_trims_staged_deletion(baseline_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Deleting a fixture file drops its entries, even though lefthook never passes it."""
    _fake_scan(monkeypatch, _full_baseline())
    monkeypatch.setattr(sb, "_staged_deletions", lambda: {"z.py"})

    assert sb.cmd_hook(["a.py"]) == 3
    after = json.loads(baseline_file.read_text(encoding="utf-8"))
    assert "z.py" not in after["results"]


def test_hook_refuses_unstaged_baseline(
    baseline_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unstaged baseline edit would be left out of the commit, so the hook stops."""
    monkeypatch.setattr(sb, "_baseline_has_unstaged_changes", lambda: True)
    calls = _fake_scan(monkeypatch, _full_baseline())

    assert sb.cmd_hook(["a.py"]) == 1
    assert calls == []


def test_committed_baseline_is_slim() -> None:
    """The repo's own baseline is stored in normalized slim form."""
    text = sb.BASELINE.read_text(encoding="utf-8")
    assert sb.normalize_text(text) == text
