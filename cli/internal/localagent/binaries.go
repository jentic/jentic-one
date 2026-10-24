package localagent

import (
	"context"
	"os"
	"os/exec"
	"runtime"
	"strings"
)

// EnsureLocalBinOnPathCmd makes ~/.local/bin resolvable for the agent user by
// appending an idempotent export line to the agent's login profiles. It runs as
// the agent user so the files are created owned by the agent, and it covers the
// bash login files the launch reads (.profile / .bash_profile) plus .zprofile
// for any interactive zsh session. Re-running is a no-op (guarded by a marker).
func EnsureLocalBinOnPathCmd(agentUser string) *exec.Cmd {
	const snippet = `line='export PATH="$HOME/.local/bin:$PATH"'
marker='# added by jentic run (ensure ~/.local/bin on PATH)'
for f in "$HOME/.profile" "$HOME/.bash_profile" "$HOME/.zprofile"; do
  if ! grep -qF "$marker" "$f" 2>/dev/null; then
    printf '\n%s\n%s\n' "$marker" "$line" >> "$f"
  fi
done`
	return agentCmd(agentUser, snippet)
}

// candidateSharedBinDirs are the well-known, world-traversable directories where
// operators install CLI tools OUTSIDE any human's home. Sharing these with the
// agent is safe precisely because they are readable+traversable by every user —
// unlike home-local bin dirs (~/.local/bin, ~/.cargo/bin, ~/go/bin), which sit
// under the operator's 700 home and are therefore unreachable by the agent no
// matter how they are referenced (a symlink resolves with the AGENT's
// credentials and dangles with EACCES at the home boundary). Those home-local
// dirs are deliberately NOT shared; see docs/security/same-host/
// local-agent-isolation.md ("Sharing the operator's installed CLI tools").
//
// /usr/bin, /bin, /usr/sbin, /sbin, and /usr/local/bin are already on the
// default login PATH (via /etc/paths), so they are omitted here — the only gap
// on a typical macOS box is Homebrew's /opt/homebrew/bin, which Homebrew adds to
// the operator's shell profile but a fresh agent login shell does not inherit.
var candidateSharedBinDirs = func() []string {
	if runtime.GOOS == "darwin" {
		return []string{"/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin"}
	}
	return []string{"/usr/local/bin", "/opt/homebrew/bin", "/snap/bin"}
}()

// SharedBinPaths returns the world-traversable operator binary directories that
// exist on this machine and are safe to add to the agent's PATH. It filters out
// anything under the operator's (700) home — such a dir would be unreachable by
// the agent, so adding it to PATH would be a dead entry rather than a share.
func SharedBinPaths(operatorHome string) []string {
	var out []string
	for _, d := range candidateSharedBinDirs {
		info, err := os.Stat(d)
		if err != nil || !info.IsDir() {
			continue
		}
		// Never add a path under the operator's home: it is shadowed by the 700
		// lock and the agent could not traverse into it.
		if operatorHome != "" && IsUnderHome(operatorHome, d) {
			continue
		}
		out = append(out, d)
	}
	return out
}

// EnsureSharedBinsOnPathCmd makes the operator's world-readable CLI tool dirs
// (e.g. Homebrew's /opt/homebrew/bin) resolvable for the agent by appending an
// idempotent export to the agent's login profiles — the same mechanism as
// EnsureLocalBinOnPathCmd. The dirs are appended AFTER $PATH so an agent-owned
// tool (in ~/.local/bin, which EnsureLocalBinOnPathCmd prepends) always wins
// over the operator's copy. Returns nil when there is nothing safe to add, so
// callers can skip running it.
func EnsureSharedBinsOnPathCmd(agentUser string, dirs []string) *exec.Cmd {
	if len(dirs) == 0 {
		return nil
	}
	// The dirs come from a fixed candidate allowlist of absolute system paths (no
	// shell metacharacters), so they interpolate directly into the export line.
	snippet := `line='export PATH="$PATH:` + strings.Join(dirs, ":") + `"'
marker='# added by jentic run (share operator CLI tool dirs)'
for f in "$HOME/.profile" "$HOME/.bash_profile" "$HOME/.zprofile"; do
  if ! grep -qF "$marker" "$f" 2>/dev/null; then
    printf '\n%s\n%s\n' "$marker" "$line" >> "$f"
  fi
done`
	return agentCmd(agentUser, snippet)
}

// OperatorBinaryPath resolves the operator's own copy of binary via `command
// -v`, returning "" if the operator doesn't have it either. Used to offer the
// copy route.
func OperatorBinaryPath(ctx context.Context, binary string) string {
	out, err := exec.CommandContext(ctx, "bash", "-lc", "command -v "+shellQuote(binary)).Output() //nolint:gosec // binary is a known descriptor value.
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(out))
}

// CopyBinaryCmd copies the operator's binary at src into the agent user's
// ~/.local/bin. Only the READ of src is privileged (it usually sits under the
// operator's 0700 home): root opens it as the stdin of a shell running AS THE AGENT,
// which creates the destination and renames a fresh temp file over it. Every
// path the write touches is agent-owned, so it runs with the agent's own
// permissions — an existing symlink (or a symlinked ~/.local / ~/.local/bin)
// can't redirect a privileged write, and the final rename replaces a link
// rather than writing through it. The result is owned by the agent, so no
// chown is needed. agentHome is resolved by the caller in Go (LookupHomeDir)
// — never via shell expansion of the account name — so the name can't reach a
// shell as anything but a quoted literal.
//
// src is attached with a shell redirection rather than piped from cat: if root
// cannot open it the agent side never runs and the command fails, instead of
// the pipeline's status (the writer's) reporting success for an empty file.
func CopyBinaryCmd(agentUser, agentHome, src, binary string) *exec.Cmd {
	script := copyBinaryScript(asAgentPrefix(agentUser), agentHome, src, binary)
	cmd := exec.Command("sudo", "sh", "-c", script) //nolint:gosec // agentUser/src/binary/agentHome are config/descriptor-derived and Go-resolved, shell-quoted.
	// The agent-side shell inherits the working directory; the operator's cwd
	// is typically unreadable to the agent (see agentCmd), so pin it to "/".
	cmd.Dir = "/"
	return cmd
}

// copyBinaryScript is CopyBinaryCmd's root-side script; asAgent is the
// command prefix that switches to the agent (asAgentPrefix), which tests
// leave empty to run the real script unprivileged.
func copyBinaryScript(asAgent, agentHome, src, binary string) string {
	return fixedPATHPrefix + asAgent + shellQuote(agentLaunchShell) + " --noprofile --norc -c " +
		shellQuote(agentInstallFromStdinScript(AgentLocalBinDir(agentHome), binary)) + " < " + shellQuote(src)
}

// asAgentPrefix is the root-side command prefix that runs the rest of the
// command line as agentUser (root needs no password for it).
func asAgentPrefix(agentUser string) string {
	return "sudo -u " + shellQuote(agentUser) + " -H "
}

// fixedPATHPrefix pins PATH to the system dirs for both halves of the
// privileged copy pipelines (CopyBinaryCmd / CopyConfigCmd), so the helpers
// they call (sudo, mkdir, mktemp, tar, …) never resolve through whatever PATH
// the invoking environment carried (sudo keeps the caller's PATH when sudoers
// sets no secure_path, as on macOS).
const fixedPATHPrefix = "PATH=" + agentSystemPATH + "; export PATH; "

// agentInstallFromStdinScript is the agent-side half of CopyBinaryCmd: create
// dir, write stdin to a new temp file in it, mark it executable, and rename it
// onto dir/binary. mktemp creates the temp file exclusively (never an existing
// path). A symlink already at the destination is removed first — mv(1)
// resolves a destination link to a DIRECTORY and would move the file into it —
// and a real directory there is an error rather than a silent move-into.
func agentInstallFromStdinScript(dir, binary string) string {
	d := shellQuote(dir)
	dest := shellQuote(dir + "/" + binary)
	return fixedPATHPrefix + `set -e; umask 022; mkdir -p ` + d + `; ` +
		`if [ -L ` + dest + ` ]; then rm -f ` + dest + `; ` +
		`elif [ -d ` + dest + ` ]; then echo ` + dest + `": is a directory" >&2; exit 1; fi; ` +
		`t="$(mktemp ` + shellQuote(dir+"/."+binary+".XXXXXX") + `)"; ` +
		`trap 'rm -f "$t"' EXIT; ` +
		`cat > "$t"; chmod 0755 "$t"; mv -f "$t" ` + dest + `; trap - EXIT`
}

// InstallBinaryCmd runs an agent's documented fresh-install command as the
// agent user (no startup files, system PATH), so the toolchain lands in the
// agent's home.
func InstallBinaryCmd(agentUser, installCmd string) *exec.Cmd {
	return agentCmd(agentUser, installCmd)
}
