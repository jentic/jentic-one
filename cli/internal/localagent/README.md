# `internal/localagent` — agent-account teardown primitives

This package holds the OS-level primitives `jentic reset` uses to remove agent
accounts that older jentic releases provisioned on a machine. Nothing in this
CLI creates them; the package exists so machines that still carry one can be
cleaned:

- the dedicated `<operator>-local-agent` Unix user — its named-user directory
  ACLs (read/write leaf grants and execute-only ancestor traverse grants), its
  passwordless-launch line in `/etc/sudoers.d/jentic-agent`, the identity and
  seeded agent/provider config in its home, and the account itself;
- the per-runtime `_jentic-<runtime>` MCP service accounts — their sudoers
  lines, their 0700 homes holding exported key material, and the root-owned
  jentic copy at `/usr/local/libexec/jentic/jentic` they ran.

The command layer that orchestrates the teardown (survey, plan, confirmation,
ordered execution) lives in [`internal/cli/api/reset*.go`](../cli/api/).
`jenticctl doctor` probe for leftovers read-only and point at
`jentic reset`.

## Layering contract

- **Stdlib-only.** Production code here imports nothing but the standard
  library — no cobra, no `internal/config`, no API client.
- **Commands out, execution up.** Functions return `*exec.Cmd` /
  `[]AccountStep`; callers decide when to run them, wire stdio, and handle
  errors. The few direct probes (`UserExists`, `AgentACLPresent`,
  `ServiceBinaryInstalled`, `LookupHomeDir`) are read-only.
- **Durable state lives elsewhere.** The agent-account record (user, home,
  granted dirs) is `internal/config`'s `AgentState`, read from
  `~/.config/jentic/agent-account.yaml` (with a read-only fallback to the
  legacy `~/.jentic/config.yaml`).

## File map

| File | What it holds |
| ---- | ------------- |
| `localagent.go` | Account teardown commands (ACL revokes, home re-own/delete, identity + seeded-config scrub, sudoers line removal, account delete), the ACL-presence probe, and `VerifyManagedHome` |
| `serviceaccount.go` | MCP service-account naming and its teardown steps |
| `servicebinary.go` | The root-owned jentic copy's path, presence probe, and removal |
| `validate.go` | The injection choke point: `ValidateAgentUser`, `ValidateHomeDir`, `ValidateGrantPath` — every recorded, hand-editable value is constrained here before it reaches a privileged command |

## Security invariants — don't loosen casually

- **Validate at the source, quote at the sink.** Recorded names and paths are
  constrained by `validate.go` *and* quoted at each sink (`shellQuote`, direct
  argv with `--`).
- **Fail closed.** `VerifyManagedHome` gates every privileged chown/rm of a
  home: the account's live home must equal the recorded managed home.
- **Never dereference symlinks** in privileged recursion: `find` (default `-P`
  walk) with `chown -h`, and multiply hard-linked files are skipped.
