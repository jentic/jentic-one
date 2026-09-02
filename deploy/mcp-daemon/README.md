# `jentic mcp --http` service templates

Socket-activated service definitions for the isolated local MCP daemon —
the top rung of the same-host hardening ladder in
[`docs/security/mcp-daemon.md`](../../docs/security/mcp-daemon.md). One
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

# 2. Provision the context the daemon will serve (as the service user).
sudo -u _jentic-mcp XDG_CONFIG_HOME=/var/lib/jentic-mcp/config \
    XDG_STATE_HOME=/var/lib/jentic-mcp/state \
    jentic setup   # or: jentic context add ...

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
sudo sysadminctl -addAccount _jentic-mcp ... # or dscl; a role account with its own home
sudo cp launchd/com.jentic.mcp.plist /Library/LaunchDaemons/
# Edit the marked EDIT lines (binary path, context, allowed uids), then:
sudo launchctl bootstrap system /Library/LaunchDaemons/com.jentic.mcp.plist
```

launchd holds the socket at `/var/run/jentic-mcp.sock` and spawns the daemon
on the first connection in inetd **wait** mode (the daemon adopts the
listening socket on fd 0 via `--from-launchd`); after the idle-exit, launchd
resumes holding it.

## Key custody

The service user's `XDG_CONFIG_HOME`/`XDG_STATE_HOME` (set in the units)
hold the context's key and token files with owner-only modes. The desktop
user cannot read them; a caller admitted by the socket can only *use* them —
every call is signed daemon-side and attributed to the daemon's context.
Rotate or revoke by re-provisioning that one context. Details and the
threat-model discussion: [`docs/security/mcp-daemon.md`](../../docs/security/mcp-daemon.md).
