# `jentic mcp --http` service templates

Socket-activated service definitions for the isolated local MCP daemon —
the top rung of the same-host hardening ladder in
[`docs/security/same-host/mcp-daemon.md`](../../docs/security/same-host/mcp-daemon.md). One
daemon holds ONE context's keys under a dedicated service user; agent
runtimes reach it credential-lessly through
`jentic mcp --connect unix:///run/jentic-mcp/mcp.sock`.

| File | What it is |
| ---- | ---------- |
| `systemd/jentic-mcp.socket` | Socket unit owning `/run/jentic-mcp/mcp.sock` (Linux) |
| `systemd/jentic-mcp.service` | The daemon unit systemd spawns on first connection |
| `launchd/com.jentic.mcp.plist` | LaunchDaemon equivalent for macOS (inetd-wait socket activation) |

Both templates rely on the daemon's own behavior: it inherits the activated
socket (`LISTEN_FDS` / `--from-launchd`), verifies every connection's peer
uid, and exits after `--idle-timeout` with no requests so nothing lingers
holding keys — the init system respawns it on the next connection.

## Install (Linux, systemd)

```sh
# 1. A dedicated service user; its home holds the context config + keys.
sudo useradd --system --home-dir /var/lib/jentic-mcp --create-home \
    --shell /usr/sbin/nologin _jentic-mcp
# useradd honors login.defs (HOME_MODE is 0755 on Debian-family) — make the
# key custody structural, not dependent on per-file modes alone:
sudo chmod 700 /var/lib/jentic-mcp

# 2. Provision the context the daemon will serve (as the service user).
sudo -u _jentic-mcp XDG_CONFIG_HOME=/var/lib/jentic-mcp/config \
    XDG_STATE_HOME=/var/lib/jentic-mcp/state \
    jentic setup   # or: jentic context create ...

# 3. Install the units, then edit both marked EDIT lines
#    (context name, and the desktop uid(s) allowed to connect).
sudo cp systemd/jentic-mcp.socket systemd/jentic-mcp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now jentic-mcp.socket
```

Point the agent runtime's MCP entry at the relay:

```json
{ "command": "jentic", "args": ["mcp", "--connect", "unix:///run/jentic-mcp/mcp.sock"] }
```

The socket is group-connectable (`SocketGroup=_jentic-mcp`, mode `0660`), so
add each allowed desktop user to the group **and** list their uid in the
service's `--allow-uid` — the filesystem mode and the daemon's peer-cred
check are independent layers, and both must pass.

## Install (macOS, launchd)

```sh
sudo sysadminctl -addUser _jentic-mcp ... # a role account with its own home (or dscl)
sudo chmod 700 /var/lib/jentic-mcp        # or wherever the account's home landed
sudo cp launchd/com.jentic.mcp.plist /Library/LaunchDaemons/
# Edit the marked EDIT lines (binary path, context, allowed uids), then:
sudo launchctl bootstrap system /Library/LaunchDaemons/com.jentic.mcp.plist
```

launchd holds the socket at `/var/run/jentic-mcp.sock` and spawns the daemon
on the first connection in inetd **wait** mode (the daemon adopts the
listening socket on fd 0 via `--from-launchd`); after the idle-exit, launchd
resumes holding it.

**The macOS socket is world-connectable (`SockPathMode` 0666) by design.**
launchd creates the socket as `root:wheel` before switching to `UserName`,
and `launchd.plist(5)` offers no `SockPathOwner`/`SockPathGroup` — a 0660
socket would admit nobody but root. So on macOS there is **no filesystem
permission layer**: the daemon's per-connection peer-credential check
(`--allow-uid`) is the gate, and it fails closed for every uid off the
list. That is a sound posture — the peer-cred check is kernel-asserted and
evaluated on every connection — but it means the `--allow-uid` list is the
ONLY thing standing between a local uid and the daemon; keep it tight. (On
Linux, `SocketGroup` + 0660 remains an additional, independent layer.)

## Key custody

The service user's `XDG_CONFIG_HOME`/`XDG_STATE_HOME` (set in the units)
hold the context's key and token files with owner-only modes. The desktop
user cannot read them; a caller admitted by the socket can only *use* them —
every call is signed daemon-side and attributed to the daemon's context.
Rotate or revoke by re-provisioning that one context. Details and the
threat-model discussion: [`docs/security/same-host/mcp-daemon.md`](../../docs/security/same-host/mcp-daemon.md).
