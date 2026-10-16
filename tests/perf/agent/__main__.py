"""Agent-facing perf probes: ``python -m tests.perf.agent <command>``.

Run via ``make perf-agent-<command>`` (see ``make perf-help``). Every command
prints markdown (tables + mechanical highlights + ``NEXT:``) and ends with
``ARTIFACT: <json>``. Exit codes: 0 ran, 2 prerequisite missing (the message
names the fix), 3 regression (``compare``).

Commands: doctor, sql, stages, cpu, resilience, compare.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from jentic_one.shared.redaction import redact_mapping
from tests.perf import pyspy_profile
from tests.perf.agent import compare, sqltrace, stages
from tests.perf.agent.base import (
    BROKER_HOST,
    EXIT_PREREQ,
    SRC_PATHS,
    TRACE_ENV_KEYS,
    PrereqError,
    Report,
    broker_variant,
    env_or_fail,
    git_dirty,
    md_table,
    proxy_url,
    require_fresh,
    require_ok,
    run_lock,
)
from tests.perf.common import DEFAULT_IMAGE, REPO_ROOT, compose, git_sha

REQUIRED = ("postgres", "app", "broker", "smoke-upstream")
#: The image the compose stack runs (``PERF_IMAGE`` overrides it, as in run metadata).
IMAGE = os.environ.get("PERF_IMAGE", DEFAULT_IMAGE)
#: Set by ``make perf-build`` (tests/perf/Makefile).
REVISION_LABEL = "org.opencontainers.image.revision"
DIRTY_LABEL = "dev.jentic.perf.src-dirty"

# --------------------------------------------------------------------------- doctor


def doctor_checks() -> list[tuple[str, bool, str, str]]:
    """(check, ok, detail, fix) rows; never raises."""
    rows: list[tuple[str, bool, str, str]] = []
    try:
        out = compose("ps", "--format", "json", capture=True)
        services = {}
        for line in out.splitlines():
            if line.strip().startswith("{"):
                svc = json.loads(line)
                services[svc.get("Service")] = svc
        for name in REQUIRED:
            svc = services.get(name)
            health = (svc or {}).get("Health") or (svc or {}).get("State", "missing")
            ok = bool(svc) and health in ("healthy", "running")
            rows.append((f"container {name}", ok, str(health), "make perf-up"))
    except RuntimeError as exc:
        rows.append(("docker compose", False, str(exc)[:120], "start Docker, then make perf-up"))
        return rows

    try:
        env = env_or_fail()
        rows.append(("perf env (.perf-env.json)", True, env.created_at or "present", ""))
        try:
            # A probe needs a few minutes of validity; each probe re-checks for its own length.
            require_fresh(env, callers=None, run_s=0)
            rows.append(("tokens fresh (5 min margin)", True, "", ""))
        except PrereqError as exc:
            rows.append(("tokens fresh (5 min margin)", False, str(exc)[:160], exc.fix))
        try:
            resp = httpx.post(
                proxy_url(env),
                headers={"Authorization": f"Bearer {env.caller('jak').token}"},
                content=b"{}",
                timeout=10,
            )
            ok = resp.status_code == 200
            rows.append(
                ("broker call (jak caller)", ok, f"HTTP {resp.status_code}", "make perf-provision")
            )
        except httpx.HTTPError as exc:
            rows.append(("broker call (jak caller)", False, type(exc).__name__, "make perf-up"))
    except PrereqError as exc:
        rows.append(("perf env (.perf-env.json)", False, str(exc), exc.fix))

    rows.append(broker_env_check())
    rows.append(image_check())
    return rows


def _docker_inspect(*args: str) -> str | None:
    proc = subprocess.run(["docker", *args], capture_output=True, text=True, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else None


def broker_env_drift(expected: dict[str, Any], actual: dict[str, str]) -> list[str]:
    """Keys whose value in the running broker differs from a clean ``make perf-up``."""
    drift = []
    for key in sorted(expected):
        want = "" if expected[key] is None else str(expected[key])
        have = actual.get(key, "")
        if want != have:
            drift.append(f"{key}={have!r} (clean: {want!r})")
    return drift


def broker_env_check() -> tuple[str, bool, str, str]:
    """Is the running broker the clean one (not traced, not a probe variant, current image)?"""
    name, fix = "broker clean (env + image)", "make perf-up   # recreates the broker from compose"
    try:
        cid = compose("ps", "-q", "broker", capture=True).strip()
        # Compose resolves ${PERF_*} the way perf-up would, with the trace hook off.
        rendered = compose(
            "config", "--format", "json", env=dict.fromkeys(TRACE_ENV_KEYS, ""), capture=True
        )
    except RuntimeError as exc:
        return name, False, str(exc)[:120], fix
    if not cid:
        return name, False, "broker not running", fix
    expected = json.loads(rendered)["services"]["broker"].get("environment") or {}
    env_json = _docker_inspect("inspect", cid, "--format", "{{json .Config.Env}}")
    if env_json is None:
        return name, False, "docker inspect failed", fix
    actual = dict(item.partition("=")[::2] for item in json.loads(env_json))
    problems = broker_env_drift(expected, actual)
    running = _docker_inspect("inspect", cid, "--format", "{{.Image}}")
    built = _docker_inspect("image", "inspect", IMAGE, "--format", "{{.Id}}")
    if running and built and running != built:
        problems.append(f"runs an older {IMAGE} build")
    return name, not problems, "; ".join(problems)[:300] or "matches compose", fix


def image_check() -> tuple[str, bool, str, str]:
    """Was ``IMAGE`` built from this checkout's ``src`` (by its revision label)?"""
    name, fix = f"image {IMAGE} vs src", "make perf-build && make perf-up"
    labels_json = _docker_inspect("image", "inspect", IMAGE, "--format", "{{json .Config.Labels}}")
    if labels_json is None:
        return f"image {IMAGE}", False, "not built", fix
    labels = json.loads(labels_json) or {}
    rev = labels.get(REVISION_LABEL, "")
    if not rev:
        return name, False, f"no {REVISION_LABEL} label (build with make perf-build)", fix
    diff = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "diff", "--quiet", rev, "HEAD", "--", *SRC_PATHS],
        capture_output=True,
        check=False,
    )
    problems = []
    if diff.returncode == 1:
        problems.append(f"src changed since the image's commit {rev[:9]}")
    elif diff.returncode != 0:
        problems.append(f"image commit {rev[:9]} is not in this checkout")
    if labels.get(DIRTY_LABEL) == "true":
        problems.append("image was built from uncommitted src")
    if git_dirty(SRC_PATHS):
        problems.append("src has uncommitted changes (not in the image)")
    detail = "; ".join(problems) or f"built from {rev[:9]}, src unchanged since"
    return name, not problems, detail, fix


def cmd_doctor(_: list[str]) -> int:
    rows = doctor_checks()
    rep = Report("doctor", "Perf stack pre-flight")
    rep.data = {"checks": [{"check": c, "ok": ok, "detail": d, "fix": f} for c, ok, d, f in rows]}
    failed = [r for r in rows if not r[1]]
    rep.highlights.append(
        "all checks passed" if not failed else f"{len(failed)} check(s) failed — run the fixes"
    )
    rep.section(
        "Checks",
        md_table(
            ["check", "ok", "detail", "fix"],
            [[c, "yes" if ok else "NO", d, "" if ok else f] for c, ok, d, f in rows],
        ),
    )
    if failed:
        rep.next_steps += sorted({r[3] for r in failed})
    else:
        rep.next_steps += ["make perf-agent-sql", "make perf-agent-stages", "make perf-agent-cpu"]
    rep.finish()
    return EXIT_PREREQ if failed else 0


def preflight() -> None:
    rows = doctor_checks()
    hard = [r for r in rows if not r[1] and not r[0].startswith("image")]
    if hard:
        lines = "\n".join(f"  - {c}: {d}  ->  {f}" for c, _, d, f in hard)
        raise PrereqError(f"perf stack not ready:\n{lines}", hard[0][3])
    for c, _, d, f in (r for r in rows if not r[1] and r[0].startswith("image")):
        print(f"warning: {c}: {d}\n  fix: {f}\n", file=sys.stderr)


# --------------------------------------------------------------------------- cpu


#: Process entrypoints: every sample passes through them, so they say nothing.
ENTRYPOINT_FILES = frozenset({"jentic_one/__main__.py"})


def line_hotspots(raw: Path, top: int = 20) -> dict[str, list[dict[str, Any]]]:
    """Top ``jentic_one`` file:line by self and by inclusive share."""
    total = 0
    self_: Counter[str] = Counter()
    incl: Counter[str] = Counter()
    frame_re = re.compile(r"(\S+) \((?:.*/)?(jentic_one/[^:]+):(\d+)\)")
    for line in raw.read_text().splitlines():
        stack, _, count = line.rpartition(" ")
        n = int(count)
        total += n
        frames = stack.split(";")
        ours = [
            m for f in frames if (m := frame_re.search(f)) and m.group(2) not in ENTRYPOINT_FILES
        ]
        if not ours:
            continue
        deepest = ours[-1]
        self_[f"src/{deepest.group(2)}:{deepest.group(3)} {deepest.group(1)}"] += n
        for key in {f"src/{m.group(2)}:{m.group(3)} {m.group(1)}" for m in ours}:
            incl[key] += n

    def rows(c: Counter[str]) -> list[dict[str, Any]]:
        return [{"frame": k, "pct": round(100 * v / total, 2)} for k, v in c.most_common(top)]

    return {"deepest_own_frame": rows(self_), "inclusive": rows(incl)}


def cmd_cpu(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="agent cpu")
    p.add_argument("--concurrency", type=int, default=16)
    p.add_argument("--duration", type=int, default=30)
    p.add_argument("--caller", choices=("jak", "at", "jwt"), default="jak")
    p.add_argument("--vendor", default="perf-noauth")
    args = p.parse_args(argv)
    try:
        # min_ok=1.0: a profile with any failed request profiles the failure.
        stem, load = pyspy_profile.capture(
            vendor=args.vendor,
            caller=args.caller,
            concurrency=args.concurrency,
            duration=args.duration,
            rate=200,
            min_ok=1.0,
        )
    except pyspy_profile.LoadError as exc:
        raise PrereqError(f"cpu load: {exc}", "make perf-agent-doctor") from exc
    except SystemExit as exc:
        # Stale tokens, the run lock held, or no broker: all fixable prerequisites.
        raise PrereqError(str(exc.code), "make perf-agent-doctor") from None
    raw = pyspy_profile.OUT_DIR / f"{stem}.txt"
    require_ok(load, "cpu load")
    summary = pyspy_profile.analyse(raw)
    hot = line_hotspots(raw)
    svg = (pyspy_profile.OUT_DIR / f"{stem}.svg").relative_to(REPO_ROOT)
    rep = Report("cpu", f"Broker CPU under load — c={args.concurrency}, {args.vendor}")
    rep.data = {"args": vars(args), "load": load, "profile": summary, "lines": hot}
    rep.data["flamegraph"] = str(svg)
    rep.highlights.append(
        f"{load.get('rps', 0):.0f} rps saturated; {summary['samples']} samples; flamegraph `{svg}`"
    )
    for name, share in list(summary["buckets"].items())[:4]:
        rep.highlights.append(f"{share:.1%} of CPU in {name}")
    for r in hot["deepest_own_frame"][:3]:
        rep.highlights.append(f"{r['pct']}% with `{r['frame']}` as deepest jentic_one frame")
    rep.section(
        "CPU by subsystem (exclusive, first match)",
        md_table(
            ["share %", "subsystem"],
            [[round(100 * v, 2), k] for k, v in summary["buckets"].items()],
        ),
    )
    rep.section(
        "Call sites (inclusive, lower bound)",
        md_table(
            ["share %", "call site"],
            [[round(100 * v, 2), k] for k, v in summary["probes"].items()],
        ),
        note="SQLAlchemy's greenlet bridge detaches ORM stacks from callers, so these undercount",
    )
    rep.section(
        "jentic_one lines — deepest own frame (where our code hands off to libraries)",
        md_table(
            ["share %", "file:line function"],
            [[r["pct"], r["frame"]] for r in hot["deepest_own_frame"]],
        ),
    )
    rep.section(
        "jentic_one lines — inclusive",
        md_table(
            ["share %", "file:line function"], [[r["pct"], r["frame"]] for r in hot["inclusive"]]
        ),
    )
    rep.section(
        "Hottest leaf frames (any library)",
        md_table(
            ["share %", "frame"],
            [[round(100 * t["share"], 2), t["frame"]] for t in summary["top_self"][:15]],
        ),
    )
    rep.next_steps += [
        "make perf-agent-stages   # which stage those lines belong to, in ms",
        "make perf-agent-compare BASE=<this ARTIFACT>",
    ]
    rep.finish()
    return 0


# --------------------------------------------------------------------------- resilience

#: (mode, broker overrides, scenarios). RetryUser needs the breaker off;
#: FailingUpstreamUser needs it on; DisconnectUser asserts per streaming mode.
RES_VARIANTS: tuple[tuple[str, dict[str, str], tuple[str, ...]], ...] = (
    (
        "streaming",
        {"PERF_STREAMING": "true"},
        ("DisconnectUser", "FailingUpstreamUser", "IdempotentReplayUser", "AsyncJobUser"),
    ),
    (
        "buffered",
        {"PERF_STREAMING": "false"},
        ("DisconnectUser", "FailingUpstreamUser", "IdempotentReplayUser"),
    ),
    ("retry", {"PERF_RETRY": "true", "PERF_BREAKER": "false"}, ("RetryUser",)),
)
SCENARIOS = tuple(dict.fromkeys(s for _, _, ss in RES_VARIANTS for s in ss))
#: Deepest ``jentic_one`` frame in a logged traceback (where it escaped our code).
TB_FRAME_RE = re.compile(r'File "[^"]*/(jentic_one/[^"]+)", line (\d+), in (\w+)')
LOG_LEVELS = ("error", "warning", "critical")
#: The only log fields kept in an artifact sample: where and what failed, never
#: request payloads, headers or identifiers beyond the request id.
SAMPLE_FIELDS = (
    "event",
    "level",
    "logger",
    "error_type",
    "request_id",
    "status_code",
    "filename",
    "lineno",
    "exception",
)


def locust_run(scenario: str, users: int, seconds: int, mode: str, streaming: bool) -> Path:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    prefix = f"agent/locust/{stamp}-{mode}-{scenario}"
    (REPO_ROOT / "tests/perf/results/agent/locust").mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(REPO_ROOT / "tests/perf/docker-compose.perf.yaml"),
            "--profile",
            "load",
            "run",
            "--rm",
            "-T",
            "--no-deps",
            "-e",
            f"PERF_STREAMING={'true' if streaming else 'false'}",
            "-e",
            # The locustfile's own token guard: this run's length, not the 600 s default.
            f"PERF_RUN_S={seconds}",
            "locust-master",
            "-f",
            "/work/tests/perf/locustfile.py",
            "--headless",
            "-u",
            str(users),
            "-r",
            str(users),
            "-t",
            f"{seconds}s",
            "--only-summary",
            "--csv",
            f"/work/tests/perf/results/{prefix}",
            scenario,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return REPO_ROOT / "tests/perf/results" / prefix


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


def broker_logs(since: str) -> list[dict[str, Any]]:
    out = compose("logs", "--no-log-prefix", "--since", since, "broker", capture=True)
    events = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def group_logs(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        level = str(e.get("level", "")).lower()
        if level not in LOG_LEVELS:
            continue
        where = e.get("logger") or e.get("module") or ""
        if e.get("filename"):
            where = f"{e['filename']}:{e.get('lineno', '')}"
        frames = TB_FRAME_RE.findall(str(e.get("exception", "")))
        if frames:
            path, lineno, func = frames[-1]
            where = f"src/{path}:{lineno} {func}"
        key = (level, str(e.get("event", "")), str(where), str(e.get("error_type", "")))
        groups[key].append(e)
    rows = []
    for (level, event, where, etype), evs in groups.items():
        sample = redact_mapping({k: evs[0][k] for k in SAMPLE_FIELDS if k in evs[0]})
        rows.append(
            {
                "level": level,
                "event": event,
                "where": where,
                "error_type": etype,
                "count": len(evs),
                "sample_request_id": evs[0].get("request_id", ""),
                "sample": sample,
            }
        )
    rows.sort(key=lambda r: (LOG_LEVELS.index(r["level"]), -r["count"]))
    return rows


def cmd_resilience(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="agent resilience")
    p.add_argument("--seconds", type=int, default=20, help="per scenario per mode")
    p.add_argument("--users", type=int, default=4)
    p.add_argument("--scenarios", default=",".join(SCENARIOS))
    args = p.parse_args(argv)
    scenarios = tuple(s for s in args.scenarios.split(",") if s)
    runs = sum(1 for _, _, vs in RES_VARIANTS for sc in vs if sc in scenarios)
    # Locust users pick any caller and the admin token; each run also recreates
    # the broker (~1 min).
    require_fresh(env_or_fail(), callers=None, admin=True, run_s=runs * (args.seconds + 60))
    results: list[dict[str, Any]] = []
    logs: dict[str, list[dict[str, Any]]] = {}
    for mode, overrides, variant_scenarios in RES_VARIANTS:
        todo = [sc for sc in variant_scenarios if sc in scenarios]
        if not todo:
            continue
        streaming = overrides.get("PERF_STREAMING", "true") == "true"
        logs[mode] = []
        for scenario in todo:
            # A fresh broker per scenario: breaker state and pending idempotency
            # claims are in-memory and would otherwise leak into the next one.
            with broker_variant(overrides):
                since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                prefix = locust_run(scenario, args.users, args.seconds, mode, streaming)
                stats = [
                    r
                    for r in read_csv(Path(f"{prefix}_stats.csv"))
                    if r.get("Name") != "Aggregated"
                ]
                if not stats:
                    results.append(
                        {
                            "mode": mode,
                            "scenario": scenario,
                            "name": "(no stats: locust failed to run)",
                            "requests": 0,
                            "failures": 1,
                            "p50_ms": "",
                            "p99_ms": "",
                            "failure_reasons": [f"see {prefix}_*.csv"],
                        }
                    )
                failures = read_csv(Path(f"{prefix}_failures.csv"))
                for st in stats:
                    results.append(
                        {
                            "mode": mode,
                            "scenario": scenario,
                            "name": st.get("Name", ""),
                            "requests": int(st.get("Request Count", 0) or 0),
                            "failures": int(st.get("Failure Count", 0) or 0),
                            "p50_ms": st.get("50%", ""),
                            "p99_ms": st.get("99%", ""),
                            "failure_reasons": [
                                f"{f.get('Occurrences')}x {f.get('Error')}"
                                for f in failures
                                if f.get("Name") == st.get("Name")
                            ],
                        }
                    )
                for row in group_logs(broker_logs(since)):
                    row["scenario"] = scenario
                    logs[mode].append(row)

    rep = Report("resilience", f"Broker resilience — {', '.join(scenarios)}")
    rep.data = {"args": vars(args), "scenarios": results, "logs": logs}
    failing = [r for r in results if r["failures"]]
    rep.highlights.append(
        f"{len(failing)} of {len(results)} scenario/mode runs had assertion failures"
    )
    for r in failing[:5]:
        rep.highlights.append(
            f"{r['mode']} {r['scenario']}: {r['failures']}/{r['requests']} failed — "
            f"{'; '.join(r['failure_reasons'])[:200]}"
        )
    for mode, rows in logs.items():
        for r in rows[:3]:
            rep.highlights.append(
                f"{mode}: {r['count']}x {r['level']} `{r['event']}` "
                f"{r['error_type']} ({r['where'] or 'no location'})"
            )
    rep.section(
        "Scenarios",
        md_table(
            ["mode", "scenario", "request", "reqs", "fails", "p50", "p99", "failure reasons"],
            [
                [
                    r["mode"],
                    r["scenario"],
                    r["name"],
                    r["requests"],
                    r["failures"],
                    r["p50_ms"],
                    r["p99_ms"],
                    "; ".join(r["failure_reasons"])[:160],
                ]
                for r in results
            ],
        ),
        note="assertions live in tests/perf/locustfile.py (e.g. DisconnectUser per streaming mode)",
    )
    for mode, rows in logs.items():
        rep.section(
            f"Broker {'/'.join(LOG_LEVELS)} logs — {mode}",
            md_table(
                ["scenario", "level", "event", "error_type", "where", "count", "sample request_id"],
                [
                    [
                        r.get("scenario", ""),
                        r["level"],
                        r["event"],
                        r["error_type"],
                        r["where"],
                        r["count"],
                        r["sample_request_id"],
                    ]
                    for r in rows
                ],
            )
            if rows
            else "_none_",
            note="grep the event name in src/ for the emitting line; full samples in the artifact",
        )
    rep.next_steps += [
        "rg -n '\"<event>\"' src/jentic_one   # find where a logged event is raised",
        "make perf-agent-resilience SCENARIOS=DisconnectUser SECONDS=60",
    ]
    rep.finish()
    return 0


# --------------------------------------------------------------------------- dispatch


def _probe(fn: Callable[[list[str]], int], *, lock: str | None) -> Callable[[list[str]], int]:
    """Pre-flight, then run ``fn`` under the suite run lock (``lock=None``: ``fn`` takes it)."""

    def run(argv: list[str]) -> int:
        preflight()
        if lock is None:
            return fn(argv)
        with run_lock(lock):
            return fn(argv)

    return run


COMMANDS: dict[str, Callable[[list[str]], int]] = {
    "doctor": cmd_doctor,
    "sql": _probe(sqltrace.main, lock="agent-sql"),
    "stages": _probe(stages.main, lock="agent-stages"),
    # pyspy_profile.capture holds the lock itself (it is also the perf-profile tool).
    "cpu": _probe(cmd_cpu, lock=None),
    "resilience": _probe(cmd_resilience, lock="agent-resilience"),
    "compare": compare.main,
}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in COMMANDS:
        print(__doc__)
        return EXIT_PREREQ
    try:
        return COMMANDS[argv[0]](argv[1:])
    except PrereqError as exc:
        print(f"PREREQUISITE: {exc}\nFIX: {exc.fix}")
        return EXIT_PREREQ


if __name__ == "__main__":
    print(f"(git {git_sha()}, broker {BROKER_HOST})", file=sys.stderr)
    sys.exit(main())
