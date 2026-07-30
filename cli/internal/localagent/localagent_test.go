package localagent

import (
	"context"
	"os"
	"os/user"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestLookupAndKnown(t *testing.T) {
	if _, ok := Lookup("claude"); !ok {
		t.Fatal("expected claude to be a known agent")
	}
	if _, ok := Lookup("nope"); ok {
		t.Fatal("did not expect an unknown agent to resolve")
	}
	known := Known()
	if len(known) == 0 || known[0] != "claude" {
		t.Fatalf("Known() = %v, want claude present", known)
	}
}

func TestDefaultUserName(t *testing.T) {
	if got := DefaultUserName("alice"); got != "alice-local-agent" {
		t.Fatalf("DefaultUserName = %q", got)
	}
}

func TestDefaultHomeDir(t *testing.T) {
	got := DefaultHomeDir("alice-local-agent")
	// The home must live under a shared, world-traversable parent — never under
	// any human's home — so the operator can be granted in without widening a home.
	var wantPrefix string
	if runtime.GOOS == "darwin" {
		wantPrefix = "/Users/Shared/"
	} else {
		wantPrefix = "/opt/"
	}
	if !strings.HasPrefix(got, wantPrefix) || !strings.HasSuffix(got, "alice-local-agent") {
		t.Fatalf("DefaultHomeDir = %q, want %s…alice-local-agent", got, wantPrefix)
	}
	if strings.HasPrefix(got, "/Users/alice") || strings.HasPrefix(got, "/home/") {
		t.Fatalf("DefaultHomeDir = %q must not sit under a human home", got)
	}
}

// TestCreateAccountCmds guards the privileged account-creation recipe: every
// step must be sudo-fronted and name the agent account, the ordered steps must
// culminate in an inherited operator grant on the agent's home, and the operator
// grant must carry the inheritance flags (without them the operator loses access
// to whatever the agent creates later).
func TestCreateAccountCmds(t *testing.T) {
	steps := CreateAccountCmds("alice", "alice-local-agent", DefaultHomeDir("alice-local-agent"))
	if len(steps) == 0 {
		t.Fatal("expected at least one account-creation step")
	}
	for _, s := range steps {
		if s.Cmd.Args[0] != "sudo" {
			t.Errorf("step %q: expected sudo-fronted command, got %v", s.What, s.Cmd.Args)
		}
		if !strings.Contains(strings.Join(s.Cmd.Args, " "), "alice-local-agent") {
			t.Errorf("step %q: args do not name the agent account: %v", s.What, s.Cmd.Args)
		}
	}
	// The first step creates the account; a later step grants the operator in.
	joined := strings.Join(steps[0].Cmd.Args, " ")
	if runtime.GOOS == "darwin" {
		if !strings.Contains(joined, "sysadminctl") || !strings.Contains(joined, "-addUser") {
			t.Errorf("first macOS step should be sysadminctl -addUser: %v", steps[0].Cmd.Args)
		}
	} else if !strings.Contains(joined, "useradd") {
		t.Errorf("first Linux step should be useradd: %v", steps[0].Cmd.Args)
	}
	var allBuilder strings.Builder
	for _, s := range steps {
		allBuilder.WriteString(strings.Join(s.Cmd.Args, " "))
		allBuilder.WriteString("\n")
	}
	all := allBuilder.String()
	if !strings.Contains(all, "alice") {
		t.Error("expected the operator to be granted access somewhere in the recipe")
	}
	if runtime.GOOS == "darwin" {
		if !strings.Contains(all, "file_inherit") || !strings.Contains(all, "directory_inherit") {
			t.Error("macOS operator grant must be inherited (file_inherit/directory_inherit)")
		}
		// The recursive operator grant descends into SIP/TCC-protected home-template
		// files nobody can ACL, so it must be best-effort or account creation aborts.
		for _, s := range steps {
			if strings.Contains(s.What, "grant the operator") && !s.BestEffort {
				t.Error("macOS operator-grant step must be BestEffort (SIP/TCC-protected home files can't be ACLed)")
			}
		}
		// The operator grant must carry add_subdirectory: bootstrap writes the agent
		// identity by `mkdir <home>/.jentic` as the operator, and the macOS "write"
		// shorthand omits add_subdirectory on a directory (files ok, mkdir EACCES).
		if !strings.Contains(all, "add_subdirectory") {
			t.Error("macOS operator grant must include add_subdirectory or mkdir <home>/.jentic fails")
		}
	} else if !strings.Contains(all, "-d -m") && !strings.Contains(all, "-d") {
		t.Error("Linux operator grant must include a default ACL for future contents")
	}
}

// TestGrantOperatorHomeCmd guards the standalone operator-grant builder used on
// The three recursive chowns must all pass -h (no-dereference) so a symlink the
// agent planted in a tree it owns can't redirect a privileged recursive chown onto
// a target outside the tree (e.g. /etc/passwd). This is a priv-esc boundary, so
// assert the flag is present on every one.
func TestRecursiveChownsDoNotDereferenceSymlinks(t *testing.T) {
	home := "/Users/Shared/alice-local-agent"
	cases := []struct {
		name string
		args []string
	}{
		{"ReownHomeCmd", ReownHomeCmd("alice", home).Args},
		{"ReclaimAgentHomeCmd", ReclaimAgentHomeCmd("alice-local-agent", home).Args},
		{"ChownToAgentCmd", ChownToAgentCmd("alice-local-agent", AgentConfigDir(home)).Args},
	}
	for _, c := range cases {
		// The chown flag token is args[2] (sudo, chown, <flags>, owner, path); it must
		// be recursive AND carry the no-dereference 'h'.
		if len(c.args) < 3 || c.args[0] != "sudo" || c.args[1] != "chown" {
			t.Fatalf("%s: unexpected shape %v", c.name, c.args)
		}
		flag := c.args[2]
		if !strings.HasPrefix(flag, "-") || !strings.ContainsRune(flag, 'R') || !strings.ContainsRune(flag, 'h') {
			t.Errorf("%s: chown flags %q must be recursive (R) and no-dereference (h)", c.name, flag)
		}
	}
}

// the account-reuse path: sudo-fronted, names the operator + home, RECURSIVE (so
// agent-owned profiles under <home>/.jentic stay operator-readable), inherited,
// and (on macOS) carrying the directory-mutation bits so `mkdir <home>/.jentic`
// works.
func TestGrantOperatorHomeCmd(t *testing.T) {
	homeDir := "/Users/Shared/alice-local-agent"
	joined := strings.Join(GrantOperatorHomeCmd("alice", homeDir).Args, " ")
	if !strings.HasPrefix(joined, "sudo ") {
		t.Errorf("operator grant must be sudo-fronted: %s", joined)
	}
	if !strings.Contains(joined, "alice") || !strings.Contains(joined, homeDir) {
		t.Errorf("operator grant must name the operator and home: %s", joined)
	}
	if runtime.GOOS == "darwin" {
		// Recursion via `find ! -type l` (chmod -R follows symlinks / refuses -h).
		if !strings.Contains(joined, "find") || !strings.Contains(joined, "! -type l") {
			t.Errorf("macOS operator grant must recurse via find ! -type l: %s", joined)
		}
		for _, bit := range []string{"add_subdirectory", "file_inherit", "directory_inherit"} {
			if !strings.Contains(joined, bit) {
				t.Errorf("macOS operator grant missing %q bit: %s", bit, joined)
			}
		}
	} else {
		if !strings.Contains(joined, "-R") {
			t.Errorf("Linux operator grant must be recursive (-R): %s", joined)
		}
		if !strings.Contains(joined, "-d") {
			t.Errorf("Linux operator grant must include a default ACL: %s", joined)
		}
	}
}

func TestClassify(t *testing.T) {
	home := "/Users/alice"
	cases := []struct {
		name string
		dir  string
		want BanClass
	}{
		// SoftBan: home roots — the dir itself is off-limits, subdirs are fine.
		{"operator home root", "/Users/alice", SoftBan},
		{"another user home", "/Users/bob", SoftBan},
		{"linux other home", "/home/bob", SoftBan},
		// HardBan: sensitive dotfile subtrees — root AND descendants off-limits.
		{"operator ssh dir", "/Users/alice/.ssh", HardBan},
		{"operator jentic dir", "/Users/alice/.jentic", HardBan},
		{"deep under jentic dir", "/Users/alice/.jentic/profiles/default", HardBan},
		{"deep under ssh dir", "/Users/alice/.ssh/keys/id", HardBan},
		// HardBan: system trees.
		{"system etc", "/etc", HardBan},
		{"system usr subdir", "/usr/local/bin", HardBan},
		{"root", "/", HardBan},
		// NotBanned: ordinary grantable paths, including subdirs of the home root.
		{"neutral shared path", "/Users/Shared/alice-local-agent/work", NotBanned},
		{"project under home is not the home root", "/Users/alice/projects/api", NotBanned},
		{"opt path", "/opt/alice-local-agent", NotBanned},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := Classify(tc.dir, home)
			if got.Class != tc.want {
				t.Fatalf("Classify(%q).Class = %v, want %v (reason %q)", tc.dir, got.Class, tc.want, got.Reason)
			}
			if (got.Reason != "") != (tc.want != NotBanned) {
				t.Fatalf("Classify(%q) reason=%q inconsistent with class %v", tc.dir, got.Reason, got.Class)
			}
		})
	}
}

func TestGrantAndRevokeCmdShape(t *testing.T) {
	// The exact args are platform-specific; assert each layer's command is
	// sudo-fronted and names the agent user + its target path so it can't
	// silently no-op.
	dir := filepath.Clean("/Users/Shared/x/work")
	home := filepath.Clean("/Users/alice")
	for _, c := range []struct {
		name   string
		args   []string
		target string
	}{
		{"traverse", TraverseGrantCmd("a-local-agent", home).Args, home},
		{"leaf-grant", LeafGrantCmd("a-local-agent", dir).Args, dir},
		{"leaf-revoke", LeafRevokeCmd("a-local-agent", dir).Args, dir},
	} {
		if c.args[0] != "sudo" {
			t.Errorf("%s: expected sudo-fronted command, got %v", c.name, c.args)
		}
		joined := strings.Join(c.args, " ")
		if !strings.Contains(joined, "a-local-agent") || !strings.Contains(joined, c.target) {
			t.Errorf("%s: args missing user or target: %v", c.name, c.args)
		}
	}
}

// TestCanRunAsAgentCmdShape guards the preflight that confirms the operator can
// become the agent user: it must be sudo-fronted, target the agent account, and
// run a trivial no-op so a non-zero exit means "couldn't switch" (declined
// password / no rights) rather than anything about the workload.
func TestCanRunAsAgentCmdShape(t *testing.T) {
	args := CanRunAsAgentCmd(context.Background(), "alice-local-agent").Args
	if args[0] != "sudo" {
		t.Fatalf("preflight must be sudo-fronted, got %v", args)
	}
	joined := strings.Join(args, " ")
	if !strings.Contains(joined, "-u alice-local-agent") {
		t.Errorf("preflight must switch to the agent user: %v", args)
	}
	if !strings.HasSuffix(joined, " true") {
		t.Errorf("preflight must run a no-op (true) so exit code reflects the switch only: %v", args)
	}
}

// TestTeardownCmdShape guards the reset primitives: every one is sudo-fronted and
// names the agent user (and, where relevant, the target path) so a reset can't
// silently no-op. TraverseRevokeCmd must mirror TraverseGrantCmd's target.
func TestTeardownCmdShape(t *testing.T) {
	home := filepath.Clean("/Users/alice")
	homeDir := "/Users/Shared/alice-local-agent"
	cases := []struct {
		name       string
		args       []string
		wantTarget string // "" = don't assert a path, just the user
		wantUser   bool
	}{
		{"traverse-revoke", TraverseRevokeCmd("alice-local-agent", home).Args, home, true},
		{"reown-home", ReownHomeCmd("alice", homeDir).Args, homeDir, false},
		{"delete-home", DeleteHomeCmd(homeDir).Args, homeDir, false},
		{"remove-identity", RemoveAgentIdentityCmd(homeDir + "/.jentic").Args, homeDir + "/.jentic", false},
		{"remove-sudoers", RemoveSudoersCmd("alice-local-agent").Args, "", true},
		{"delete-account", DeleteAccountCmd("alice-local-agent").Args, "", true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if tc.args[0] != "sudo" {
				t.Errorf("%s: expected sudo-fronted command, got %v", tc.name, tc.args)
			}
			joined := strings.Join(tc.args, " ")
			if tc.wantUser && !strings.Contains(joined, "alice-local-agent") {
				t.Errorf("%s: args do not name the agent user: %v", tc.name, tc.args)
			}
			if tc.wantTarget != "" && !strings.Contains(joined, tc.wantTarget) {
				t.Errorf("%s: args do not name target %q: %v", tc.name, tc.wantTarget, tc.args)
			}
		})
	}

	// TraverseRevokeCmd reverses TraverseGrantCmd on the same target.
	if g, r := TraverseGrantCmd("a-local-agent", home).Args, TraverseRevokeCmd("a-local-agent", home).Args; strings.Join(g, " ") == strings.Join(r, " ") {
		t.Error("traverse grant and revoke must not be identical commands")
	}

	// ReownHomeCmd must use `chown -Rfh`: -R to re-own the whole tree, -f to
	// suppress per-file errors on the SIP/TCC-protected template files a macOS home
	// carries (which nobody can chown) so the command still processes everything
	// else, and -h (no-dereference) so an agent-planted symlink can't redirect the
	// recursive chown onto a target outside the tree. reset marks this step
	// best-effort, so a residual non-zero exit is reported, not fatal.
	reown := strings.Join(ReownHomeCmd("alice", homeDir).Args, " ")
	if !strings.Contains(reown, "chown -Rfh ") {
		t.Errorf("re-own must use `chown -Rfh` (recursive, force, no-dereference): %s", reown)
	}

	// ReclaimAgentHomeCmd is the inverse: it re-owns the home to the AGENT (used when
	// reusing a home a prior reset handed back to the operator). It must be
	// sudo-fronted, name the agent user + home, and use `-Rfh` for the same reasons.
	reclaim := strings.Join(ReclaimAgentHomeCmd("alice-local-agent", homeDir).Args, " ")
	if !strings.Contains(reclaim, "chown -Rfh ") {
		t.Errorf("reclaim must use `chown -Rfh`: %s", reclaim)
	}
	if !strings.Contains(reclaim, "alice-local-agent") || !strings.Contains(reclaim, homeDir) {
		t.Errorf("reclaim must name the agent user and home: %s", reclaim)
	}
	if !strings.HasPrefix(reclaim, "sudo ") {
		t.Errorf("reclaim must be sudo-fronted: %s", reclaim)
	}

	// DeleteAccountCmd must keep the home (the home is settled separately): it must
	// NOT carry a home-removing flag. On macOS we delete the DirectoryService
	// record with `dscl . -delete` (no filesystem side-effect) rather than
	// `sysadminctl -deleteUser`, whose `-keepHome` flag is rejected at runtime on
	// recent macOS; on Linux we use `userdel` without -r.
	del := strings.Join(DeleteAccountCmd("alice-local-agent").Args, " ")
	if runtime.GOOS == "darwin" {
		if !strings.Contains(del, "dscl") || !strings.Contains(del, "-delete") {
			t.Errorf("macOS account delete must use `dscl . -delete` so the home survives: %s", del)
		}
		if strings.Contains(del, "sysadminctl") {
			t.Errorf("macOS account delete must not use sysadminctl (-keepHome unsupported on recent macOS): %s", del)
		}
	} else if strings.Contains(del, " -r") || strings.HasSuffix(del, "-r") {
		t.Errorf("Linux account delete must NOT pass -r (home is settled separately): %s", del)
	}
}

// TestMacTraverseRevokeMatchesGrant guards the macOS ACE-match requirement: the
// traverse revoke must name the same "allow execute" permission string as the
// grant, or `chmod -a` won't find the entry to drop.
func TestMacTraverseRevokeMatchesGrant(t *testing.T) {
	if runtime.GOOS != "darwin" {
		t.Skip("macOS-specific ACL permission set")
	}
	grant := strings.Join(TraverseGrantCmd("a-local-agent", "/Users/alice").Args, " ")
	revoke := strings.Join(TraverseRevokeCmd("a-local-agent", "/Users/alice").Args, " ")
	if !strings.Contains(grant, "allow execute") || !strings.Contains(revoke, "allow execute") {
		t.Errorf("traverse grant/revoke must both name `allow execute`: grant=%q revoke=%q", grant, revoke)
	}
}

// TestRemoveSudoersIsSafe guards two properties of the sudoers teardown: it edits
// the fixed jentic-agent drop-in and validates with visudo before installing, so
// a malformed result can never brick sudo.
func TestRemoveSudoersIsSafe(t *testing.T) {
	joined := strings.Join(RemoveSudoersCmd("alice-local-agent").Args, " ")
	if !strings.Contains(joined, "/etc/sudoers.d/jentic-agent") {
		t.Errorf("sudoers removal must target the jentic-agent drop-in: %s", joined)
	}
	if !strings.Contains(joined, "visudo") {
		t.Errorf("sudoers removal must validate with visudo before installing: %s", joined)
	}
}

// TestSudoersRuleIsScopedNotRoot guards that the passwordless-launch rule grants
// only "become the agent user to run the login shell" — never a root capability.
func TestSudoersRuleIsScopedNotRoot(t *testing.T) {
	rule := SudoersRule("alice", "alice-local-agent")
	want := "alice ALL=(alice-local-agent) NOPASSWD: /bin/bash"
	if rule != want {
		t.Errorf("sudoers rule = %q, want %q", rule, want)
	}
	// The runas spec must name the unprivileged agent account, not root/ALL.
	if strings.Contains(rule, "(root)") || strings.Contains(rule, "(ALL)") || strings.Contains(rule, "=(ALL:ALL)") {
		t.Errorf("passwordless rule must not grant root: %q", rule)
	}
}

// TestInstallSudoersIsSafeAndIdempotent guards that the install edits the fixed
// drop-in, validates with visudo before writing (so a bad edit can't brick sudo),
// and only appends the rule when it is not already present (idempotent re-run).
func TestInstallSudoersIsSafeAndIdempotent(t *testing.T) {
	joined := strings.Join(InstallSudoersCmd("alice", "alice-local-agent").Args, " ")
	if !strings.Contains(joined, "/etc/sudoers.d/jentic-agent") {
		t.Errorf("sudoers install must target the jentic-agent drop-in: %s", joined)
	}
	if !strings.Contains(joined, "visudo -cf") {
		t.Errorf("sudoers install must validate with visudo before installing: %s", joined)
	}
	if !strings.Contains(joined, "grep -qxF") {
		t.Errorf("sudoers install must be idempotent (only append when absent): %s", joined)
	}
	if !strings.Contains(joined, "install -m 0440") {
		t.Errorf("sudoers drop-in must be installed mode 0440: %s", joined)
	}
}

// TestRemoveSudoersDropsInstalledRule guards that the teardown grep-filter removes
// exactly the line InstallSudoersCmd writes — i.e. reset undoes the install. The
// removal filters on the agent user, which appears in the rule's runas spec.
func TestRemoveSudoersDropsInstalledRule(t *testing.T) {
	rule := SudoersRule("alice", "alice-local-agent")
	remove := strings.Join(RemoveSudoersCmd("alice-local-agent").Args, " ")
	// The removal script greps out lines matching the agent user; that token must
	// be present in the installed rule so the filter catches it.
	if !strings.Contains(rule, "alice-local-agent") {
		t.Fatalf("installed rule must contain the agent user so removal matches it: %q", rule)
	}
	if !strings.Contains(remove, "alice-local-agent") {
		t.Errorf("removal must filter on the agent user: %s", remove)
	}
}

// TestMacLeafGrantIncludesDeleteBits guards against the macOS shorthand bug: a
// "write" grant on a directory expands to add_file only, so the agent could
// create but not delete/rename files (breaking write-to-temp-then-rename and
// leaving `test -w` false, which re-prompted on every launch). The explicit set
// must carry the directory-mutation bits and be symmetric between grant/revoke.
func TestMacLeafGrantIncludesDeleteBits(t *testing.T) {
	if runtime.GOOS != "darwin" {
		t.Skip("macOS-specific ACL permission set")
	}
	grant := strings.Join(LeafGrantCmd("a-local-agent", "/Users/Shared/x/work").Args, " ")
	for _, bit := range []string{"delete", "delete_child", "add_subdirectory"} {
		if !strings.Contains(grant, bit) {
			t.Errorf("leaf grant missing %q bit (dir writes would fail): %s", bit, grant)
		}
	}
	// Revoke must name the identical permission string so macOS can drop the ACE.
	revoke := strings.Join(LeafRevokeCmd("a-local-agent", "/Users/Shared/x/work").Args, " ")
	if !strings.Contains(revoke, macLeafACE) || !strings.Contains(grant, macLeafACE) {
		t.Errorf("grant/revoke permission strings must match macLeafACE")
	}
}

// TestLinuxLeafGrantAndRevokeUseSetfaclPairs guards the Linux ACL idiom: the leaf
// grant lays down BOTH an access ACL (-m) and a matching default ACL (-d -m) so
// files the agent creates later inherit the grant, and the revoke drops BOTH with
// the mirror (-x / -d -x) so no half-removed default ACL is left behind. rwX (capital
// X) is required so the execute bit is granted on directories but not on plain files.
func TestLinuxLeafGrantAndRevokeUseSetfaclPairs(t *testing.T) {
	if runtime.GOOS == "darwin" {
		t.Skip("Linux-specific setfacl idiom")
	}
	dir := "/home/alice/projects/api"
	grant := strings.Join(LeafGrantCmd("a-local-agent", dir).Args, " ")
	for _, want := range []string{
		"setfacl -R -m u:'a-local-agent':rwX",
		"setfacl -R -d -m u:'a-local-agent':rwX",
	} {
		if !strings.Contains(grant, want) {
			t.Errorf("Linux leaf grant missing %q: %s", want, grant)
		}
	}
	revoke := strings.Join(LeafRevokeCmd("a-local-agent", dir).Args, " ")
	for _, want := range []string{
		"setfacl -R -x u:'a-local-agent'",
		"setfacl -R -d -x u:'a-local-agent'",
	} {
		if !strings.Contains(revoke, want) {
			t.Errorf("Linux leaf revoke missing %q: %s", want, revoke)
		}
	}
}

// TestLinuxTraverseGrantIsExecuteOnly guards Layer-1 on Linux: the traverse grant
// opens execute-only (--x, pass-through, NOT read/list) on a single directory and
// is non-recursive/non-default, so it can't leak the ancestor's other children.
func TestLinuxTraverseGrantIsExecuteOnly(t *testing.T) {
	if runtime.GOOS == "darwin" {
		t.Skip("Linux-specific setfacl idiom")
	}
	grant := strings.Join(TraverseGrantCmd("a-local-agent", "/home/alice").Args, " ")
	if !strings.Contains(grant, "setfacl -m u:a-local-agent:--x") {
		t.Errorf("Linux traverse grant must be execute-only (--x): %s", grant)
	}
	// Non-recursive / non-default: a traverse grant must not carry -R or -d, or it
	// would open more than pass-through on the single ancestor.
	if strings.Contains(grant, " -R") || strings.Contains(grant, " -d") {
		t.Errorf("Linux traverse grant must be non-recursive and non-default: %s", grant)
	}
	revoke := strings.Join(TraverseRevokeCmd("a-local-agent", "/home/alice").Args, " ")
	if !strings.Contains(revoke, "setfacl -x u:a-local-agent") {
		t.Errorf("Linux traverse revoke must drop the named-user entry: %s", revoke)
	}
}

// TestLinuxCreateAccountUsesUseraddWithHome guards the Linux account recipe:
// `useradd -m -d <home> -s /bin/bash` creates the account and its home in one step
// (matching agentLaunchShell), then the operator grant lays down access+default
// ACLs. The home must be the /opt default, not a human-home path.
func TestLinuxCreateAccountUsesUseraddWithHome(t *testing.T) {
	if runtime.GOOS == "darwin" {
		t.Skip("Linux-specific account recipe")
	}
	home := DefaultHomeDir("alice-local-agent")
	if !strings.HasPrefix(home, "/opt/") {
		t.Fatalf("Linux default agent home must live under /opt, got %q", home)
	}
	steps := CreateAccountCmds("alice", "alice-local-agent", home)
	create := strings.Join(steps[0].Cmd.Args, " ")
	for _, want := range []string{"useradd", "-m", "-d " + home, "-s /bin/bash"} {
		if !strings.Contains(create, want) {
			t.Errorf("Linux useradd step missing %q: %s", want, create)
		}
	}
}

// TestSharedBinPathsExcludesOperatorHome guards the core isolation invariant of
// the CLI-tool sharing feature: only world-traversable dirs OUTSIDE the
// operator's 700 home may be shared. A candidate under the operator home is
// unreachable by the agent, so it must never be added to the agent's PATH.
func TestSharedBinPathsExcludesOperatorHome(t *testing.T) {
	// Use the operator's real home as the guard boundary; every returned dir must
	// exist, be a directory, and sit outside that home.
	home := OperatorHome()
	for _, d := range SharedBinPaths(home) {
		if home != "" && IsUnderHome(home, d) {
			t.Errorf("SharedBinPaths returned %q under the operator home %q — the agent cannot traverse a 700 home", d, home)
		}
		info, err := os.Stat(d)
		if err != nil || !info.IsDir() {
			t.Errorf("SharedBinPaths returned non-existent/non-dir %q", d)
		}
	}

	// A candidate that lives under the given home is filtered even when it exists:
	// pretend the operator home is a parent of a real candidate dir and assert none
	// of that candidate's descendants come back.
	if runtime.GOOS == "darwin" {
		for _, d := range SharedBinPaths("/opt") {
			if IsUnderHome("/opt", d) {
				t.Errorf("SharedBinPaths(\"/opt\") leaked %q under the home boundary", d)
			}
		}
	}
}

// TestEnsureSharedBinsOnPathCmd guards the PATH-append builder: it no-ops on an
// empty dir list, and when given dirs it produces a sudo/agent-fronted, marker-
// guarded profile append that names each dir and appends AFTER $PATH (so an
// agent-owned tool still shadows the operator's copy).
func TestEnsureSharedBinsOnPathCmd(t *testing.T) {
	if EnsureSharedBinsOnPathCmd("alice-local-agent", nil) != nil {
		t.Error("expected nil command when there is nothing to share")
	}
	dirs := []string{"/opt/homebrew/bin", "/usr/local/bin"}
	c := EnsureSharedBinsOnPathCmd("alice-local-agent", dirs)
	if c == nil {
		t.Fatal("expected a command when dirs are given")
	}
	joined := strings.Join(c.Args, " ")
	if c.Args[0] != "sudo" {
		t.Errorf("expected sudo-fronted command (runs as the agent user): %v", c.Args)
	}
	if !strings.Contains(joined, "alice-local-agent") {
		t.Errorf("command does not name the agent user: %v", c.Args)
	}
	for _, d := range dirs {
		if !strings.Contains(joined, d) {
			t.Errorf("command does not name shared dir %q: %v", d, c.Args)
		}
	}
	// Appended after $PATH so agent-owned tools (in the prepended ~/.local/bin)
	// win over the operator's copies.
	if !strings.Contains(joined, `PATH="$PATH:`) {
		t.Errorf("shared dirs must be appended after $PATH, not prepended: %v", c.Args)
	}
	// Marker-guarded so re-running is a no-op.
	if !strings.Contains(joined, "marker=") || !strings.Contains(joined, "grep -qF") {
		t.Errorf("append must be marker-guarded for idempotency: %v", c.Args)
	}
}

func TestAncestorChain(t *testing.T) {
	home := "/Users/alice"
	got := AncestorChain(home, "/Users/alice/projects/api")
	want := []string{"/Users/alice", "/Users/alice/projects"}
	if len(got) != len(want) {
		t.Fatalf("AncestorChain = %v, want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("AncestorChain = %v, want %v", got, want)
		}
	}
	// A path outside the home has no chain.
	if c := AncestorChain(home, "/Users/Shared/x/work"); c != nil {
		t.Fatalf("expected nil chain for out-of-home path, got %v", c)
	}
	// The home itself (a grant at the home root) has just the home's parent? No —
	// leaf==home means the leaf is the home; chain walks from Dir(home).
	if c := AncestorChain(home, home); len(c) == 0 {
		t.Fatalf("expected non-empty chain for home leaf, got %v", c)
	}
}

func TestDetectProvider(t *testing.T) {
	writeSettings := func(t *testing.T, env string) string {
		t.Helper()
		home := t.TempDir()
		dir := filepath.Join(home, ".claude")
		if err := os.MkdirAll(dir, 0o700); err != nil {
			t.Fatal(err)
		}
		body := `{"env": {` + env + `}}`
		if err := os.WriteFile(filepath.Join(dir, "settings.json"), []byte(body), 0o600); err != nil {
			t.Fatal(err)
		}
		return home
	}

	t.Run("bedrock -> aws with ~/.aws", func(t *testing.T) {
		home := writeSettings(t, `"CLAUDE_CODE_USE_BEDROCK": "1"`)
		pc := DetectProvider(home)
		if pc.Name != "aws" || len(pc.ConfigPaths) != 1 || pc.ConfigPaths[0] != "~/.aws" {
			t.Fatalf("DetectProvider = %+v", pc)
		}
	})

	t.Run("vertex -> gcloud plus explicit creds", func(t *testing.T) {
		home := writeSettings(t, `"CLAUDE_CODE_USE_VERTEX": "true", "GOOGLE_APPLICATION_CREDENTIALS": "/tmp/key.json"`)
		pc := DetectProvider(home)
		if pc.Name != "vertex" {
			t.Fatalf("DetectProvider = %+v", pc)
		}
		if len(pc.ConfigPaths) != 2 || pc.ConfigPaths[0] != "~/.config/gcloud" || pc.ConfigPaths[1] != "/tmp/key.json" {
			t.Fatalf("vertex paths = %v", pc.ConfigPaths)
		}
	})

	t.Run("disabled flag falls through to anthropic", func(t *testing.T) {
		home := writeSettings(t, `"CLAUDE_CODE_USE_BEDROCK": "0"`)
		pc := DetectProvider(home)
		if pc.Name != "anthropic" || len(pc.ConfigPaths) != 0 {
			t.Fatalf("DetectProvider = %+v", pc)
		}
	})

	t.Run("missing settings -> anthropic default", func(t *testing.T) {
		pc := DetectProvider(t.TempDir())
		if pc.Name != "anthropic" || len(pc.ConfigPaths) != 0 {
			t.Fatalf("DetectProvider = %+v", pc)
		}
	})
}

func TestProviderConfigPaths(t *testing.T) {
	home := t.TempDir()
	if err := os.MkdirAll(filepath.Join(home, ".aws"), 0o700); err != nil {
		t.Fatal(err)
	}
	got := ProviderConfigPaths(home, ProviderConfig{Name: "aws", ConfigPaths: []string{"~/.aws", "~/.config/gcloud"}})
	if len(got) != 1 || got[0] != filepath.Join(home, ".aws") {
		t.Fatalf("ProviderConfigPaths = %v (only the existing path should be returned)", got)
	}
}

func TestSafeSeedSourcesRejectsSourcesEscapingHome(t *testing.T) {
	home := t.TempDir()
	// A real config dir under the home — safe.
	awsDir := filepath.Join(home, ".aws")
	if err := os.MkdirAll(awsDir, 0o700); err != nil {
		t.Fatal(err)
	}
	// A symlink under the home that points OUTSIDE it — unsafe: EvalSymlinks
	// resolves it to the outside target, which is not under the home.
	outside := t.TempDir()
	escaping := filepath.Join(home, ".config")
	if err := os.Symlink(outside, escaping); err != nil {
		t.Fatal(err)
	}
	// An absolute path fully outside the home (e.g. GOOGLE_APPLICATION_CREDENTIALS
	// pointing at /etc) — unsafe.
	extern := filepath.Join(outside, "key.json")
	if err := os.WriteFile(extern, []byte("k"), 0o600); err != nil {
		t.Fatal(err)
	}

	safe, skipped := SafeSeedSources(home, []string{awsDir, escaping, extern})
	if len(safe) != 1 || safe[0] != awsDir {
		t.Fatalf("safe = %v, want only %q", safe, awsDir)
	}
	if len(skipped) != 2 {
		t.Fatalf("skipped = %v, want the escaping symlink and the external path", skipped)
	}
}

func TestSafeSeedSourcesRefusesAllWhenHomeUnresolvable(t *testing.T) {
	// A home that doesn't exist can't establish the boundary, so nothing is safe.
	safe, skipped := SafeSeedSources(filepath.Join(t.TempDir(), "gone"), []string{"/tmp/x"})
	if len(safe) != 0 || len(skipped) != 1 {
		t.Fatalf("safe=%v skipped=%v, want everything skipped", safe, skipped)
	}
}

func TestCopyConfigCmdDoesNotDereferenceSymlinks(t *testing.T) {
	joined := strings.Join(CopyConfigCmd("agent", "/opt/agent", "/Users/alice", []string{"/Users/alice/.aws"}).Args, " ")
	// cp must copy symlinks as links (-P), and chown must re-own the link, not
	// its target (-h) — otherwise a link nested in the tree re-owns /etc/shadow.
	if !strings.Contains(joined, "cp -RP ") {
		t.Errorf("copy must use `cp -RP` (no symlink deref): %s", joined)
	}
	if !strings.Contains(joined, "chown -Rh ") {
		t.Errorf("chown must use `chown -Rh` (no symlink deref): %s", joined)
	}
}

func TestVerifyManagedHome(t *testing.T) {
	// A recorded home outside the managed root is refused before any account
	// lookup — this is a real human home, never a jentic-managed one.
	if err := VerifyManagedHome("root", "/home/alice"); err == nil {
		t.Error("expected a non-managed recorded home to be refused")
	}
	// A managed-looking home for an account that does not exist is refused
	// (lookup fails), so reset/reuse never proceed against a phantom account.
	if err := VerifyManagedHome("nope-no-such-agent-xyz", AgentHomeRoot()+"/nope-no-such-agent-xyz"); err == nil {
		t.Error("expected a missing account to be refused")
	}
	// An EXISTING account whose live home differs from the recorded managed home
	// is refused — the name has collided with a different account. We use the
	// current user (which exists but whose home is NOT the managed path).
	me, err := user.Current()
	if err != nil {
		t.Skip("cannot resolve current user")
	}
	if err := VerifyManagedHome(me.Username, AgentHomeRoot()+"/"+me.Username); err == nil {
		t.Error("expected a home mismatch against an existing account to be refused")
	}
}

func TestIsUnderHome(t *testing.T) {
	home := "/Users/alice"
	if !IsUnderHome(home, "/Users/alice/projects/api") {
		t.Error("expected in-home path to be under home")
	}
	if !IsUnderHome(home, home) {
		t.Error("expected home itself to be under home")
	}
	if IsUnderHome(home, "/Users/Shared/x") {
		t.Error("did not expect shared path to be under home")
	}
	if IsUnderHome(home, "/Users/alice-other") {
		t.Error("did not expect sibling-prefix path to be under home")
	}
}

// TestTrustedWorkspaces checks that only TRUSTED projects from the agent's own
// config are returned, that the strict permission model still takes precedence
// (banned paths dropped), that vanished paths are dropped, and that the result is
// deduped.
func TestTrustedWorkspaces(t *testing.T) {
	// Root the fixture under the real home, not t.TempDir(): on macOS the latter is
	// under /var, which Classify HardBans as a system tree, so every candidate would
	// be dropped. A real operator home (/Users/x, /home/x) is never under a system
	// tree, so this mirrors production while staying hermetic (removed on cleanup).
	home := workspaceTestRoot(t)

	mkdir := func(rel string) string {
		dir := filepath.Join(home, rel)
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
		return dir
	}

	trustedA := mkdir("code/api")
	trustedB := mkdir("src/web")
	untrusted := mkdir("code/scratch") // exists, but not trust-accepted → dropped
	bannedDir := mkdir(".ssh")         // trusted in config but a HardBan → dropped
	gonePath := filepath.Join(home, "code/deleted")
	// A trusted parent and a trusted child under it: the parent's recursive grant
	// already covers the child, so only the parent should be offered.
	nestedParent := mkdir("work")
	nestedChild := mkdir("work/repo")

	// A ~/.claude.json with a projects map: two trusted, one untrusted, one banned,
	// one trusted-but-missing-on-disk, and a trusted parent+child pair.
	claudeJSON := `{
      "projects": {
        "` + trustedA + `":     {"hasTrustDialogAccepted": true},
        "` + trustedB + `":     {"hasTrustDialogAccepted": true},
        "` + untrusted + `":    {"hasTrustDialogAccepted": false},
        "` + bannedDir + `":    {"hasTrustDialogAccepted": true},
        "` + gonePath + `":     {"hasTrustDialogAccepted": true},
        "` + nestedParent + `": {"hasTrustDialogAccepted": true},
        "` + nestedChild + `":  {"hasTrustDialogAccepted": true}
      }
    }`
	if err := os.WriteFile(filepath.Join(home, ".claude.json"), []byte(claudeJSON), 0o600); err != nil {
		t.Fatal(err)
	}

	desc, _ := Lookup("claude")
	got := TrustedWorkspaces(home, desc)
	set := map[string]bool{}
	for _, g := range got {
		set[g] = true
	}

	if !set[trustedA] || !set[trustedB] {
		t.Errorf("expected both trusted workspaces, got %v", got)
	}
	if !set[nestedParent] {
		t.Errorf("expected the trusted parent workspace, got %v", got)
	}
	for _, bad := range []string{untrusted, bannedDir, gonePath, nestedChild} {
		if set[bad] {
			t.Errorf("TrustedWorkspaces must not surface %s; got %v", bad, got)
		}
	}
	// trustedA, trustedB, nestedParent — the child collapses into the parent.
	if len(got) != 3 {
		t.Errorf("expected exactly 3 workspaces (child collapsed into parent), got %v", got)
	}
}

// TestTrustedWorkspacesEmptyOrUnknown guards the guards: no home, and an agent with
// no trusted-projects source wired up, both yield nil.
func TestTrustedWorkspacesEmptyOrUnknown(t *testing.T) {
	desc, _ := Lookup("claude")
	if got := TrustedWorkspaces("", desc); got != nil {
		t.Errorf("expected nil for empty home, got %v", got)
	}
	if got := TrustedWorkspaces(workspaceTestRoot(t), Descriptor{ID: "unknown-agent"}); got != nil {
		t.Errorf("expected nil for an agent with no trusted-projects source, got %v", got)
	}
}

// workspaceTestRoot returns a unique, self-cleaning directory under the real
// operator home, so paths look like production (not under /var, which Classify
// HardBans). It skips if the home isn't usable.
func workspaceTestRoot(t *testing.T) string {
	t.Helper()
	home := OperatorHome()
	if home == "" {
		t.Skip("no operator home available")
	}
	root, err := os.MkdirTemp(home, ".jentic-ws-test-")
	if err != nil {
		t.Skipf("cannot create fixture under home: %v", err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(root) })
	return root
}
