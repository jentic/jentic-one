# Running local coding agents on the credential boundary

> **Status:** implemented and shipping in the `jentic` CLI (account creation,
> `jentic run` with per-session confinement, directory grants, and `jentic
> reset`). Two follow-ups remain deferred and are called out as such below.

Jentic One keeps stored credentials out of credential read APIs and injects them
inside the Broker. Upstream responses pass through, so upstream trust remains
part of the boundary. The one-line install can place the agent and Jentic One
under the **same OS user on the same machine**, where a compromised or
prompt-injected agent can read the keys straight off disk. These docs work
through isolating the agent as its own unprivileged Unix user so the isolated
posture is the default.

> **Just want to use it?** The operator-facing guide — flow, examples, and
> troubleshooting for `jentic setup` / `jentic run` / `jentic reset` — is
> [`docs/local-agent.md`](../../local-agent.md). The documents below are the
> security analysis and design rationale behind it.

| Doc | What it covers |
| --- | -------------- |
| [`analysis.md`](analysis.md) | **The problem.** The same-user attack paths (AP-1…AP-4), why isolating the *agent* is strictly stronger than isolating Jentic One, and the state before this work. |
| [`local-agent-isolation.md`](local-agent-isolation.md) | **The design.** Run the agent as a dedicated Unix user, wrapped by the `jentic` CLI: account creation folded into `setup` / `wizard`, then `jentic run <agent>`, directory grants, and `jentic reset`. |
| [`filesystem-access-model.md`](filesystem-access-model.md) | **The permission mechanics.** How the agent's and operator's accounts reach — and are kept out of — each other's files: traverse-walk + rwx-leaf ACLs, the inherited operator grant, per-session process confinement, and the grant/revoke/reset lifecycle. |
| [`sandbox-exec-plan.md`](sandbox-exec-plan.md) | **The confinement layer.** The per-session process confinement (`sandbox-exec` on macOS, `bwrap` on Linux) that closes the sibling-traversal leak and replaces the `chmod 700 ~` default-deny: why it's a targeted human-home deny, why it errors closed, and the non-negotiable boundaries (whole-`/Users`+`/home` deny, read-only exec routes). |
| [`agent-home-under-operator-home-plan.md`](agent-home-under-operator-home-plan.md) | **A deferred plan (not implemented).** What it would take to default the agent home to `~/jentic-agents/<agent>` instead of the shared parent — the one new security tradeoff (a persistent execute ACE on `~`), and the open questions to resolve first. |
| [`profile-run-as-agent-plan.md`](profile-run-as-agent-plan.md) | **A deferred, pre-V2 plan (superseded).** Written against the V1 `profile` model — switching the active profile to an agent-owned one should run subsequent operations *as* the agent's Unix user. The flat `profile` surface was removed in the V2 activation (only a hidden deprecated shim → `context` remains); the concept now maps to contexts/identities. Kept for the run-as design rationale, not as a description of the live CLI. |

For the broader deployment guidance (separate host / network postures) see
[`../hardening.md`](../hardening.md).
