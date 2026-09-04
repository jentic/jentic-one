# Running local coding agents on the credential boundary

> **Status:** implemented and shipping in the `jentic` CLI (account creation,
> `jentic run` with per-session confinement, directory grants, and `jentic
> reset`). Two follow-ups remain deferred and are called out as such below.

Jentic One keeps third-party credentials off the *network* path (the Broker
injects them; the agent only sees responses). But the one-line install lands most
users with the agent and Jentic One running as the **same OS user on the same
machine**, where that guarantee doesn't hold — a compromised or prompt-injected
agent can read the keys straight off disk. These docs work through isolating the
agent as its own unprivileged Unix user so the isolated posture is the default.

> **Just want to use it?** The operator-facing guide — flow, examples, and
> troubleshooting for `jentic setup` / `jentic run` / `jentic reset` — is
> [`docs/guides/local-agent.md`](../../guides/local-agent.md). The documents below are the
> security analysis and design rationale behind it.

| Doc | What it covers |
| --- | -------------- |
| [`analysis.md`](analysis.md) | **The problem.** The same-user attack paths (AP-1…AP-4) and why isolating the *agent* is strictly stronger than isolating Jentic One. The design docs below cite its AP codes. |
| [`local-agent-isolation.md`](local-agent-isolation.md) | **The design.** Run the agent as a dedicated Unix user, wrapped by the `jentic` CLI: account creation folded into `setup` / `wizard`, then `jentic run <agent>`, directory grants, and `jentic reset`. |
| [`filesystem-access-model.md`](filesystem-access-model.md) | **The permission mechanics.** How the agent's and operator's accounts reach — and are kept out of — each other's files: traverse-walk + rwx-leaf ACLs, the inherited operator grant, per-session process confinement, and the grant/revoke/reset lifecycle. |
| [`sandbox-confinement-design.md`](sandbox-confinement-design.md) | **The confinement layer.** The per-session process confinement (`sandbox-exec` on macOS, `bwrap` on Linux) that closes the sibling-traversal leak and replaces the `chmod 700 ~` default-deny: why it's a targeted human-home deny, why it errors closed, and the non-negotiable boundaries (whole-`/Users`+`/home` deny, read-only exec routes). |

For the broader deployment guidance (separate host / network postures) see
[`../security.md`](../security.md).
