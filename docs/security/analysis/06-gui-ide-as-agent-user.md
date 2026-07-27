# Can a GUI IDE (Cursor / VS Code) run as the agent user?

> Companion to [`05-agent-as-own-unix-user.md`](05-agent-as-own-unix-user.md),
> which covered CLI-initiated agents. This doc answers the harder case flagged
> there: an agent driven through an IDE UI. Focus macOS, with Linux notes.
> Doc URLs are given inline; treat product specifics as of 2026-07.

## Bottom line

**Don't try to launch the IDE *GUI* as a different macOS user in the operator's
login session — it's unsupported and fragile.** But the goal is still achievable,
because VS Code and Cursor are **not monoliths**: they split into a UI/renderer and
a server + extension host. The supported design is the **client/server split** —
run the *UI as the operator*, and run the *server + extension host + integrated
terminal + agent as the dedicated `agent` user*, connected over **loopback
Remote-SSH**. That puts the privilege boundary exactly where we want it: **UI =
operator, code execution = agent** — and it preserves single-window, side-by-side
ergonomics.

## Why raw "GUI as another user" fails on macOS

macOS gates GUI access on three things owned by the **console (logged-in) user**,
none of which a second user has in the operator's session:

- **WindowServer** only accepts drawing connections from the console user's
  graphical session (its audit session / ASID).
- **The launchd `Aqua` (GUI) domain** (`gui/<uid>`) exists only when that user logs
  in *at the login window*. An SSH/background login gets `user/<uid>` with **no GUI
  domain**.
- **TCC / Keychain / sandbox** are per-user.

So the naive commands break:
- `sudo -u agent open -a Cursor` → talks to the operator's Launch Services but as
  `agent`, who has no Aqua session → `LSOpenURLs…`/registration failure, no render.
- `sudo -u agent /Applications/Cursor.app/Contents/MacOS/Cursor` → the Electron app
  immediately tries to connect to WindowServer as `agent` → *Failed to connect to
  WindowServer* and it exits.
- `launchctl asuser <uid> …` enters another user's bootstrap namespace **without
  changing UID**. Combining it with `sudo -u agent` either (a) enters the
  operator's session then drops to `agent` — a window may appear but keychain/TCC/
  session identity are now inconsistent (the "sort-of-works, everything-broken"
  zone), or (b) targets the agent's non-existent GUI domain and fails.

**The controls that make it "sort of" render are the same ones that break the
isolation you wanted.** Treat GUI-as-another-user as unsupported and unsuitable as
a security boundary.
(`launchctl` `asuser` / `gui` vs `user` domains: https://ss64.com/mac/launchctl.html)

## The architectural answer: the client/server split

VS Code (and Cursor, a VS Code fork) separates:

- **Renderer / UI** — the window and editor surface; "UI extensions" (themes,
  snippets). Runs where the human is.
- **Extension host + VS Code Server** — runs extension code, language servers,
  tasks, the **integrated terminal**, file operations, and **the agent/AI logic
  that reads files and executes commands**.

In remote scenarios (Remote-SSH, Tunnels, Dev Containers) the split is a **real
trust boundary**: the UI stays local, while the **server + extension host run on
the target as the SSH-authenticated user**
(https://code.visualstudio.com/docs/remote/ssh). Extension placement is governed
by the `extensionKind` manifest field (`"ui"` local vs `"workspace"` remote —
"most extensions fall into this category")
(https://code.visualstudio.com/api/advanced-topics/extension-host).

**Crucially, an IDE coding agent's file reads, terminal commands, and tool calls
run in the extension host / server — i.e. as the user the server runs as — not in
the operator's renderer.** That is exactly the boundary we want.

### The mechanism: loopback Remote-SSH to `agent@localhost`

Works on a single Mac, no session hacks:

1. Create the minimal-privilege `agent` user (as in [`05`](05-agent-as-own-unix-user.md));
   enable Remote Login (SSH) so key-based `ssh agent@localhost` works.
2. Operator opens the IDE GUI **as themselves** — their own normal Aqua session.
3. **Remote-SSH → `agent@localhost`.** The IDE bootstraps its server into
   `~agent/.vscode-server` / `~/.cursor-server`; the extension host, integrated
   terminal, and agent now run **as `agent`**.
4. Keep the **workspace under `agent`'s ownership** (the shared-group `2770`
   workspace from [`05`](05-agent-as-own-unix-user.md) is ideal). The agent runs
   with `agent`'s permissions and cannot read the operator's Keychain, browser
   profile, SSH keys, or `~/.jentic` key material.

Single window, side-by-side, boundary between UI (operator) and execution (agent).

### CLI / tunnel variants
- **Remote-SSH** — most direct; no external service. (…/docs/remote/ssh)
- **`code tunnel` / `code serve-web`** — run the server as `agent`, attach a
  desktop or browser client as the operator
  (https://code.visualstudio.com/docs/remote/vscode-server). Note the server is
  licensed **single-user-per-instance** — fine for one-operator/one-agent, not for
  a shared multi-tenant service.

### Cursor specifics
- Cursor ships a `cursor` launcher CLI (VS Code `code`-equivalent) and a separate,
  documented **Cursor Agent CLI** (`cursor-agent`) — a **headless terminal coding
  agent** (`cursor-agent -p "…"` non-interactive, `--sandbox`, resume/continue).
  That runs cleanly under `su - agent` with **no GUI problem at all** — see below.
- Cursor's Remote-SSH is its **own fork** ("Anysphere Remote - SSH"), because
  Microsoft's Remote-SSH is proprietary to official VS Code builds. Same
  architecture, but validate the current version on your macOS release — it has
  historically lagged Microsoft's and can be finicky on some hosts.

## Fast User Switching (native, but no side-by-side)
Logging the `agent` user in at the login window gives it a **genuine, fully
supported second Aqua session** (own WindowServer/Keychain/TCC); launch Cursor
there normally. Strongest native isolation, zero hacks — **but you lose
side-by-side** (only one session is on the display; switching is a full-screen
swap) and it costs extra RAM/GPU. Good fallback for occasional supervised runs;
poor daily driver.

## Linux (much easier)
- **X11:** `xhost +si:localuser:agent` (or an `xauth` cookie), then
  `sudo -u agent env DISPLAY=:0 cursor` renders **into the operator's desktop,
  side-by-side, as `agent`** — the thing macOS refuses to do. (`xhost` is coarse;
  cookie-based `xauth` is tighter.)
- **Wayland:** stricter (no per-app `xhost`), but the **client/server split works
  identically** — and remote/container dev is the norm on Linux anyway. Using
  loopback Remote-SSH gives you **one architecture on both OSes.**

## Honest caveats
- **Unix perms are the actual boundary.** The split only isolates if `~operator`
  (or its sensitive subdirs) isn't group/other-readable, the `agent` user isn't an
  admin / in `sudoers`, and the repo genuinely lives under `agent`'s ownership. A
  separate UID alone doesn't protect a world-readable `~/.ssh`. (Same caveat as
  [`05`](05-agent-as-own-unix-user.md).)
- **Per-user Keychain/TCC/git creds** — the agent gets its own (good: can't reach
  the operator's), but must be provisioned with its own git tokens etc.
- **Cursor's remote fork** is less battle-tested than Microsoft's — validate first.

## Recommendation (ranked)

1. **If the IDE UI isn't strictly required — don't use the GUI at all.** Run the
   agent headless under `su - agent`: **`cursor-agent -p …`** or **Claude Code**.
   Simplest and strongest; no tunnel, no session issues. This is the same path as
   [`05`](05-agent-as-own-unix-user.md) and should be the default recommendation.
2. **If a human needs the IDE UI attached to the agent's execution env:**
   operator runs the GUI as themselves + **loopback Remote-SSH into
   `agent@localhost`** so server/extension-host/terminal/agent execute as `agent`.
   Keep the repo under `~agent`; harden `~operator` perms.
3. **Occasional/manual:** Fast User Switching for a native isolated GUI session,
   accepting loss of side-by-side.
4. **Avoid:** `sudo -u agent open`, direct-binary launch as another user, and
   `launchctl asuser` tricks — unsupported, fragile, and they break the very
   Keychain/TCC isolation you're after.

## How this feeds the mitigations doc
This confirms structural fix **1a/1b** in [`03`](03-mitigations.md) extends to
IDE-driven agents **without** us owning the client: the client/server split is a
built-in IDE feature, so the only operator setup is "Remote-SSH to `agent@localhost`"
on top of the [`05`](05-agent-as-own-unix-user.md) account recipe. The strongest
and simplest story remains **headless agent as the `agent` user** — the IDE-remote
path is the accommodation for users who insist on the GUI.
