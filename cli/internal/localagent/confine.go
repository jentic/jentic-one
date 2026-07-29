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
func ConfineLaunchCmd(ctx context.Context, agentUser, binary, dir, agentHome string, grantedDirs []string) *exec.Cmd {
	cd := `cd "$HOME"`
	if dir != "" {
		cd = "cd " + shellQuote(dir)
	}
	inner := cd + " && exec " + confineExec(binary, dir, agentHome, grantedDirs)
	return agentCmdContext(ctx, agentUser, inner)
}

// confineExec builds the `sandbox-exec …`/`bwrap …` prefix plus the agent binary,
// already shell-quoted for embedding in the login-shell snippet.
func confineExec(binary, dir, agentHome string, grantedDirs []string) string {
	switch runtime.GOOS {
	case "darwin":
		profile := SandboxProfile(agentHome, grantedDirs)
		return "sandbox-exec -p " + shellQuote(profile) + " " + shellQuote(binary)
	case "linux":
		return strings.Join(bwrapArgs(binary, dir, agentHome, grantedDirs), " ")
	default:
		// Unsupported — no confinement to add. Callers gate on
		// ConfinementAvailable, so reaching here means the launch was not guarded.
		return shellQuote(binary)
	}
}

// ── macOS: Seatbelt / SBPL ────────────────────────────────────────────────────

// humanHomeRoots are the parent directories of every human home. The whole
// subtree of each is denied by default in the sandbox: granting the agent access
// to one user's directory must never expose any other user's files, and the
// operator's own home (a child of one of these) is covered by the same blanket
// deny rather than a bespoke rule.
var humanHomeRoots = []string{"/Users", "/home"}

// SandboxProfile builds the SBPL profile for a confined macOS session. The model
// is: keep the base permissive (so the agent's own runtime deps — its binary,
// dylibs, tmp, /dev, the loopback socket, the shared toolchain — are untouched and
// can't fail to start on an OS/agent update), then deny ALL human-home roots
// (/Users, /home) and re-open only what this session legitimately needs:
//
//   - the agent's own home (it lives under /Users/Shared, inside the deny);
//   - each granted directory;
//   - metadata traversal on the ancestor chain of each re-allowed path (so path
//     resolution into it works).
//
// Finally the executable/CLI routes on the agent's PATH are marked read-only: a
// compromised agent must not be able to rewrite the very binaries `jentic run`
// executes and thereby escape its sandbox on the next launch. This is a
// non-negotiable default boundary.
//
// Seatbelt evaluates rules top-to-bottom with LAST-match-wins, so re-allows are
// emitted after the home denies, and the read-only exec-route denies are emitted
// LAST so they hold even if a re-allow overlapped them. Ancestor metadata uses
// `(literal …)` — it matches the directory node itself, not its children, so it
// grants stat/traverse on the path component without re-exposing that ancestor's
// other entries (the SBPL analog of the execute-only ACL traverse-walk).
func SandboxProfile(agentHome string, grantedDirs []string) string {
	var b strings.Builder
	b.WriteString("(version 1)\n")
	b.WriteString("(allow default)\n")

	b.WriteString("; deny every human home; re-open only the agent home and granted paths\n")
	for _, root := range humanHomeRoots {
		fmt.Fprintf(&b, "(deny file* (subpath %s))\n", sbplPath(root))
	}

	// Everything the session may reach inside a denied root: the agent's own home
	// first (always), then each granted directory.
	var reopen []string
	if agentHome != "" {
		reopen = append(reopen, filepath.Clean(agentHome))
	}
	for _, g := range grantedDirs {
		reopen = append(reopen, filepath.Clean(g))
	}

	seenMeta := map[string]bool{}
	var metaLines, allowLines []string
	for _, p := range reopen {
		root := deniedRootOf(p)
		if root == "" {
			continue // outside every denied root: already allowed by (allow default)
		}
		// Metadata traversal on each ancestor from the denied root down to p's
		// parent — the exact path the kernel resolves to reach p.
		for _, anc := range AncestorChain(root, p) {
			if seenMeta[anc] {
				continue
			}
			seenMeta[anc] = true
			metaLines = append(metaLines,
				fmt.Sprintf("(allow file-read-metadata (literal %s))", sbplPath(anc)))
		}
		// Full access to the re-opened subtree (wins over the home deny by last-match).
		allowLines = append(allowLines,
			fmt.Sprintf("(allow file* (subpath %s))", sbplPath(p)))
	}
	for _, l := range metaLines {
		b.WriteString(l)
		b.WriteByte('\n')
	}
	for _, l := range allowLines {
		b.WriteString(l)
		b.WriteByte('\n')
	}

	// Read-only executable routes, emitted LAST so the write-deny is authoritative.
	b.WriteString("; the binaries jentic run executes stay read-only (no sandbox self-escape)\n")
	for _, d := range execRouteDirs() {
		fmt.Fprintf(&b, "(deny file-write* (subpath %s))\n", sbplPath(d))
	}
	return b.String()
}

// deniedRootOf returns the human-home root (/Users or /home) that p sits under,
// or "" if p is outside every denied root.
func deniedRootOf(p string) string {
	for _, root := range humanHomeRoots {
		if IsUnderHome(root, p) {
			return root
		}
	}
	return ""
}

// systemBinDirs are the OS binary directories always present on the login PATH.
// They are marked read-only alongside the sanctioned tool dirs so the agent can
// run the tools it needs but never overwrite any executable on its PATH.
var systemBinDirs = []string{"/usr/bin", "/bin", "/usr/sbin", "/sbin", "/usr/local/bin"}

// execRouteDirs returns the executable directories on the agent's PATH that the
// sandbox marks read-only: the sanctioned shared tool dirs plus the system bin
// dirs, de-duplicated and filtered to those that exist. Making these
// write-denied is a non-negotiable boundary — it stops a compromised agent from
// rewriting the binaries `jentic run` executes to shed its own sandbox next run.
func execRouteDirs() []string {
	seen := map[string]bool{}
	var out []string
	for _, d := range append(append([]string{}, candidateSharedBinDirs...), systemBinDirs...) {
		d = filepath.Clean(d)
		if seen[d] {
			continue
		}
		seen[d] = true
		if info, err := os.Stat(d); err != nil || !info.IsDir() {
			continue
		}
		out = append(out, d)
	}
	return out
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
// inside the namespace — bwrap grants no privilege), then hide EVERY human-home
// root (/Users, /home) behind a tmpfs and re-bind only the agent's own home and
// the granted dirs over the top. Grants outside those roots stay visible through
// the root bind. Finally the executable routes are re-mounted read-only so the
// agent can't rewrite the binaries it runs. --die-with-parent tears the namespace
// (and its mounts) down when the session exits.
//
// Order matters: bwrap applies mounts left-to-right, so the tmpfs masks come
// first, the re-binds land on top, and the read-only exec-route binds come LAST so
// they hold even if a grant re-bound an overlapping path.
func bwrapArgs(binary, dir, agentHome string, grantedDirs []string) []string {
	args := []string{"bwrap", "--die-with-parent", "--dev-bind", "/", "/"}
	for _, root := range humanHomeRoots {
		if info, err := os.Stat(root); err == nil && info.IsDir() {
			args = append(args, "--tmpfs", root)
		}
	}
	var reopen []string
	if agentHome != "" {
		reopen = append(reopen, filepath.Clean(agentHome))
	}
	for _, g := range grantedDirs {
		reopen = append(reopen, filepath.Clean(g))
	}
	for _, p := range reopen {
		if deniedRootOf(p) != "" {
			args = append(args, "--bind", p, p)
		}
	}
	for _, d := range execRouteDirs() {
		args = append(args, "--ro-bind", d, d)
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
