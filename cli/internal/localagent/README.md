# `internal/localagent` — OS primitives for the local-agent sandbox

This package holds the OS-level primitives behind `jentic run`: the registry of
known coding agents, and the helpers that create/probe/grant/confine/launch as a
dedicated agent user. The command layer that orchestrates them (prompts, config
persistence, cobra wiring) lives in
[`internal/cli/localagentcmd`](../cli/localagentcmd/) — that package is the
**only** one that performs privileged host mutation, and this one produces the
`*exec.Cmd` values and pure data it runs.

Operator-facing guide: [`docs/local-agent.md`](../../../docs/local-agent.md).
Security design: [`docs/security/local-agent/`](../../../docs/security/local-agent/README.md).

## Layering contract

- **Stdlib-only.** Production code here imports nothing but the standard
  library — no cobra, no `internal/config`, no API client. Keep it that way:
  the command layer stays a thin orchestrator, and this package stays testable
  without a CLI.
- **Commands out, execution up.** Functions return `*exec.Cmd` /
  `[]AccountStep`; callers decide when to run them, wire stdio, and handle
  errors. The few direct probes (`UserExists`, `ProbeBinary`, `DirAccess`,
  `AgentACLPresent`) are read-only.
- **Durable state lives elsewhere.** The agent-account record and grant
  inventory are `internal/config`'s `AgentState` (persisted at
  `~/.config/jentic/agent-account.yaml`); this package only reads the
  agent's own JSON files (`~/.claude.json`, `~/.claude/settings.json`) under
  the operator's home.

## File map

| File | What it holds |
| ---- | ------------- |
| `localagent.go` | `Descriptor`/`Registry` (known agents), account lifecycle commands, the traverse-walk + rwx-leaf ACL grant model, config/provider seeding + secret scrubbing, `Classify` path bans, sudoers install/remove, trusted-workspace discovery |
| `confine.go` | Per-session confinement: prereq probing (`AgentUserPrereqs`), `SessionAccess` (single source of truth for what a session reaches), SBPL profile builder (macOS), `bwrap` argv builder (Linux), `ConfineLaunchCmd` |
| `validate.go` | The injection choke point: `ValidateAgentUser`, `ValidateHomeDir`, `ValidateGrantPath` — every operator-editable value is constrained here before it reaches a shell, sudoers line, ACL entry, or SBPL profile |
| `operators_test.go` | Registry ↔ `internal/skillgen` parity guardrail, descriptor well-formedness, adversarial scrub-path containment |

## Adding a new runnable agent

One data row, no code: add a `Descriptor` to `Registry` in `localagent.go`
(ID, binary, probe paths, install command, `SingleBinary`, `ConfigPaths`, and —
if the agent keeps a separable credential file inside its config tree —
`SecretConfigPaths` so seeding scrubs it). If the agent is a skill target but
has no runnable binary, add it to `SkillOnly` instead. The parity test in
`operators_test.go` fails until every `skillgen` operator is accounted for in
exactly one of the two sets. If the agent records trusted workspaces, wire a
reader into `TrustedWorkspaces` (it dispatches on the descriptor).

## Security invariants — don't loosen casually

- **Validate at the source, quote at the sink.** Account names and paths are
  constrained by `validate.go` *and* re-checked/quoted at each sink
  (`shellQuote`, `sbplPath` control-char stripping, direct argv with `--`).
- **Fail closed.** Confinement unavailable → the launch must be refused
  (`ConfinementAvailable`); an inconclusive userns probe reads as disabled;
  `VerifyManagedHome` gates every privileged chown/rm of the home.
- **Never dereference symlinks** in privileged recursion: `chown -Rh`,
  `cp -RP`, `find ! -type l`, `EvalSymlinks` before classification.
- **Absolute wrapper paths.** `sandbox-exec`/`bwrap` are resolved in the
  trusted operator process; an agent-controlled PATH must never pick them.
- **Read-only exec routes, emitted last** (SBPL last-match / bwrap mount
  order), including the agent's own `~/.local/bin` — the self-rewrite guard.
