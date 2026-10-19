# CLI runner spikes (not for merge)

Throwaway spikes for a broker-side CLI runner (running vendor CLIs such as `gh`
and `aws` in a broker-managed sandbox). Each folder has a `RESULTS.md`.

| Spike | Question | Folder |
|---|---|---|
| 1 | Can the sandbox run under the chart's pod posture? | `sandbox/` |
| 2 | Can the registry route the calls a CLI makes (GitHub GraphQL, AWS hosts)? | `discovery/` + `tests/integration/registry/ingest/test_spike_cli_runner_discovery.py` |
| 3 | Which real commands escape endpoint-override wiring? | `wiring/` |
