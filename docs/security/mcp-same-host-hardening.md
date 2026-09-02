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
key; recipes 2 and 3 move that behind a boundary too (of differing strength
— see each recipe's residual risk).

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
   `sudo -u _jentic-claude jentic register --url <install URL> --env local
   --name claude` and approves it), so the context and key files are
   created in the service user's own config dir. Pass `--env`/`--name`
   explicitly so the derived context name is predictable; a remote install
   also needs `--broker-url`.
3. **Rename the derived context** (run as the service user). Registration
   derives the context name as `<env>-<name>` (here `local-claude`; env
   defaults to the first DNS label of the install URL and name to the
   hostname) — it never creates a context named `claude` on its own.
   Rename it so the sudoers pin and MCP entry below can use the short
   name:

   ```
   sudo -u _jentic-claude jentic context rename local-claude claude
   ```

4. **Install an argv-pinned NOPASSWD sudoers line** (via `visudo -f
   /etc/sudoers.d/jentic-claude`):

   ```
   yourdesktopuser ALL=(_jentic-claude) NOPASSWD: /usr/local/bin/jentic mcp --context claude
   ```

   One source user → one target user → the exact `jentic mcp --context
   <name>` argv. `sudo` matches the full argv, so the entry cannot be
   replayed with a different context or subcommand.
5. **Point the MCP entry at the shim** (`-n` because GUI spawns cannot answer
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
The keys live in a named volume rather than in the desktop user's home, so
ordinary file reads by desktop-user processes cannot reach them (but see the
residual risk below — Docker daemon access can).

**Bring your own image.** No official CLI image is published today — build
one from the released `jentic` binary. The steps below assume an image that
provides: the `jentic` binary on `PATH`; a non-root user (uid 10001 here)
with a writable `HOME` (`/home/jentic`); and the config directory
`$HOME/.config/jentic` pre-created and **owned by that user**, so the named
volume initializes with the right ownership on first mount (Docker seeds an
empty named volume from the image's content at the mount path). A minimal
example:

```dockerfile
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 10001 --create-home jentic \
 && mkdir -p /home/jentic/.config/jentic \
 && chown -R jentic:jentic /home/jentic
# The released jentic CLI binary for the image's OS/arch.
COPY jentic /usr/local/bin/jentic
USER 10001:10001
ENV HOME=/home/jentic
```

1. **Register the agent once inside the container** (state persists in the
   named volume; your operator approves the registration). An instance
   listening on the host's loopback is reached as `host.docker.internal`
   (add `--add-host=host.docker.internal:host-gateway` on Linux) — which is
   **not** loopback from the container's point of view, so `register` does
   not auto-seed the broker URL. Pass `--broker-url` explicitly or
   `execute` fail-closes:

   ```
   docker run -i --rm \
     --add-host=host.docker.internal:host-gateway \
     -v jentic-mcp-claude:/home/jentic/.config/jentic \
     your-jentic-cli-image \
     jentic register --url http://host.docker.internal:8000 \
       --broker-url http://host.docker.internal:8100 \
       --env local --name claude
   ```

2. **Rename the derived context.** Registration derives the context name as
   `<env>-<name>` (here `local-claude`; env defaults to the first DNS label
   of the install URL and name to the hostname — pass `--env`/`--name` as
   above so it is predictable). It never creates a context named `claude`
   on its own, so rename it to match the pin in the entry below:

   ```
   docker run --rm -v jentic-mcp-claude:/home/jentic/.config/jentic \
     your-jentic-cli-image jentic context rename local-claude claude
   ```

3. **Point the MCP entry at the container.** `--read-only` makes the rootfs
   unwritable, and `jentic mcp` opens its log sink (default: under the XDG
   state dir, on that read-only rootfs) *before* serving — so pass
   `--log-file` on a writable mount. A tmpfs is the simplest; use a second
   named volume instead if the log should survive the session:

   ```json
   {
     "command": "docker",
     "args": ["run", "-i", "--rm",
              "--read-only", "--cap-drop=ALL",
              "--security-opt", "no-new-privileges",
              "--user", "10001:10001",
              "--add-host=host.docker.internal:host-gateway",
              "--tmpfs", "/tmp",
              "-v", "jentic-mcp-claude:/home/jentic/.config/jentic",
              "your-jentic-cli-image",
              "jentic", "mcp", "--context", "claude",
              "--log-file", "/tmp/mcp.log"]
   }
   ```

Narrow the container's egress to the instance's address (e.g. a dedicated
Docker network or host firewall rules). The context's environment points at
`http://host.docker.internal:8000` (broker `:8100`), as seeded by the
registration step.

Residual risk: this rung's boundary is only as strong as access to the
Docker daemon — which the desktop user necessarily holds, since the MCP
entry itself runs `docker`. Any desktop-user process that can reach the
daemon can mount the named volume (`docker run -v jentic-mcp-claude:/x …`)
and read the key files, so unlike Recipe 2 the ladder invariant — the
desktop-user side never holds long-lived key material — does **not** hold
here against daemon-capable processes. What the rung buys is convenience,
containment of the *server* (read-only rootfs, dropped capabilities,
narrowed egress), and protection against casual file reads — not key
exfiltration by a shell-capable agent. Recipe 3 is the convenient rung
where Docker Desktop exists (and the only one on Windows); on isolation
strength, Recipe 2's uid boundary is the stronger of the two, and the
sudo-shim also serves docker-less machines.

## Recipe 4 — isolated local daemon (`jentic mcp --http`)

The top rung replaces per-runtime stdio processes with one socket-activated
daemon under a dedicated service user, reached through the credential-less
`jentic mcp --connect` relay: the desktop side holds no key material, no
sudoers line, and no Docker-daemon power — the OS's peer-credential check on
the daemon's unix socket is the boundary. It has its own page,
[mcp-daemon.md](mcp-daemon.md), and ready-made systemd/launchd templates in
`deploy/mcp-daemon/`.

## Choosing a rung

| Setup | Recommendation |
|---|---|
| Instance on another host / VPC | Recipe 1 (remote) already covers the instance files; add 2, 3 or 4 if the agent's own key must move off the desktop uid |
| Same host, Docker available | Recipe 1 (containerized instance) + Recipe 3 for convenience — but Recipe 3's key boundary yields to Docker-daemon access (see its residual risk); pick Recipe 2 or 4 where key exfiltration is the concern |
| Same host, no Docker (macOS/Linux) | Recipe 1 (different OS user) + Recipe 2, or Recipe 4 for a spawn-free entry with the same uid boundary |
| Trying it out, low-value credentials | Plain stdio entry — the supported default |
