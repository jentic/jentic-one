# Same-host setups: the threat model and your options

Jentic One keeps third-party credentials off the **network** path — the Broker
injects them; the agent only ever sees responses. That guarantee does not hold
on the **host** when the agent and Jentic One run as the same OS user on the
same machine (the default after a one-line local install): everything the
boundary depends on lives in files the agent's own user can read, so a
compromised or prompt-injected agent goes *around* the Broker rather than
through it.

The problem is stated once, canonically, in
[`threat-model.md`](threat-model.md) — the four attack paths **AP-1…AP-4**
that the docs below cite by code — along with the key structural fact:
isolating the *agent* closes all four paths; isolating *Jentic One* closes
only two.

## The options

More than one mitigation exists, they differ in strength, and they can be
combined. Pick by how the agent runs and what the credentials are worth:

| Option | What it closes | Where |
| ------ | -------------- | ----- |
| **Separate host / private network** — the instance never shares a machine with any agent | AP-1/AP-2 by construction (pair with operator-browser hygiene for AP-3); the strongest posture, and the recommended one for real credentials | [Deployment tiers, T3+](../security.md#deployment-tiers) |
| **Isolate the agent — `jentic run`** (shipped) — the CLI launches a coding agent as its own unprivileged Unix user, confined per session | AP-1…AP-4 on the machine it runs on | Design docs below; operator guide: [`guides/local-agent.md`](../../guides/local-agent.md) |
| **Isolate the instance** — run Jentic One under a different OS user, in a container, or rootless | AP-1/AP-2 only — the agent still shares a machine with the operator's browser, so **AP-3 stays open** | [Deployment tiers, T2](../security.md#deployment-tiers) |
| **MCP-spawned agents** — the runtime (Claude Desktop, Cursor, …) spawns `jentic mcp` as the desktop user; a different seam, same problem | A ladder of four recipes, from isolating the instance to a socket-activated daemon holding the keys | [`mcp-same-host-hardening.md`](mcp-same-host-hardening.md), [`mcp-daemon.md`](mcp-daemon.md) |
| **Generic sandboxing** — the agent's built-in sandbox, OS primitives, containers, microVMs | Defense-in-depth of varying strength; not a substitute for the rows above | [Sandboxing the agent](../security.md#sandboxing-the-agent-axis-a) |

> **Just want to use the shipped isolation?** The operator-facing guide —
> flow, examples, and troubleshooting for `jentic setup` / `jentic run` /
> `jentic reset` — is [`docs/guides/local-agent.md`](../../guides/local-agent.md).

## Option: `jentic run` — the design docs

The currently shipped implementation of agent isolation, and the security
analysis and design rationale behind it:

| Doc | What it covers |
| --- | -------------- |
| [`threat-model.md`](threat-model.md) | **The problem.** The same-user attack paths (AP-1…AP-4) and why isolating the *agent* is strictly stronger than isolating Jentic One. The design docs below cite its AP codes. |
| [`local-agent-isolation.md`](local-agent-isolation.md) | **The design.** Run the agent as a dedicated Unix user, wrapped by the `jentic` CLI: account creation folded into `setup` / `wizard`, then `jentic run <agent>`, directory grants, and `jentic reset`. |
| [`filesystem-access-model.md`](filesystem-access-model.md) | **The permission mechanics.** How the agent's and operator's accounts reach — and are kept out of — each other's files: traverse-walk + rwx-leaf ACLs, the inherited operator grant, per-session process confinement, and the grant/revoke/reset lifecycle. |
| [`sandbox-confinement-design.md`](sandbox-confinement-design.md) | **The confinement layer.** The per-session process confinement (`sandbox-exec` on macOS, `bwrap` on Linux) that closes the sibling-traversal leak and replaces the `chmod 700 ~` default-deny: why it's a targeted human-home deny, why it errors closed, and the non-negotiable boundaries (whole-`/Users`+`/home` deny, read-only exec routes). |

For the broader deployment guidance (network postures, tiers, the production
checklist) see [`../security.md`](../security.md).
