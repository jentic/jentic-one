# Hardening same-host MCP setups

The local `jentic mcp` stdio server (available in the `jentic` CLI from the
next release — check `jentic mcp --help`) is spawned by the agent runtime
itself: a GUI runtime (Claude Desktop, Cursor, …) starts the process as the
**desktop user**, and there is no seam to interpose a different Unix user the
way `jentic run` does for agents the CLI launches. When the Jentic One
instance runs on the **same host**, that default has two consequences:

- The MCP server process — and the context's key/token files it reads — run
  and live under the desktop user's uid.
- If the instance also runs as that same OS user, a shell-capable agent can
  read the instance's config, database, and encryption key directly off disk,
  bypassing the broker. (This is not MCP-specific — any same-user local agent
  can do it today — but MCP makes same-host setups more common.)

Two things to keep straight before choosing a recipe:

- **The API-level credential boundary holds in every mode.** Upstream
  credentials are injected by the broker server-side and are never returned
  to the agent — no recipe below changes that, and no recipe is needed for
  it. What the recipes close is the **OS-level** exposure of the agent's
  signing key and the instance's files in same-host setups.
- **Plain stdio remains the supported default.** The recipes are additive
  hardening for stricter environments. Every rung keeps the same MCP entry
  shape: a command to spawn.

Pick the lowest rung that matches the sensitivity of the credentials your
instance holds (the general tier ladder is in [hardening.md](hardening.md)).

## Recipe 1 — isolate the instance

Close the instance-file half entirely by making sure the instance's files are
not readable by the desktop user:

- **Containerized instance**: `jenticctl install`'s Docker deployment already
  keeps the database and key material inside container volumes not shared
  with the desktop user's home.
- **Different OS user**: run the instance under a dedicated non-root user (or
  rootless container) so its key/DB are not readable by the agent's uid —
  tier T2 in the [hardening guide](hardening.md#deployment-tiers).
- **Remote instance**: point the MCP entry's context at an instance on
  another host/VM/private network (tier T3) — the agent cannot touch the
  key store by construction.

With any of these, the desktop side still holds the *agent's own* signing
key; recipes 2 and 3 move that behind a boundary too.

## Recipe 2 — sudo-shim entry with a per-agent service user

Run the MCP server (and its key/token files) under a dedicated service user,
and let the agent runtime spawn it through an argv-pinned `sudo` line. `sudo`
inherits stdin/stdout, so the JSON-RPC pipe survives the user switch; the
desktop side is left holding only a disposable spawn line, while the process
and the context's key/token files live under the service user, unreadable by
the desktop user. macOS/Linux only; this is a manual recipe today — a later
release automates it as an optional `jentic setup` isolation step.

1. **Create one service user per agent identity/runtime** (`_jentic-claude`,
   `_jentic-cursor`, …) — *not* a shared catch-all user, which would
   co-locate every runtime's keys and recreate the shared-key failure mode
   that one-agent-per-runtime exists to kill. One runtime ↔ one agent ↔ one
   context ↔ one uid ↔ one sudoers line; revoking a runtime = disable the
   agent + delete the user. Service-account hygiene: system-uid range, `_`
   prefix (the macOS convention), no login shell, state under the service
   user's own `0700` home. Never use setuid wrappers.
2. **Register the agent under the service user** (your operator runs
   `sudo -u _jentic-claude jentic register --url <install URL> …` and
   approves it), so the context and key files are created in the service
   user's own config dir.
3. **Install an argv-pinned NOPASSWD sudoers line** (via `visudo -f
   /etc/sudoers.d/jentic-claude`):

   ```
   yourdesktopuser ALL=(_jentic-claude) NOPASSWD: /usr/local/bin/jentic mcp --context claude
   ```

   One source user → one target user → the exact `jentic mcp --context
   <name>` argv. `sudo` matches the full argv, so the entry cannot be
   replayed with a different context or subcommand.
4. **Point the MCP entry at the shim** (`-n` because GUI spawns cannot answer
   prompts):

   ```json
   {
     "command": "sudo",
     "args": ["-n", "-u", "_jentic-claude",
              "/usr/local/bin/jentic", "mcp", "--context", "claude"]
   }
   ```

**Env boundary (by design):** `sudo`'s default `env_reset` means environment
variables do **not** cross into the service user. The MCP entry's `env`
block, `$JENTIC_CONTEXT`, and the file-less overrides
(`JENTIC_BASE_URL`/`JENTIC_BEARER_TOKEN`) are all incompatible with the
sudo-shim entry — the context comes from the pinned argv and the config from
the service user's own files. An orchestrator-set `$JENTIC_SESSION_ID` does
not cross either, so under the shim the server's own per-process session UUID
is always the session key. **Never add `env_keep` for `JENTIC_*`** — an
env-writable desktop user could otherwise re-point the isolated server at
another instance or token and defeat the boundary.

Residual risk: any process running as the desktop user can invoke the shim —
impersonation *within* the audited channel, bounded by the entry's pinned
context and the agent's server-side scopes. Key exfiltration is blocked: the
desktop-user side never holds long-lived key material.

## Recipe 3 — container entry

Where Docker Desktop is available (including Windows), spawn the MCP server
in a container instead — the MCP ecosystem's mainstream isolation pattern.
The keys live in a named volume no desktop-user process mounts:

```json
{
  "command": "docker",
  "args": ["run", "-i", "--rm",
           "--read-only", "--cap-drop=ALL",
           "--security-opt", "no-new-privileges",
           "--user", "10001:10001",
           "-v", "jentic-mcp-claude:/home/jentic/.config/jentic",
           "<your-jentic-cli-image>", "jentic", "mcp", "--context", "claude"]
}
```

Narrow the container's egress to the instance's address (e.g. a dedicated
Docker network or host firewall rules). An instance listening on the host's
loopback needs `host.docker.internal` plumbing
(`--add-host=host.docker.internal:host-gateway` on Linux) and a context whose
environment points at `http://host.docker.internal:8000`. Register the agent
once inside the container (state persists in the named volume).

Prefer this rung where Docker Desktop exists; the sudo-shim serves
docker-less machines.

## Choosing a rung

| Setup | Recommendation |
|---|---|
| Instance on another host / VPC | Recipe 1 (remote) already covers the instance files; add 2 or 3 if the agent's own key must move off the desktop uid |
| Same host, Docker available | Recipe 1 (containerized instance) + Recipe 3 |
| Same host, no Docker (macOS/Linux) | Recipe 1 (different OS user) + Recipe 2 |
| Trying it out, low-value credentials | Plain stdio entry — the supported default |
