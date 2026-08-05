package localagent

import (
	"context"
	"errors"
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

// Prereq is one prerequisite the agent-as-Unix-user model needs on this machine,
// with whether it is satisfied and — when it is not — a human-readable reason and
// an actionable hint (how to install/enable it). It is the single unit the whole
// CLI reasons about when deciding "can this machine run an isolated agent".
type Prereq struct {
	// Name is a short label for the missing capability ("bubblewrap (bwrap)").
	Name string
	// OK reports whether the prerequisite is satisfied on this machine.
	OK bool
	// Reason explains, when !OK, what is missing (shown to the operator).
	Reason string
	// Hint is the actionable fix, when !OK — an install command or a pointer to
	// the setup docs. Empty when OK.
	Hint string
}

// AgentUserPrereqs reports every prerequisite the agent-as-Unix-user lifecycle
// needs on THIS machine — account creation, the ACL grants/revokes, and the
// per-session confinement launch all depend on them, so the CLI checks them ONCE,
// up front, before it starts creating a Unix user it could never launch. The list
// is returned in check order; callers use MissingPrereqs to filter to the
// unsatisfied ones.
//
//	macOS  — Seatbelt (`sandbox-exec`) for confinement; ACLs are built into the
//	         filesystem tooling, so nothing extra is required.
//	Linux  — bubblewrap (`bwrap`) + unprivileged user namespaces for confinement,
//	         and the `acl` package (`setfacl`/`getfacl`) for the directory grants.
//
// On an unsupported platform a single unsatisfied Prereq is returned.
func AgentUserPrereqs() []Prereq {
	switch runtime.GOOS {
	case "darwin":
		return []Prereq{lookPathPrereq(
			"sandbox-exec", "sandbox-exec",
			"sandbox-exec is not available on this macOS",
			"sandbox-exec ships with macOS; see docs/security/local-agent/local-agent-isolation.md",
		)}
	case "linux":
		return []Prereq{
			lookPathPrereq(
				"bubblewrap (bwrap)", "bwrap",
				"bubblewrap (bwrap) is not installed",
				linuxInstallHint,
			),
			usernsPrereq(),
			aclPrereq(),
		}
	default:
		return []Prereq{{
			Name:   "process confinement",
			Reason: "process confinement is not supported on " + runtime.GOOS,
		}}
	}
}

// linuxInstallHint is the distro-specific one-liner for the Linux prerequisites,
// resolved from whichever package manager is on PATH. bubblewrap and the acl
// package are named together because both are required and both are commonly
// absent on a minimal install; installing them in one command is the fix.
var linuxInstallHint = resolveLinuxInstallHint()

// MissingPrereqs returns only the unsatisfied prerequisites from AgentUserPrereqs
// — the set an operator must resolve before the agent-as-Unix-user model can run.
// An empty slice means this machine is fully ready.
func MissingPrereqs() []Prereq {
	var missing []Prereq
	for _, p := range AgentUserPrereqs() {
		if !p.OK {
			missing = append(missing, p)
		}
	}
	return missing
}

// ConfinementAvailable reports whether this machine can run a locked-down agent
// session, and if not, a short human-readable reason. It is the launch-time gate
// (see run.go): confinement is REQUIRED, so an unsatisfied prerequisite here means
// the launch is refused. It shares AgentUserPrereqs with the setup-time gate so
// the two can never disagree about what this machine can do.
func ConfinementAvailable() (bool, string) {
	for _, p := range MissingPrereqs() {
		return false, p.Reason
	}
	return true, ""
}

// lookPathPrereq builds a Prereq satisfied iff bin is resolvable on PATH.
func lookPathPrereq(name, bin, reason, hint string) Prereq {
	if _, err := exec.LookPath(bin); err != nil {
		return Prereq{Name: name, Reason: reason, Hint: hint}
	}
	return Prereq{Name: name, OK: true}
}

// usernsPrereq reports whether the kernel permits unprivileged user namespaces,
// which bubblewrap needs to build its mount namespace without root.
func usernsPrereq() Prereq {
	if !unprivilegedUserNSEnabled() {
		return Prereq{
			Name:   "unprivileged user namespaces",
			Reason: "unprivileged user namespaces are disabled on this kernel",
			Hint:   "enable them: sudo sysctl -w kernel.unprivileged_userns_clone=1 (persist in /etc/sysctl.d/)",
		}
	}
	return Prereq{Name: "unprivileged user namespaces", OK: true}
}

// aclPrereq reports whether the `acl` tooling (setfacl AND getfacl) is present —
// the directory grants/revokes and the reset teardown all shell out to it, so a
// missing acl package silently breaks every grant. Both binaries ship in the same
// package, so one hint covers both.
func aclPrereq() Prereq {
	for _, bin := range []string{"setfacl", "getfacl"} {
		if _, err := exec.LookPath(bin); err != nil {
			return Prereq{
				Name:   "acl (setfacl/getfacl)",
				Reason: bin + " is not installed (the acl package provides directory grants)",
				Hint:   linuxInstallHint,
			}
		}
	}
	return Prereq{Name: "acl (setfacl/getfacl)", OK: true}
}

// resolveLinuxInstallHint returns the install command for bubblewrap + acl using
// whichever package manager is on PATH, falling back to a generic instruction
// when none is recognised. Package/binary names: Debian/Ubuntu use `bubblewrap`
// + `acl`; Fedora/RHEL `dnf` the same; Arch `pacman` uses `bubblewrap` + `acl`;
// openSUSE `zypper` likewise. The two are named together because both are needed.
func resolveLinuxInstallHint() string {
	switch {
	case hasBinary("apt"):
		return "install them: sudo apt install bubblewrap acl"
	case hasBinary("apt-get"):
		return "install them: sudo apt-get install bubblewrap acl"
	case hasBinary("dnf"):
		return "install them: sudo dnf install bubblewrap acl"
	case hasBinary("yum"):
		return "install them: sudo yum install bubblewrap acl"
	case hasBinary("pacman"):
		return "install them: sudo pacman -S bubblewrap acl"
	case hasBinary("zypper"):
		return "install them: sudo zypper install bubblewrap acl"
	default:
		return "install the bubblewrap and acl packages with your distro's package manager"
	}
}

// hasBinary reports whether bin resolves on PATH.
func hasBinary(bin string) bool {
	_, err := exec.LookPath(bin)
	return err == nil
}

// sandboxExecPath and bwrapPath are the ABSOLUTE paths to the confinement
// wrappers, resolved ONCE in the operator's own (trusted) process at start-up.
// This is a security boundary, not a convenience: the wrapper command is embedded
// in a snippet that runs as the agent user, so if it were an unqualified name
// (`sandbox-exec`, `bwrap`) the agent's own login PATH — which the agent controls
// via its rc files — could shadow it with a no-op and shed confinement entirely.
// An absolute path can't be hijacked that way. Resolution prefers the operator
// PATH (via LookPath, so a non-standard install is honoured) but only when it
// yields an absolute path, and otherwise falls back to the canonical system
// location. Both wrappers live in system bin dirs that the profile marks
// read-only, so the agent can't rewrite them either.
var (
	sandboxExecPath = resolveWrapperPath("sandbox-exec", "/usr/bin/sandbox-exec")
	bwrapPath       = resolveWrapperPath("bwrap", "/usr/bin/bwrap")
)

// resolveWrapperPath returns the absolute path to a confinement wrapper: the
// operator-PATH resolution when it is absolute, else the canonical fallback. It is
// evaluated in the operator process (never the agent's), so the PATH consulted is
// the trusted operator PATH.
func resolveWrapperPath(bin, fallback string) string {
	if p, err := exec.LookPath(bin); err == nil && filepath.IsAbs(p) {
		return p
	}
	return fallback
}

// ConfineLaunchCmd builds the interactive launch. It becomes the agent user in a
// NON-login outer shell (`sudo -u <user> -H bash -c`, fresh env, HOME at the
// agent's home) that sources no agent-owned rc — so no agent code runs before
// confinement takes hold — and immediately execs the platform confinement wrapper
// by its ABSOLUTE path (so the agent's own PATH can't shadow it). The wrapper runs
// a LOGIN shell INSIDE the sandbox, which sources the agent's rc (honouring its
// ~/.local/bin PATH export), cd's to dir (or the agent's home), and execs the agent
// binary. agentHome + grantedDirs drive the deny/re-allow set; agentArgs are the
// operator's `--`-forwarded arguments, appended verbatim (each shell-quoted) to the
// agent's argv. profile, when non-empty, is exported as JENTIC_PROFILE before the
// wrapper exec so it carries into the confined session (the agent, and any `jentic`
// it runs, acts on the operator's checked-out agent profile without a flag). The
// caller wires os.Stdin/out/err. Callers MUST have checked ConfinementAvailable
// first — on an unsupported platform confineExec adds no wrapper, so reaching here
// unconfined is a programming error, not a security posture.
func ConfineLaunchCmd(ctx context.Context, agentUser, binary, dir, agentHome, profile string, grantedDirs, agentArgs []string) *exec.Cmd {
	// Scrub the operator's SSH/GPG agent handles first (see UnsetSensitiveEnvSnippet)
	// so a compromised agent can't authenticate as the operator over a forwarded
	// agent socket. Done in the snippet, so it holds regardless of sudoers env_keep.
	prefix := UnsetSensitiveEnvSnippet()
	if profile != "" {
		prefix += "export JENTIC_PROFILE=" + shellQuote(profile) + " && "
	}
	// The OUTER shell is deliberately NON-login (agentCmdContextNoLogin → `bash
	// -c`): it must source no agent-owned rc, so no agent code runs in the window
	// before the confinement wrapper takes hold. It only exports JENTIC_PROFILE and
	// execs the wrapper. The wrapper then runs a LOGIN shell (see confineExec), so
	// the agent's rc — and the PATH export that finds its binary in ~/.local/bin —
	// is still honoured, but now CONFINED. JENTIC_PROFILE is exported before the
	// exec so it carries through the wrapper into the confined session.
	inner := prefix + "exec " + confineExec(binary, dir, agentHome, grantedDirs, agentArgs)
	return agentCmdContextNoLogin(ctx, agentUser, inner)
}

// AccessKind classifies how a confined session can reach a directory.
type AccessKind int

const (
	// AccessReadWrite is a directory the session can both read and write: the
	// agent's own home and each granted directory.
	AccessReadWrite AccessKind = iota
	// AccessReadOnly is a directory the session can read/execute but not write:
	// the executable routes on its PATH, held read-only so the agent can't
	// rewrite the binaries it runs.
	AccessReadOnly
)

// SessionDir is one directory a confined agent session can reach, and how.
type SessionDir struct {
	Path string
	Kind AccessKind
}

// SessionAccess returns the complete set of directories a confined agent session
// can reach: the agent's own home and each granted directory (read/write), plus
// the executable routes on its PATH (read-only). It is the SINGLE source of truth
// shared by the confinement builders (SandboxProfile on macOS, bwrapArgs on
// Linux) and any display of "what the agent can see" (`jentic profile view`), so
// the two can never diverge. Paths are cleaned; the read-only routes are filtered
// to those that actually exist on this machine, exactly as the launcher computes
// them. Directories outside a denied human-home root are still reachable via the
// permissive base and are not enumerated here (they are not session-specific).
func SessionAccess(agentHome string, grantedDirs []string) []SessionDir {
	var dirs []SessionDir
	if agentHome != "" {
		dirs = append(dirs, SessionDir{Path: filepath.Clean(agentHome), Kind: AccessReadWrite})
	}
	for _, g := range grantedDirs {
		dirs = append(dirs, SessionDir{Path: filepath.Clean(g), Kind: AccessReadWrite})
	}
	for _, d := range execRouteDirs(agentHome) {
		dirs = append(dirs, SessionDir{Path: d, Kind: AccessReadOnly})
	}
	return dirs
}

// reopenDirs returns just the read/write directories from SessionAccess — the
// paths the confinement re-opens inside an otherwise-denied human-home root.
func reopenDirs(agentHome string, grantedDirs []string) []string {
	var out []string
	for _, d := range SessionAccess(agentHome, grantedDirs) {
		if d.Kind == AccessReadWrite {
			out = append(out, d.Path)
		}
	}
	return out
}

// roExecDirs returns just the read-only executable-route directories from
// SessionAccess.
func roExecDirs(agentHome string, grantedDirs []string) []string {
	var out []string
	for _, d := range SessionAccess(agentHome, grantedDirs) {
		if d.Kind == AccessReadOnly {
			out = append(out, d.Path)
		}
	}
	return out
}

// confineExec builds the confinement command that the outer NON-login shell execs:
// the ABSOLUTE-path wrapper (`/usr/bin/sandbox-exec …` / `<abs>/bwrap …`) running a
// LOGIN bash inside it, which in turn cd's into the working directory and execs the
// agent binary with its forwarded arguments. Putting the login shell INSIDE the
// wrapper is deliberate: the agent's rc files (and the PATH export that finds its
// binary in ~/.local/bin) are sourced under confinement, never in an unconfined
// pre-exec window. The wrapper path is absolute so the agent's own PATH can't
// shadow it and shed the sandbox. agentArgs are appended verbatim after the binary.
func confineExec(binary, dir, agentHome string, grantedDirs, agentArgs []string) string {
	login := confinedLoginSnippet(binary, dir, agentArgs)
	switch runtime.GOOS {
	case "darwin":
		profile := SandboxProfile(agentHome, grantedDirs)
		return shellQuote(sandboxExecPath) + " -p " + shellQuote(profile) +
			" " + shellQuote(agentLaunchShell) + " -lc " + shellQuote(login)
	case "linux":
		return strings.Join(bwrapArgs(agentHome, grantedDirs,
			[]string{agentLaunchShell, "-lc", login}), " ")
	default:
		// Unsupported — no confinement to add. Callers gate on
		// ConfinementAvailable, so reaching here means the launch was not guarded.
		// Still run the login shell so PATH resolution matches the confined path.
		return shellQuote(agentLaunchShell) + " -lc " + shellQuote(login)
	}
}

// confinedLoginSnippet is the command the confined LOGIN bash runs: cd into the
// working directory (the agent's home when none was chosen) and exec the agent
// binary with its forwarded arguments. It runs after the agent's rc has been
// sourced (so ~/.local/bin is on PATH) but wholly inside the confinement wrapper.
// Each argument is shell-quoted so spaces/globs/quotes reach the agent as single
// literal tokens.
func confinedLoginSnippet(binary, dir string, agentArgs []string) string {
	cd := `cd "$HOME"`
	if dir != "" {
		cd = "cd " + shellQuote(dir)
	}
	return cd + " && exec " + shellQuote(binary) + quotedArgsSuffix(agentArgs)
}

// quotedArgsSuffix renders agentArgs as a leading-space-separated, shell-quoted
// suffix (empty when there are none) for appending to a binary in the login-shell
// snippet. Each arg is quoted independently so spaces, globs, and quotes in an
// argument reach the agent as a single literal token.
func quotedArgsSuffix(agentArgs []string) string {
	if len(agentArgs) == 0 {
		return ""
	}
	var b strings.Builder
	for _, a := range agentArgs {
		b.WriteString(" ")
		b.WriteString(shellQuote(a))
	}
	return b.String()
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
	// first (always), then each granted directory. Sourced from the shared
	// SessionAccess so this can't drift from what `jentic profile view` shows.
	reopen := reopenDirs(agentHome, grantedDirs)

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
	for _, d := range roExecDirs(agentHome, grantedDirs) {
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
// sandbox marks read-only: the agent's OWN ~/.local/bin (where `jentic run`
// copies/installs the agent binary), the sanctioned shared tool dirs, and the
// system bin dirs, de-duplicated and filtered to those that exist. Making these
// write-denied is a non-negotiable boundary — it stops a compromised agent from
// rewriting the binaries `jentic run` executes to shed its own sandbox next run.
// The home-local dir matters most: it sits inside the agent's writable home, so
// without an explicit last-emitted deny the home re-allow would leave the very
// binary the launcher execs agent-writable.
func execRouteDirs(agentHome string) []string {
	var candidates []string
	if agentHome != "" {
		candidates = append(candidates, filepath.Join(agentHome, ".local", "bin"))
	}
	candidates = append(candidates, candidateSharedBinDirs...)
	candidates = append(candidates, systemBinDirs...)
	seen := map[string]bool{}
	var out []string
	for _, d := range candidates {
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

// sbplPath renders a filesystem path as an SBPL double-quoted string literal. It
// escapes the two characters special inside one (backslash and quote) AND strips
// every ASCII control character — most importantly the newline, which would
// otherwise end the current `(allow …)`/`(deny …)` form and let the remainder of
// the path be parsed as a NEW top-level rule (e.g. a `(allow file* (subpath "/"))`
// that reopens everything the profile just denied). The account name and grant
// paths that reach here are already control-char-free (ValidateHomeDir /
// ValidateGrantPath), so stripping is a belt-and-braces guarantee at the sink
// itself: no unvalidated caller can turn a path into extra SBPL.
func sbplPath(p string) string {
	p = stripControlChars(p)
	p = strings.ReplaceAll(p, `\`, `\\`)
	p = strings.ReplaceAll(p, `"`, `\"`)
	return `"` + p + `"`
}

// stripControlChars removes every ASCII control character (0x00–0x1f and 0x7f,
// which includes newline, carriage return, and tab) from s. These are the
// characters that let a value break out of a single SBPL rule line.
func stripControlChars(s string) string {
	if !strings.ContainsFunc(s, isControlChar) {
		return s
	}
	var b strings.Builder
	b.Grow(len(s))
	for _, r := range s {
		if !isControlChar(r) {
			b.WriteRune(r)
		}
	}
	return b.String()
}

// isControlChar reports whether r is an ASCII control character.
func isControlChar(r rune) bool { return r < 0x20 || r == 0x7f }

// ── Linux: bubblewrap ─────────────────────────────────────────────────────────

// bwrapArgs builds the `bwrap … -- <cmdArgv...>` argv (each token shell-quoted)
// that mirrors the macOS profile: use the ABSOLUTE bwrap path (so the agent's own
// PATH can't shadow it and shed confinement), bind the whole host read/write (DAC
// still applies inside the namespace — bwrap grants no privilege), then hide EVERY
// human-home root (/Users, /home) behind a tmpfs and re-bind only the agent's own
// home and the granted dirs over the top. Grants outside those roots stay visible
// through the root bind. Finally the executable routes are re-mounted read-only so
// the agent can't rewrite the binaries it runs. --die-with-parent tears the
// namespace (and its mounts) down when the session exits.
//
// cmdArgv is the command bwrap runs inside the namespace — the confined LOGIN shell
// (`/bin/bash -lc <snippet>`), which cd's to the working directory and execs the
// agent. Running the login shell here means the agent's rc is sourced UNDER
// confinement, never in an unconfined pre-exec window.
//
// Order matters: bwrap applies mounts left-to-right, so the tmpfs masks come
// first, the re-binds land on top, and the read-only exec-route binds come LAST so
// they hold even if a grant re-bound an overlapping path.
func bwrapArgs(agentHome string, grantedDirs, cmdArgv []string) []string {
	args := []string{bwrapPath, "--die-with-parent", "--dev-bind", "/", "/"}
	for _, root := range humanHomeRoots {
		if info, err := os.Stat(root); err == nil && info.IsDir() {
			args = append(args, "--tmpfs", root)
		}
	}
	for _, p := range reopenDirs(agentHome, grantedDirs) {
		if deniedRootOf(p) != "" {
			args = append(args, "--bind", p, p)
		}
	}
	for _, d := range roExecDirs(agentHome, grantedDirs) {
		args = append(args, "--ro-bind", d, d)
	}
	// The command to run inside the namespace ends the argv; `--` is bwrap's own
	// end-of-options marker so a flag in cmdArgv is never mistaken for a bwrap
	// option.
	args = append(args, "--")
	args = append(args, cmdArgv...)
	quoted := make([]string, len(args))
	for i, a := range args {
		quoted[i] = shellQuote(a)
	}
	return quoted
}

// usernsClonePath is the Debian/Ubuntu sysctl knob that gates unprivileged user
// namespaces. A var so tests can point the probe at a controlled path.
var usernsClonePath = "/proc/sys/kernel/unprivileged_userns_clone"

// unprivilegedUserNSEnabled reports whether the kernel permits unprivileged user
// namespaces, which bubblewrap needs to build its mount namespace without root.
// The sysctl is Debian/Ubuntu-specific, so a clean NOT-EXIST is the normal state
// on mainline/RHEL kernels — there unprivileged userns is generally on, and
// absence is treated as enabled. Any OTHER read failure (a masked or
// permission-denied /proc) means we could not actually probe the kernel, and is
// treated as DISABLED: the whole confinement model errors closed, so the prereq
// gate must not report "available" on a machine it couldn't verify.
func unprivilegedUserNSEnabled() bool {
	data, err := os.ReadFile(usernsClonePath)
	if errors.Is(err, os.ErrNotExist) {
		return true // knob absent → not gated here
	}
	if err != nil {
		return false // unreadable → fail closed, like the rest of confinement
	}
	return strings.TrimSpace(string(data)) != "0"
}
