# `internal/localagent` — OS primitives for the local-agent sandbox

This package holds the OS-level primitives behind `jentic run`: the registry of
known coding agents, and the helpers that create/probe/grant/confine/launch as a
dedicated agent user. The command layer that orchestrates them (prompts, config
persistence, cobra wiring) lives in
[`internal/cli/localagentcmd`](../cli/localagentcmd/) — that package is the
**only** one that performs privileged host mutation, and this one produces the
`*exec.Cmd` values and pure data it runs.

Operator-facing guide: [`docs/guides/local-agent.md`](../../../docs/guides/local-agent.md).
Security design: [`docs/security/same-host/`](../../../docs/security/same-host/README.md).

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
| `doc.go` | The package doc: what the package is for and the security model it implements |
| `descriptor.go` | `Descriptor`/`Registry` (known agents), `SkillOnly`, `Known`/`Lookup` |
| `account.go` | Agent account naming and default home/config paths, account lifecycle (create/delete commands, full name, operator home grant) and the sudoers rule install/remove |
| `acl.go` | The traverse-walk + rwx-leaf ACL grant model: binary/dir-access probes, ancestor traverse grants, leaf grants and their revokes |
| `homedir.go` | Agent home paths and ownership: reown/reclaim/delete, identity and profile removal, `IsUnderHome`, managed-home lookup and verification |
| `agentexec.go` | Running commands as the agent user: the login/no-login bash argv, the probe `PATH`, the sensitive-env scrub and launch env allowlist, `runAsAgent` |
| `binaries.go` | Agent `PATH` setup (local bin, shared bin dirs), operator binary lookup, copying/installing binaries into the agent home |
| `seedconfig.go` | Config seeding from the operator's home (safe sources, copy/extract scripts, token caches) and secret/config scrubbing |
| `provider.go` | Model-provider detection from the operator's config, provider config paths, agent path probes |
| `danger.go` | `Classify` path bans (`DangerVerdict`), canonicalization and case-aware path comparison, `OperatorHome` |
| `workspaces.go` | Trusted-workspace discovery from the operator's agent config |
| `confine.go` | Per-session confinement: prereq probing (`AgentUserPrereqs`), `SessionAccess` (single source of truth for what a session reaches), SBPL profile builder (macOS), `bwrap` argv builder (Linux), `ConfineLaunchCmd` |
| `serviceaccount.go` | The MCP service-account profile (`_jentic-<runtime>`): account + 0700 home creation, root-side context export, the argv-pinned MCP sudoers rule, teardown |
| `servicebinary.go` | The root-owned jentic copy (`/usr/local/libexec/jentic/jentic`) the MCP sudoers rule and sudo-shim entry pin: atomic root-side install/refresh, removal, staleness check |
| `validate.go` | The injection choke point: `ValidateAgentUser`, `ValidateHomeDir`, `ValidateGrantPath` — every operator-editable value is constrained here before it reaches a shell, sudoers line, ACL entry, or SBPL profile |
| `operators_test.go` | Registry ↔ `internal/skillgen` parity guardrail, descriptor well-formedness, adversarial scrub-path containment |

## Adding a new runnable agent

One data row, no code: add a `Descriptor` to `Registry` in `descriptor.go`
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
