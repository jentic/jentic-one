package localagent

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
)

// This file implements the per-session process-confinement layer that closes the
// sibling-traversal residual: granting the agent access to ~/a must not let it
// reach a world-readable sibling ~/b. The agent account + allow-only ACLs open the
// one chosen path (a DAC grant is still required — the sandbox is intersection-only
// and can never widen access its ownership denies); this layer then TRIMS the
// process's view so the coarse grant can't be abused.
//
// The model is a TARGETED HOME-DENY, not a strict `(deny default)` allow-list: the
// profile keeps the base permissive and only denies the operator home, then
// re-opens the granted subpaths inside it. This is robust (the agent's own runtime
// dependencies outside ~ are untouched, so it can't fail to start on an OS/agent
// update) and scoped to the one thing the sandbox uniquely does — the per-entry
// distinction inside ~ that DAC cannot express. See
// docs/security/local-agent/sandbox-exec-plan.md for the full rationale.
//
// Confinement is REQUIRED, not best-effort: when the platform mechanism is
// unavailable the caller must error closed (refuse the launch) rather than fall
// back to an unconfined session, which would silently reinstate the leak now that
// the operator home is no longer force-locked to 700.

// ConfinementAvailable reports whether this machine can run a locked-down agent
// session, and if not, a short human-readable reason. macOS uses Seatbelt via
// `sandbox-exec`; Linux uses bubblewrap (`bwrap`) over an unprivileged user+mount
// namespace. Any other platform is unsupported.
func ConfinementAvailable() (bool, string) {
	switch runtime.GOOS {
	case "darwin":
		if _, err := exec.LookPath("sandbox-exec"); err != nil {
			return false, "sandbox-exec is not available on this macOS"
		}
		return true, ""
	case "linux":
		if _, err := exec.LookPath("bwrap"); err != nil {
			return false, "bubblewrap (bwrap) is not installed"
		}
		if !unprivilegedUserNSEnabled() {
			return false, "unprivileged user namespaces are disabled on this kernel"
		}
		return true, ""
	default:
		return false, "process confinement is not supported on " + runtime.GOOS
	}
}

// ConfineLaunchCmd builds the interactive launch: become the agent user in a login
// shell (`sudo -u <user> -H bash -lc`, fresh env, HOME at the agent's home), cd to
// dir (or the agent's home), and exec the agent binary wrapped in the platform
// confinement mechanism. operatorHome + grantedDirs drive the deny/re-allow set;
// the caller wires os.Stdin/out/err. Callers MUST have checked ConfinementAvailable
// first — on an unsupported platform confineExec adds no wrapper, so reaching here
// unconfined is a programming error, not a security posture.
func ConfineLaunchCmd(ctx context.Context, agentUser, binary, dir, operatorHome string, grantedDirs []string) *exec.Cmd {
	cd := `cd "$HOME"`
	if dir != "" {
		cd = "cd " + shellQuote(dir)
	}
	inner := cd + " && exec " + confineExec(binary, dir, operatorHome, grantedDirs)
	return agentCmdContext(ctx, agentUser, inner)
}

// confineExec builds the `sandbox-exec …`/`bwrap …` prefix plus the agent binary,
// already shell-quoted for embedding in the login-shell snippet.
func confineExec(binary, dir, operatorHome string, grantedDirs []string) string {
	switch runtime.GOOS {
	case "darwin":
		profile := SandboxProfile(operatorHome, grantedDirs)
		return "sandbox-exec -p " + shellQuote(profile) + " " + shellQuote(binary)
	case "linux":
		return strings.Join(bwrapArgs(binary, dir, operatorHome, grantedDirs), " ")
	default:
		// Unsupported — no confinement to add. Callers gate on
		// ConfinementAvailable, so reaching here means the launch was not guarded.
		return shellQuote(binary)
	}
}

// ── macOS: Seatbelt / SBPL ────────────────────────────────────────────────────

// SandboxProfile builds the SBPL profile for a confined macOS session. It is the
// targeted-home-deny model: keep the base permissive, deny the operator home, then
// re-open only what a grant under ~ needs — metadata traversal on the ancestor
// chain (so path resolution into the grant works) plus full access to the granted
// subtree. Grants OUTSIDE the home need no rule: `(allow default)` already permits
// them, so we never enumerate the agent binary, its dylibs, tmp, /dev, the loopback
// socket, or the shared toolchain (the brittleness a `(deny default)` profile would
// have incurred).
//
// Seatbelt evaluates rules top-to-bottom with LAST-match-wins, so every re-allow is
// emitted after the home deny. Ancestor metadata uses `(literal …)` — it matches
// the directory node itself, not its children, so it grants stat/traverse on the
// path component without re-exposing that ancestor's other entries (the SBPL analog
// of the execute-only ACL traverse-walk).
func SandboxProfile(operatorHome string, grantedDirs []string) string {
	var b strings.Builder
	b.WriteString("(version 1)\n")
	b.WriteString("(allow default)\n")

	if operatorHome == "" {
		// No home to protect — nothing to trim. The permissive base stands.
		// (Guard before Clean, which would turn "" into ".".)
		return b.String()
	}
	home := filepath.Clean(operatorHome)

	b.WriteString("; close the operator home, then re-open only the granted paths\n")
	fmt.Fprintf(&b, "(deny file* (subpath %s))\n", sbplPath(home))

	seenMeta := map[string]bool{}
	var metaLines, allowLines []string
	for _, g := range grantedDirs {
		g = filepath.Clean(g)
		if !IsUnderHome(home, g) {
			continue // outside the home: already allowed by (allow default)
		}
		// Metadata traversal on each ancestor from the home down to the grant's
		// parent — the exact path the kernel resolves to reach the grant.
		for _, anc := range AncestorChain(home, g) {
			if seenMeta[anc] {
				continue
			}
			seenMeta[anc] = true
			metaLines = append(metaLines,
				fmt.Sprintf("(allow file-read-metadata (literal %s))", sbplPath(anc)))
		}
		// Full access to the granted subtree (wins over the home deny by last-match).
		allowLines = append(allowLines,
			fmt.Sprintf("(allow file* (subpath %s))", sbplPath(g)))
	}
	for _, l := range metaLines {
		b.WriteString(l)
		b.WriteByte('\n')
	}
	for _, l := range allowLines {
		b.WriteString(l)
		b.WriteByte('\n')
	}
	return b.String()
}

// sbplPath renders a filesystem path as an SBPL double-quoted string literal,
// escaping the two characters that are special inside one (backslash and quote).
func sbplPath(p string) string {
	p = strings.ReplaceAll(p, `\`, `\\`)
	p = strings.ReplaceAll(p, `"`, `\"`)
	return `"` + p + `"`
}

// ── Linux: bubblewrap ─────────────────────────────────────────────────────────

// bwrapArgs builds the `bwrap … <binary>` argv (each token shell-quoted) that
// mirrors the macOS profile: bind the whole host read/write (DAC still applies
// inside the namespace — bwrap grants no privilege), then hide the operator home
// behind a tmpfs and re-bind only the granted dirs over it. Grants outside the home
// stay visible through the root bind. --die-with-parent tears the namespace (and
// its mounts) down when the session exits.
func bwrapArgs(binary, dir, operatorHome string, grantedDirs []string) []string {
	args := []string{"bwrap", "--die-with-parent", "--dev-bind", "/", "/"}
	if operatorHome != "" {
		home := filepath.Clean(operatorHome)
		args = append(args, "--tmpfs", home)
		for _, g := range grantedDirs {
			g = filepath.Clean(g)
			if IsUnderHome(home, g) {
				args = append(args, "--bind", g, g)
			}
		}
	}
	if dir != "" {
		args = append(args, "--chdir", dir)
	}
	args = append(args, binary)
	quoted := make([]string, len(args))
	for i, a := range args {
		quoted[i] = shellQuote(a)
	}
	return quoted
}

// unprivilegedUserNSEnabled reports whether the kernel permits unprivileged user
// namespaces, which bubblewrap needs to build its mount namespace without root.
// The sysctl is Debian/Ubuntu-specific; when the knob is absent (mainline kernels,
// RHEL) unprivileged userns is generally on, so absence is treated as enabled.
func unprivilegedUserNSEnabled() bool {
	data, err := os.ReadFile("/proc/sys/kernel/unprivileged_userns_clone")
	if err != nil {
		return true // knob absent → not gated here
	}
	return strings.TrimSpace(string(data)) != "0"
}
