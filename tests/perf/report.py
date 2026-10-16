"""Static HTML report over every result JSON in tests/perf/results/.

No Python plotting dependency: emits a self-contained HTML page that loads
Plotly.js from its CDN and renders JSON traces built here. Sections:

* **Overhead (latest run)** — broker vs direct p50/p99 per case, and the
  overhead (mean / p50) bar per case.
* **Overhead over time** — overhead p50 per case across runs (git SHA on hover),
  for run-over-run regression spotting.
* **DB cost** — statements and transactions per call per plane, warm vs cold.
* **Locust** — the latest ``*_stats.csv`` summary table, if present.

Usage::

    uv run python -m tests.perf.report            # writes results/report.html
    uv run python -m tests.perf.report --open
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import sys
import webbrowser
from pathlib import Path
from typing import Any

from tests.perf.common import RESULTS_DIR

PLOTLY_CDN = "https://cdn.plot.ly/plotly-2.35.2.min.js"


def load(kind: str) -> list[dict[str, Any]]:
    runs = []
    for path in sorted(RESULTS_DIR.glob(f"*-{kind}.json")):
        data = json.loads(path.read_text())
        data["_file"] = path.name
        runs.append(data)
    return runs


def _label(run: dict[str, Any]) -> str:
    return f"{run.get('timestamp', '')[:16]} {run.get('git_sha', '')}"


def overhead_latest(run: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [r for r in run["rows"] if r.get("direct")]
    names = [f"{r['case']} @{r['rate']}" for r in rows]
    charts = [
        {
            "title": f"Broker vs direct latency — {_label(run)}",
            "data": [
                {
                    "type": "bar",
                    "name": "direct p50",
                    "x": names,
                    "y": [r["direct"]["p50_ms"] for r in rows],
                },
                {
                    "type": "bar",
                    "name": "broker p50",
                    "x": names,
                    "y": [r["broker"]["p50_ms"] for r in rows],
                },
                {
                    "type": "bar",
                    "name": "broker p99",
                    "x": names,
                    "y": [r["broker"]["p99_ms"] for r in rows],
                },
            ],
            "layout": {"barmode": "group", "yaxis": {"title": "ms"}},
        },
        {
            "title": "Broker overhead (broker - direct)",
            "data": [
                {
                    "type": "bar",
                    "name": "mean (exact)",
                    "x": names,
                    "y": [r["overhead"]["mean_ms"] for r in rows],
                },
                {
                    "type": "bar",
                    "name": "p50 (indicative)",
                    "x": names,
                    "y": [r["overhead"]["p50_ms"] for r in rows],
                },
            ],
            "layout": {"barmode": "group", "yaxis": {"title": "ms"}},
        },
    ]
    return charts


def overhead_trend(runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    if len(runs) < 2:
        return None
    series: dict[str, tuple[list[str], list[float]]] = {}
    for run in runs:
        for row in run["rows"]:
            if not row.get("overhead"):
                continue
            key = f"{row['case']} @{row['rate']}"
            xs, ys = series.setdefault(key, ([], []))
            xs.append(_label(run))
            ys.append(row["overhead"]["p50_ms"])
    return {
        "title": "Overhead p50 over runs",
        "data": [
            {"type": "scatter", "mode": "lines+markers", "name": k, "x": xs, "y": ys}
            for k, (xs, ys) in sorted(series.items())
        ],
        "layout": {"yaxis": {"title": "ms"}},
    }


def dbcost_chart(run: dict[str, Any]) -> list[dict[str, Any]]:
    rows = run["rows"]
    names = [f"{r['variant']} {r['caller']}" for r in rows]
    roles = ("registry_user", "control_user", "admin_user")
    charts = []
    for metric, title in (
        ("statements", "Statements per call"),
        ("transactions", "Transactions per call"),
    ):
        charts.append(
            {
                "title": f"{title} — {_label(run)}",
                "data": [
                    {
                        "type": "bar",
                        "name": role.split("_")[0],
                        "x": names,
                        "y": [r["per_call"][role][metric] for r in rows],
                    }
                    for role in roles
                ],
                "layout": {"barmode": "stack"},
            }
        )
    return charts


def locust_table() -> str:
    files = sorted(RESULTS_DIR.glob("*_stats.csv"), key=lambda p: p.stat().st_mtime)
    if not files:
        return ""
    latest = files[-1]
    with latest.open() as fh:
        rows = list(csv.DictReader(fh))
    cols = ["Type", "Name", "Request Count", "Failure Count", "50%", "95%", "99%", "Requests/s"]
    head = "".join(f"<th>{html.escape(c)}</th>" for c in cols)
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(r.get(c, '')))}</td>" for c in cols) + "</tr>"
        for r in rows
    )
    return f"<h2>Locust — {html.escape(latest.name)}</h2><table><tr>{head}</tr>{body}</table>"


def render(charts: list[dict[str, Any]], extra_html: str) -> str:
    divs, scripts = [], []
    for i, chart in enumerate(charts):
        layout = {"title": {"text": chart["title"]}, "height": 460, **chart.get("layout", {})}
        divs.append(f'<div id="c{i}" class="chart"></div>')
        scripts.append(
            f"Plotly.newPlot('c{i}', {json.dumps(chart['data'])}, {json.dumps(layout)}, "
            "{responsive: true});"
        )
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Broker perf report</title>
<script src="{PLOTLY_CDN}"></script>
<style>
body {{ font-family: -apple-system, system-ui, sans-serif; margin: 2rem; color: #222; }}
.chart {{ margin-bottom: 2rem; }}
table {{ border-collapse: collapse; font-size: 13px; }}
td, th {{ border: 1px solid #ddd; padding: 4px 8px; text-align: right; }}
td:nth-child(2), th:nth-child(2) {{ text-align: left; }}
.note {{ color: #666; font-size: 13px; max-width: 70rem; }}
</style></head><body>
<h1>Broker perf report</h1>
<p class="note">Overhead = broker - direct, same request, same rate, open loop (oha, in-network).
Mean deltas are exact; percentile deltas are indicative (percentiles do not subtract).
Docker Desktop numbers are for relative comparison, not absolute SLOs.</p>
{"".join(divs)}
{extra_html}
<script>{"".join(scripts)}</script>
</body></html>"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render results/*.json into results/report.html")
    parser.add_argument("--out", type=Path, default=RESULTS_DIR / "report.html")
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args(argv)

    charts: list[dict[str, Any]] = []
    overhead = load("overhead")
    if overhead:
        charts += overhead_latest(overhead[-1])
        trend = overhead_trend(overhead)
        if trend:
            charts.append(trend)
    dbcost = load("dbcost")
    if dbcost:
        charts += dbcost_chart(dbcost[-1])
    if not charts:
        print("no results yet — run make perf-overhead / perf-dbcost first", file=sys.stderr)
        return 1
    args.out.write_text(render(charts, locust_table()))
    print(f"wrote {args.out}")
    if args.open:
        webbrowser.open(args.out.as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
