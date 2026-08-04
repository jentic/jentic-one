package localagent

import (
	"context"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestSandboxProfileDeniesHumanHomesAndReopensGrants(t *testing.T) {
	agentHome := "/Users/Shared/alice-local-agent"
	grants := []string{"/Users/alice/projects/api"}
	p := SandboxProfile(agentHome, grants)

	mustContain := []string{
		"(version 1)",
		"(allow default)",
		// every human-home root is denied by default, not just the operator's
		`(deny file* (subpath "/Users"))`,
		`(deny file* (subpath "/home"))`,
		// the agent's own home (under /Users/Shared) is re-opened
		`(allow file* (subpath "/Users/Shared/alice-local-agent"))`,
		// the granted subtree is re-opened (wins by last-match over the /Users deny)
		`(allow file* (subpath "/Users/alice/projects/api"))`,
		// metadata traversal on the ancestor chain, root-first, via literal (not subpath)
		`(allow file-read-metadata (literal "/Users/alice"))`,
		`(allow file-read-metadata (literal "/Users/alice/projects"))`,
	}
	for _, want := range mustContain {
		if !strings.Contains(p, want) {
			t.Errorf("profile missing %q\n---\n%s", want, p)
		}
	}

	// Last-match-wins: the grant re-allow must appear AFTER the /Users deny.
	denyAt := strings.Index(p, `(deny file* (subpath "/Users"))`)
	allowAt := strings.Index(p, `(allow file* (subpath "/Users/alice/projects/api"))`)
	if denyAt < 0 || allowAt < 0 || allowAt < denyAt {
		t.Errorf("grant re-allow must come after the /Users deny (deny@%d allow@%d)", denyAt, allowAt)
	}

	// The ancestor grant must NOT be a subpath — that would re-expose the
	// ancestor's other children (the sibling leak we exist to close).
	if strings.Contains(p, `(allow file* (subpath "/Users/alice/projects"))`) {
		t.Error("ancestor must get metadata-only, not a subpath allow")
	}
}

func TestSandboxProfileMarksExecRoutesReadOnly(t *testing.T) {
	// /usr/bin exists on every macOS/Linux box and is a sanctioned exec route, so
	// the profile must deny writes to it — the non-negotiable self-escape boundary.
	p := SandboxProfile("/Users/Shared/agent", nil)
	if !strings.Contains(p, `(deny file-write* (subpath "/usr/bin"))`) {
		t.Errorf("exec routes must be write-denied\n%s", p)
	}
	// The write-deny must come LAST (after any re-allow) so it is authoritative.
	woAt := strings.Index(p, "(deny file-write*")
	reallowAt := strings.LastIndex(p, "(allow file*")
	if woAt < 0 {
		t.Fatalf("no exec-route write-deny found\n%s", p)
	}
	if reallowAt >= 0 && woAt < reallowAt {
		t.Errorf("exec-route write-deny must come after re-allows (deny@%d allow@%d)", woAt, reallowAt)
	}
}

func TestSandboxProfileIgnoresGrantsOutsideHome(t *testing.T) {
	p := SandboxProfile("/Users/Shared/agent", []string{"/opt/data", "/srv/things"})
	// Grants outside every denied home root are already covered by (allow
	// default); the profile must not emit re-allow rules for them.
	for _, outside := range []string{
		`(allow file* (subpath "/opt/data"))`,
		`(allow file* (subpath "/srv/things"))`,
	} {
		if strings.Contains(p, outside) {
			t.Errorf("profile should not re-allow out-of-home grant %q\n%s", outside, p)
		}
	}
}

func TestSandboxProfileReopensAgentHomeUnderUsers(t *testing.T) {
	// The agent home lives under /Users/Shared, inside the blanket /Users deny —
	// the profile MUST re-open it or the agent can't reach its own workspace.
	p := SandboxProfile("/Users/Shared/bob-local-agent", nil)
	if !strings.Contains(p, `(allow file* (subpath "/Users/Shared/bob-local-agent"))`) {
		t.Errorf("agent home under /Users must be re-opened\n%s", p)
	}
	// And its metadata-traversal ancestors so path resolution reaches it.
	if !strings.Contains(p, `(allow file-read-metadata (literal "/Users/Shared"))`) {
		t.Errorf("agent home ancestors must get metadata traversal\n%s", p)
	}
}

func TestSandboxProfileNoAgentHome(t *testing.T) {
	// With no agent home the human-home roots are still denied (the default
	// boundary does not depend on knowing the agent home).
	p := SandboxProfile("", nil)
	if !strings.Contains(p, `(deny file* (subpath "/Users"))`) {
		t.Errorf("human-home roots must be denied even without an agent home:\n%s", p)
	}
	if !strings.Contains(p, "(allow default)") {
		t.Errorf("expected the permissive base:\n%s", p)
	}
}

func TestSandboxProfileDedupesSharedAncestors(t *testing.T) {
	// Two grants under the same parent must not duplicate the ancestor metadata rule.
	p := SandboxProfile("/Users/Shared/agent", []string{
		"/Users/alice/projects/api",
		"/Users/alice/projects/web",
	})
	if got := strings.Count(p, `(allow file-read-metadata (literal "/Users/alice/projects"))`); got != 1 {
		t.Errorf("shared ancestor metadata rule emitted %d times, want 1\n%s", got, p)
	}
}

// SessionAccess is the single source of truth both the confinement builders and
// `jentic profile view` consume: the agent home + grants are read/write, the
// existing exec routes are read-only, and everything it reports read/write must
// be exactly what SandboxProfile re-opens (so the display can't drift from the
// mount).
func TestSessionAccessClassifiesAndFeedsSandbox(t *testing.T) {
	home := "/Users/Shared/alice-local-agent"
	grants := []string{"/Users/alice/projects/api"}
	dirs := SessionAccess(home, grants)

	var rw, ro []string
	for _, d := range dirs {
		switch d.Kind {
		case AccessReadWrite:
			rw = append(rw, d.Path)
		case AccessReadOnly:
			ro = append(ro, d.Path)
		}
	}
	// Home + grant are read/write, in that order.
	if len(rw) != 2 || rw[0] != home || rw[1] != grants[0] {
		t.Fatalf("read/write set = %v, want [%s %s]", rw, home, grants[0])
	}
	// /usr/bin exists on every dev box and must be reported read-only.
	foundBin := false
	for _, p := range ro {
		if p == "/usr/bin" {
			foundBin = true
		}
	}
	if !foundBin {
		t.Errorf("read-only routes %v missing /usr/bin", ro)
	}

	// The profile the launcher builds must re-open exactly the read/write set.
	p := SandboxProfile(home, grants)
	for _, w := range rw {
		if !strings.Contains(p, `(allow file* (subpath "`+w+`"))`) {
			t.Errorf("sandbox profile does not re-open read/write dir %q\n%s", w, p)
		}
	}
}

func TestSbplPathEscaping(t *testing.T) {
	got := sbplPath(`/tmp/a"b\c`)
	want := `"/tmp/a\"b\\c"`
	if got != want {
		t.Errorf("sbplPath escaping: got %s want %s", got, want)
	}
}

// The confined launch must scrub the operator's SSH/GPG agent handles before any
// agent code runs, so a prompt-injected agent can't reach back through a forwarded
// agent socket and act as the operator.
func TestConfineLaunchCmdUnsetsSensitiveEnv(t *testing.T) {
	cmd := ConfineLaunchCmd(context.Background(), "alice-local-agent", "/usr/bin/claude",
		"", "/Users/Shared/alice-local-agent", "", nil, nil)
	// The snippet is the last sudo arg (…-c <snippet>).
	snippet := cmd.Args[len(cmd.Args)-1]
	for _, v := range []string{"SSH_AUTH_SOCK", "SSH_AGENT_PID", "GPG_AGENT_INFO"} {
		if !strings.Contains(snippet, v) {
			t.Errorf("launch snippet must unset %s:\n%s", v, snippet)
		}
	}
	if !strings.HasPrefix(snippet, "unset ") {
		t.Errorf("the scrub must run first (snippet must start with `unset`):\n%s", snippet)
	}
}

// The outer sudo argv must name the shell by its ABSOLUTE path. sudo resolves a
// bare command name against its environment's PATH — and the launch hands sudo
// the curated launchEnv, which deliberately carries no PATH. With no sudoers
// secure_path to fall back on (macOS's default sudoers sets none), a bare `bash`
// fails the launch with "sudo: bash: command not found". The absolute path also
// matches the sudoers NOPASSWD rule exactly.
func TestConfineLaunchCmdUsesAbsoluteShell(t *testing.T) {
	cmd := ConfineLaunchCmd(context.Background(), "alice-local-agent", "/usr/bin/claude",
		"", "/Users/Shared/alice-local-agent", "", nil, nil)

	// argv shape: sudo -u <user> -H <shell> -c <snippet> — the shell is the
	// third-to-last arg and must be the absolute agentLaunchShell.
	shell := cmd.Args[len(cmd.Args)-3]
	if shell != agentLaunchShell {
		t.Errorf("outer shell must be the absolute %s (sudo can't resolve a bare name without PATH), got %q", agentLaunchShell, shell)
	}

	// The precondition that makes the absolute path load-bearing: the curated
	// launch env really does carry no PATH for sudo to resolve a bare name with.
	for _, kv := range cmd.Env {
		if strings.HasPrefix(kv, "PATH=") {
			t.Errorf("launch env must not carry PATH, got %q", kv)
		}
	}
}

// launchEnv forwards only the allowlisted terminal/locale hints, and never a
// sensitive or arbitrary operator variable.
func TestLaunchEnvIsAllowlisted(t *testing.T) {
	t.Setenv("TERM", "xterm-256color")
	t.Setenv("SSH_AUTH_SOCK", "/tmp/agent.sock")
	t.Setenv("AWS_SECRET_ACCESS_KEY", "leak-me")

	env := launchEnv()
	var sawTerm bool
	for _, kv := range env {
		if strings.HasPrefix(kv, "TERM=") {
			sawTerm = true
		}
		if strings.HasPrefix(kv, "SSH_AUTH_SOCK=") || strings.HasPrefix(kv, "AWS_SECRET_ACCESS_KEY=") {
			t.Errorf("launchEnv leaked a non-allowlisted var: %q", kv)
		}
	}
	if !sawTerm {
		t.Errorf("launchEnv should forward TERM when set, got %v", env)
	}
}

// A newline in a path must never survive into the profile: it would end the
// current SBPL form and let the remainder parse as a new top-level rule. sbplPath
// strips it (and every other control character) so the result is always a single
// quoted literal on one line.
func TestSbplPathStripsControlChars(t *testing.T) {
	got := sbplPath("/tmp/a\n(allow file* (subpath \"/\"))\t\r/b")
	// Newline, tab, and CR are removed; the injected rule text is now inert
	// content inside a single quoted literal, not a second rule.
	want := `"/tmp/a(allow file* (subpath \"/\"))/b"`
	if got != want {
		t.Errorf("sbplPath control-char stripping: got %s want %s", got, want)
	}
	if strings.Contains(got, "\n") {
		t.Errorf("sbplPath result must not contain a newline: %q", got)
	}
}

// Building a full profile from a path carrying a newline must not emit a second
// line that re-allows anything — the whole point of stripping in sbplPath.
func TestSandboxProfileNeutralisesNewlineInjection(t *testing.T) {
	evil := "/Users/Shared/agent\n(allow file* (subpath \"/\"))"
	p := SandboxProfile(evil, nil)
	if strings.Contains(p, "\n(allow file* (subpath \"/\"))\n") {
		t.Errorf("newline in agent home injected a standalone re-allow rule:\n%s", p)
	}
}

// existingHomeRoot returns a human-home root that actually exists on the running
// platform (/Users on macOS, /home on Linux) plus the two paths under it the test
// uses. bwrapArgs only emits `--tmpfs <root>` for a root that Stat-exists, so a
// test that hardcodes /Users passes on macOS but silently fails on the Linux CI
// runner where /Users is absent — this keeps the fixture meaningful on both.
func existingHomeRoot(t *testing.T) (root, agentHome, grant string) {
	t.Helper()
	for _, r := range humanHomeRoots {
		if info, err := os.Stat(r); err == nil && info.IsDir() {
			// Agent home lives under /Users/Shared on macOS; on Linux the agent home
			// is /opt (outside every human root), so for the "home re-bound over the
			// mask" assertions use an in-root path either way.
			return r, r + "/Shared/alice-local-agent", r + "/alice/projects/api"
		}
	}
	t.Skip("no human-home root exists on this platform")
	return "", "", ""
}

func TestBwrapArgsHidesHomesRebindsGrantsAndReadOnlyExec(t *testing.T) {
	root, agentHome, grant := existingHomeRoot(t)
	// Each returned token is individually shell-quoted; unquote for structural
	// assertions on the command shape.
	cmdArgv := []string{"/bin/bash", "-lc", "cd x && exec /usr/bin/claude"}
	quoted := bwrapArgs(agentHome, []string{grant, "/opt/outside"}, cmdArgv)
	args := make([]string, len(quoted))
	for i, q := range quoted {
		args[i] = strings.Trim(q, "'")
	}
	joined := strings.Join(args, " ")

	for _, want := range []string{
		// the wrapper is invoked by its ABSOLUTE path (can't be PATH-shadowed)
		bwrapPath + " --die-with-parent",
		// hide the human-home root behind a tmpfs...
		"--tmpfs " + root,
		// ...then re-bind the agent home and the in-home grant over it
		"--bind " + agentHome + " " + agentHome,
		"--bind " + grant + " " + grant,
		// exec routes re-mounted read-only (/usr/bin exists everywhere)
		"--ro-bind /usr/bin /usr/bin",
		// the command to run is introduced by bwrap's `--` end-of-options marker
		"-- /bin/bash -lc",
	} {
		if !strings.Contains(joined, want) {
			t.Errorf("bwrap args missing %q\ngot: %s", want, joined)
		}
	}
	if !filepath.IsAbs(strings.Trim(quoted[0], "'")) {
		t.Errorf("bwrap must be invoked by an absolute path, got %q", quoted[0])
	}
	// The out-of-home grant needs no bind — it stays visible through the root bind.
	if strings.Contains(joined, "--bind /opt/outside /opt/outside") {
		t.Errorf("out-of-home grant should not be re-bound:\n%s", joined)
	}
	// tmpfs must come before the re-bind so the bind lands on top; the read-only
	// exec bind must come after the grant re-bind.
	if strings.Index(joined, "--tmpfs "+root) > strings.Index(joined, "--bind "+grant) {
		t.Errorf("tmpfs must precede the grant re-bind:\n%s", joined)
	}
	if strings.Index(joined, "--bind "+grant) > strings.Index(joined, "--ro-bind /usr/bin") {
		t.Errorf("read-only exec bind must come after grant re-binds:\n%s", joined)
	}
}

// confinedLoginSnippet is what the confined LOGIN shell runs: cd into the working
// directory then exec the binary with its forwarded args, each shell-quoted so
// spaces and quotes survive as single tokens.
func TestConfinedLoginSnippetForwardsAgentArgs(t *testing.T) {
	got := confinedLoginSnippet("/usr/bin/claude", "/work",
		[]string{"--model", "opus", "-p", "hello world"})

	// cd's into the working directory, then execs the binary, then each arg in order.
	iCd := strings.Index(got, "cd "+shellQuote("/work"))
	iBin := strings.Index(got, "exec "+shellQuote("/usr/bin/claude"))
	iModel := strings.Index(got, "--model")
	iOpus := strings.Index(got, "opus")
	if iCd < 0 || iBin < iCd || iModel < iBin || iOpus < iModel {
		t.Fatalf("snippet must cd, then exec binary, then args in order, got:\n%s", got)
	}
	// A multi-word argument is a single quoted token, not two.
	if !strings.Contains(got, shellQuote("hello world")) {
		t.Errorf("multi-word arg must be one quoted token, got:\n%s", got)
	}
	// No dir → cd into the agent's own $HOME; no args → no trailing separator.
	bare := confinedLoginSnippet("/usr/bin/claude", "", nil)
	if !strings.HasPrefix(bare, `cd "$HOME" &&`) {
		t.Errorf("no-dir snippet must cd into $HOME, got: %q", bare)
	}
	if strings.HasSuffix(bare, " ") {
		t.Errorf("no-args snippet must not leave a trailing separator: %q", bare)
	}
}

// confineExec wraps the login shell in the ABSOLUTE-path confinement wrapper, so
// the agent's own PATH can never shadow the wrapper and shed the sandbox, and the
// login shell (which sources agent rc) runs INSIDE it.
func TestConfineExecUsesAbsoluteWrapperAndConfinedLoginShell(t *testing.T) {
	got := confineExec("/usr/bin/claude", "/work", "/Users/Shared/bot", nil,
		[]string{"--model", "opus"})

	// The wrapper is invoked by an absolute path (platform-specific).
	var wrapper string
	switch runtime.GOOS {
	case "darwin":
		wrapper = sandboxExecPath
	case "linux":
		wrapper = bwrapPath
	default:
		t.Skip("no confinement wrapper on this platform")
	}
	if !filepath.IsAbs(wrapper) {
		t.Fatalf("wrapper path must be absolute, got %q", wrapper)
	}
	if !strings.HasPrefix(got, shellQuote(wrapper)) && !strings.Contains(got, shellQuote(wrapper)+" ") {
		t.Errorf("confineExec must invoke the absolute wrapper %q, got:\n%s", wrapper, got)
	}
	// The login shell runs INSIDE the wrapper (so agent rc is sourced confined).
	// The two wrappers render the argv differently — darwin builds the string with
	// a bare `-lc` (sandbox-exec … 'bash' -lc '…'), while the Linux bwrap builder
	// shell-quotes EVERY token ('bash' '-lc' '…') — so assert whichever form this
	// platform actually emits.
	loginShell := shellQuote(agentLaunchShell) + " -lc "
	if runtime.GOOS == "linux" {
		loginShell = shellQuote(agentLaunchShell) + " " + shellQuote("-lc") + " "
	}
	if !strings.Contains(got, loginShell) {
		t.Errorf("confineExec must run a login shell (%s -lc) inside the wrapper, got:\n%s", agentLaunchShell, got)
	}
}

// A flag in the inner command must not be parsed as a bwrap option: bwrapArgs ends
// its own options with `--` before the command argv, so `-lc` reaches bash.
func TestBwrapArgsSeparatesInnerCommand(t *testing.T) {
	cmdArgv := []string{"/bin/bash", "-lc", "exec /usr/bin/claude --model opus"}
	quoted := bwrapArgs("/Users/Shared/bot", nil, cmdArgv)
	args := make([]string, len(quoted))
	for i, q := range quoted {
		args[i] = strings.Trim(q, "'")
	}
	// `--` immediately precedes the inner command, so `-lc` is bash's, not bwrap's.
	joined := strings.Join(args, " ")
	if !strings.Contains(joined, "-- /bin/bash -lc") {
		t.Errorf("inner command must follow bwrap's `--` marker, got:\n%s", joined)
	}
}

// On the real Linux layout the agent home is /opt/<user> — OUTSIDE every denied
// human-home root — so it must stay visible through the root bind (never tmpfs-
// masked, never re-bound), while a grant that lives under /home is masked then
// re-bound. This is the Linux analog of the /Users/Shared case above and the
// end-to-end guarantee behind an /opt home reaching through confinement.
func TestBwrapArgsLinuxOptHomeStaysVisible(t *testing.T) {
	cmdArgv := []string{"/bin/bash", "-lc", "cd /home/alice/projects/api && exec /usr/bin/claude"}
	quoted := bwrapArgs("/opt/alice-local-agent",
		[]string{"/home/alice/projects/api"}, cmdArgv)
	args := make([]string, len(quoted))
	for i, q := range quoted {
		args[i] = strings.Trim(q, "'")
	}
	joined := strings.Join(args, " ")

	// The /opt home is outside /Users and /home, so it needs no bind — it is
	// reachable through the root --dev-bind that opens the whole host.
	if strings.Contains(joined, "--bind /opt/alice-local-agent") {
		t.Errorf("an /opt agent home must NOT be re-bound (it is visible via the root bind):\n%s", joined)
	}
	// A /home grant IS inside a denied root, so it must be re-bound over the mask.
	if !strings.Contains(joined, "--bind /home/alice/projects/api /home/alice/projects/api") {
		t.Errorf("a /home grant must be re-bound over the tmpfs mask:\n%s", joined)
	}
	// The cd into the grant now lives inside the login snippet (bwrap no longer
	// emits --chdir); the snippet cd's there before exec'ing the agent.
	if strings.Contains(joined, "--chdir") {
		t.Errorf("bwrap should no longer emit --chdir (cd is inside the login snippet):\n%s", joined)
	}
	// SessionAccess (the shared source both platforms consume) reports the /opt
	// home as read/write, so the display can't claim the agent lacks its own home.
	rw := reopenDirs("/opt/alice-local-agent", []string{"/home/alice/projects/api"})
	if len(rw) == 0 || rw[0] != "/opt/alice-local-agent" {
		t.Errorf("the /opt home must be the first read/write dir SessionAccess reports, got %v", rw)
	}
}

// ── prerequisite preflight ──────────────────────────────────────────────────

// stubBinsOnPath points PATH at a fresh dir holding an executable stub for each
// named binary, so lookPathPrereq/hasBinary resolve exactly the given set and
// nothing else. Returns the dir. POSIX-only (executable-bit stubs).
func stubBinsOnPath(t *testing.T, bins ...string) string {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("PATH-stub technique is POSIX-only")
	}
	dir := t.TempDir()
	for _, b := range bins {
		if err := os.WriteFile(filepath.Join(dir, b), []byte("#!/bin/sh\nexit 0\n"), 0o755); err != nil {
			t.Fatalf("write stub %q: %v", b, err)
		}
	}
	t.Setenv("PATH", dir)
	return dir
}

// lookPathPrereq reports missing when the binary is absent from PATH, with the
// supplied reason and hint, and OK when present.
func TestLookPathPrereqReportsPresence(t *testing.T) {
	stubBinsOnPath(t) // empty PATH: nothing resolves

	missing := lookPathPrereq("thing", "definitely-not-a-real-bin", "it is missing", "install it")
	if missing.OK {
		t.Fatal("expected missing prereq for an absent binary")
	}
	if missing.Reason != "it is missing" || missing.Hint != "install it" {
		t.Errorf("reason/hint not carried through: %+v", missing)
	}

	stubBinsOnPath(t, "present-bin")
	ok := lookPathPrereq("thing", "present-bin", "unused", "unused")
	if !ok.OK {
		t.Errorf("expected satisfied prereq for a present binary, got %+v", ok)
	}
	if ok.Reason != "" || ok.Hint != "" {
		t.Errorf("a satisfied prereq must carry no reason/hint, got %+v", ok)
	}
}

// aclPrereq is unsatisfied unless BOTH setfacl and getfacl resolve.
func TestACLPrereqRequiresBothBinaries(t *testing.T) {
	stubBinsOnPath(t, "setfacl") // getfacl absent
	if p := aclPrereq(); p.OK {
		t.Error("acl prereq must be missing when getfacl is absent")
	}

	stubBinsOnPath(t, "setfacl", "getfacl")
	if p := aclPrereq(); !p.OK {
		t.Errorf("acl prereq must be satisfied when both binaries resolve, got %+v", p)
	}
}

// The Linux install hint names both bubblewrap and acl, and picks a command that
// matches whichever package manager is on PATH.
func TestResolveLinuxInstallHintMatchesPackageManager(t *testing.T) {
	stubBinsOnPath(t, "apt")
	hint := resolveLinuxInstallHint()
	if !strings.Contains(hint, "apt install") || !strings.Contains(hint, "bubblewrap") || !strings.Contains(hint, "acl") {
		t.Errorf("apt hint should install bubblewrap+acl via apt, got %q", hint)
	}

	stubBinsOnPath(t, "dnf")
	if hint := resolveLinuxInstallHint(); !strings.Contains(hint, "dnf install") {
		t.Errorf("dnf hint expected, got %q", hint)
	}

	stubBinsOnPath(t) // no package manager
	if hint := resolveLinuxInstallHint(); !strings.Contains(hint, "package manager") {
		t.Errorf("generic fallback hint expected with no pkg manager, got %q", hint)
	}
}

// ConfinementAvailable is the launch gate; it must agree with MissingPrereqs —
// available exactly when nothing is missing — so the setup-time and launch-time
// gates can never disagree.
func TestConfinementAvailableAgreesWithMissingPrereqs(t *testing.T) {
	ok, reason := ConfinementAvailable()
	missing := MissingPrereqs()
	if ok && len(missing) != 0 {
		t.Errorf("available but %d prereqs missing", len(missing))
	}
	if !ok {
		if len(missing) == 0 {
			t.Error("unavailable but no prereqs reported missing")
		}
		if reason != missing[0].Reason {
			t.Errorf("ConfinementAvailable reason %q != first missing prereq reason %q", reason, missing[0].Reason)
		}
	}
}

// The userns probe distinguishes "knob absent" (mainline/RHEL kernels — userns
// generally on, treated as enabled) from "knob unreadable" (masked or denied
// /proc — the kernel could NOT be verified, so it must fail CLOSED like the rest
// of the confinement model) and reads the sysctl value when it is present.
func TestUnprivilegedUserNSProbe(t *testing.T) {
	orig := usernsClonePath
	t.Cleanup(func() { usernsClonePath = orig })
	dir := t.TempDir()

	// Knob absent → enabled (not gated on this kernel).
	usernsClonePath = filepath.Join(dir, "does-not-exist")
	if !unprivilegedUserNSEnabled() {
		t.Error("absent knob must be treated as enabled")
	}

	// Knob present and set → the value decides.
	usernsClonePath = filepath.Join(dir, "clone")
	if err := os.WriteFile(usernsClonePath, []byte("0\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if unprivilegedUserNSEnabled() {
		t.Error("knob set to 0 must be treated as disabled")
	}
	if err := os.WriteFile(usernsClonePath, []byte("1\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if !unprivilegedUserNSEnabled() {
		t.Error("knob set to 1 must be treated as enabled")
	}

	// Knob present but unreadable → fail closed (root can read anything, skip).
	if os.Geteuid() == 0 {
		t.Log("running as root; skipping the unreadable-knob case")
		return
	}
	if err := os.Chmod(usernsClonePath, 0o000); err != nil {
		t.Fatal(err)
	}
	if unprivilegedUserNSEnabled() {
		t.Error("an unreadable knob must fail closed (treated as disabled)")
	}
}

// The agent's own ~/.local/bin — where `jentic run` copies/installs the agent
// binary — must be in the read-only exec routes when it exists: it sits inside
// the agent's writable home, so without an explicit write-deny the home
// re-allow would leave the launched binary agent-writable (a self-modification
// route). Both confinement builders consume it via SessionAccess.
func TestAgentLocalBinIsReadOnlyExecRoute(t *testing.T) {
	home := t.TempDir()
	localBin := filepath.Join(home, ".local", "bin")
	if err := os.MkdirAll(localBin, 0o755); err != nil {
		t.Fatal(err)
	}

	foundRO := false
	for _, d := range SessionAccess(home, nil) {
		if d.Path == localBin {
			if d.Kind != AccessReadOnly {
				t.Fatalf("%s must be read-only, got kind %v", localBin, d.Kind)
			}
			foundRO = true
		}
	}
	if !foundRO {
		t.Fatalf("SessionAccess(%s, nil) does not report %s as a read-only route", home, localBin)
	}

	// macOS: the write-deny must be emitted, and AFTER the home re-allow so
	// last-match-wins keeps it authoritative inside the re-opened home.
	p := SandboxProfile(home, nil)
	deny := `(deny file-write* (subpath ` + sbplPath(localBin) + `))`
	allowHome := `(allow file* (subpath ` + sbplPath(filepath.Clean(home)) + `))`
	di, ai := strings.Index(p, deny), strings.Index(p, allowHome)
	if di == -1 {
		t.Fatalf("sandbox profile missing the ~/.local/bin write-deny:\n%s", p)
	}
	if ai != -1 && di < ai {
		t.Errorf("~/.local/bin write-deny must come after the home re-allow (last-match-wins):\n%s", p)
	}

	// Linux: the ro-bind must land after any home bind so it masks it. Each
	// returned token is individually shell-quoted; unquote for the assertions.
	quoted := bwrapArgs(home, nil, []string{"/bin/bash", "-lc", "true"})
	args := make([]string, len(quoted))
	for i, q := range quoted {
		args[i] = strings.Trim(q, "'")
	}
	joined := strings.Join(args, " ")
	roBind := "--ro-bind " + localBin + " " + localBin
	if !strings.Contains(joined, roBind) {
		t.Fatalf("bwrap args missing the ~/.local/bin ro-bind:\n%s", joined)
	}
	if bi := strings.Index(joined, "--bind "+home); bi != -1 && strings.Index(joined, roBind) < bi {
		t.Errorf("~/.local/bin ro-bind must come after the home bind (later mounts mask earlier ones):\n%s", joined)
	}
}
