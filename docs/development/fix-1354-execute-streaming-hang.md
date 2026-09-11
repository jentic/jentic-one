# Fix: `jentic execute` hangs on a non-interactive stdin

**Issue:** [jentic/jentic-one#1354](https://github.com/jentic/jentic-one/issues/1354)
**Branch:** `fix/1354-execute-streaming-hang`
**Status:** fixed (CLI); regression test added

> **Correction.** The title and the first-pass analysis of this bug blamed the
> broker's streamed-response path / keep-alive connection reuse. A deterministic
> local-stub reproduction (below) disproved that. The real cause is unrelated to
> the broker, streaming, or upstream rate limits — it is a blocking
> `io.ReadAll(os.Stdin)` in the CLI. The earlier broker-leg mitigation was
> reverted.

## Symptom

`jentic execute <op>` (no request body) hangs indefinitely when run from any
context whose stdin is **not a TTY and never sends EOF** — a backgrounded
process, or an interactive agent/harness whose stdin is a socket/pty. The first
observation was against Alpha Vantage, which made it look upstream-related; it
is not.

## Root cause

`cli/internal/cli/api/execute.go`, request-body resolution. The old condition:

```go
case opts.data == "-" || (opts.data == "" && opts.dataFile == "" && !term.IsTerminal(os.Stdin.Fd())):
    data, readErr := io.ReadAll(os.Stdin)   // blocks until EOF
```

The `!term.IsTerminal(os.Stdin.Fd())` heuristic reads stdin whenever it is not a
terminal. That is true not only for a real `echo … | execute` but also for an
**inherited, idle, non-TTY fd** (backgrounded process, agent/harness). On those,
`io.ReadAll` blocks forever waiting for an EOF that never arrives, hanging every
body-less execute.

### How it was proven

A local stub broker (no network, no quota) returning a tiny HTTP-200 body
reproduced the hang deterministically:

- `jentic execute GET:/tiny --broker-host <stub>` (inherited non-TTY stdin) —
  **hung >120s**.
- The Go `SIGQUIT` stack dump showed the main goroutine blocked in
  `io.ReadAll` at `execute.go:258` (the stdin read) — **before** the broker
  request is ever built or sent.
- The identical command with `< /dev/null` returned in **0s** — an
  EOF-terminated stdin never blocks.
- Direct `curl` to the broker always returned <1s; the broker logged nothing for
  the hung runs, confirming the hang is client-side, pre-send.

This ruled out the broker, streaming, keep-alive reuse, and Alpha Vantage rate
limits (the 369-byte "Information" notice was a red herring — the body size is
irrelevant; the CLI never reads the response because it is stuck on stdin).

## Fix

Split the implicit stdin fallback from the explicit one and gate the implicit
read on the stdin fd's *shape*:

- `-d -` (explicit) — read stdin, blocking to EOF. The caller opted in.
- No body flag — read stdin **only** when `stdinHasPipedBody(os.Stdin)` is true:
  a named pipe (`ModeNamedPipe`) or a non-empty regular file. Every other
  shape (a TTY, a char device, an idle inherited non-TTY fd) is skipped, so
  `io.ReadAll` can never block.

This preserves the two legitimate body inputs — `echo … | execute` and
`execute < file` — while removing the hang on idle inherited stdin.

### Known, accepted trade-off

A shell pipe (`producer | execute`) classifies as a body source even before the
producer flushes, so a producer that never writes would still block the read.
That is the correct, least-surprising behavior for an explicit `|` (the same as
`cat | grep`) and is not the reported bug. The bug was the non-pipe idle fd,
which is now excluded.

## Verification

- Stub repro, inherited non-TTY stdin: previously hung >120s → now `0s`, exit 0.
- `echo -n '{…}' | execute POST:/ok` — body still sent (stub logs the POST).
- `-d -` with piped data — works; `-d -` with no data — blocks by design (opt-in).
- Unit test `TestStdinHasPipedBody` covers pipe / non-empty file / empty file /
  idle fd / directory / nil.
- `go build ./...`, `go vet`, package tests, arch and golden suites all pass.

## Files

- `cli/internal/cli/api/execute.go` — split the stdin cases; add
  `stdinHasPipedBody`; drop the now-unused `term` import.
- `cli/internal/cli/api/execute_test.go` — `TestStdinHasPipedBody`.
