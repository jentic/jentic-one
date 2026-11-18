package localagent

import (
	"path/filepath"
	"runtime"
	"slices"
	"strings"
	"testing"
)

// TestTeardownCmdShape asserts every privileged teardown step reset runs is
// sudo-fronted and names the account and/or target it acts on, so a step can
// never silently no-op against the wrong thing.
func TestTeardownCmdShape(t *testing.T) {
	home := filepath.Clean("/Users/alice")
	homeDir := "/Users/Shared/alice-local-agent"
	dir := filepath.Clean("/Users/alice/work")
	cases := []struct {
		name       string
		args       []string
		wantTarget string // "" = don't assert a path, just the user
		wantUser   bool
	}{
		{"leaf-revoke", LeafRevokeCmd("alice-local-agent", dir).Args, dir, true},
		{"traverse-revoke", TraverseRevokeCmd("alice-local-agent", home).Args, home, true},
		{"reown-home", ReownHomeCmd("alice", homeDir).Args, homeDir, false},
		{"delete-home", DeleteAgentHomeCmd(homeDir).Args, homeDir, false},
		{"remove-identity", RemoveAsAgentCmd("alice-local-agent", AgentIdentityDirs(homeDir)).Args, homeDir + "/.jentic", true},
		{"remove-sudoers", RemoveAgentSudoersCmd("alice-local-agent").Args, "", true},
		{"delete-account", DeleteAgentAccountCmd("alice-local-agent").Args, "", true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if tc.args[0] != "/usr/bin/sudo" {
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

	// ReownHomeCmd walks with find and execs `chown -fh` from each entry's
	// parent directory: -f suppresses per-file errors on SIP/TCC-protected
	// macOS template files, -h acts on links themselves, and -execdir avoids
	// resolving an entry through its full path a second time.
	chownPath := "/bin/chown"
	if runtime.GOOS == "darwin" {
		chownPath = "/usr/sbin/chown"
	}
	reownCmd := ReownHomeCmd("alice", homeDir)
	reown := strings.Join(reownCmd.Args, " ")
	if !strings.Contains(reown, "/usr/bin/find "+homeDir) || !strings.Contains(reown, "-execdir "+chownPath+" -fh alice ") {
		t.Errorf("re-own must use absolute find/chown with execdir and no-dereference: %s", reown)
	}
	assertFixedUtilityPATH(t, reownCmd)
	if got, want := DeleteAgentHomeCmd(homeDir).Args, []string{"/usr/bin/sudo", "/bin/rm", "-rf", "--", homeDir}; !slices.Equal(got, want) {
		t.Errorf("delete-home = %v, want %v", got, want)
	}
	assertFixedUtilityPATH(t, DeleteAgentHomeCmd(homeDir))

	// DeleteAgentAccountCmd must keep the home (it is settled separately): macOS
	// deletes the DirectoryService record with `dscl . -delete` (no filesystem
	// side effect) rather than `sysadminctl -deleteUser`, whose `-keepHome` is
	// rejected at runtime on recent macOS; Linux uses `userdel` without -r.
	del := strings.Join(DeleteAgentAccountCmd("alice-local-agent").Args, " ")
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

// TestRevokeCmdsMatchInstalledACEs pins the exact ACE spellings the revokes
// remove: macOS `chmod -a` only drops an entry whose permission string matches
// it exactly, and Linux must drop both the access and the default entries.
func TestRevokeCmdsMatchInstalledACEs(t *testing.T) {
	dir := "/home/alice/projects/api"
	leaf := strings.Join(LeafRevokeCmd("a-local-agent", dir).Args, " ")
	traverse := strings.Join(TraverseRevokeCmd("a-local-agent", "/home/alice").Args, " ")
	if runtime.GOOS == "darwin" {
		if !strings.Contains(leaf, "user:a-local-agent allow "+macLeafACE) {
			t.Errorf("macOS leaf revoke must name the exact leaf ACE: %s", leaf)
		}
		if !strings.Contains(leaf, "! -type l") {
			t.Errorf("macOS leaf revoke must not follow symlinks: %s", leaf)
		}
		if !strings.Contains(traverse, "allow execute") {
			t.Errorf("macOS traverse revoke must name `allow execute`: %s", traverse)
		}
		return
	}
	for _, want := range []string{
		"setfacl -R -x u:'a-local-agent'",
		"setfacl -R -d -x u:'a-local-agent'",
	} {
		if !strings.Contains(leaf, want) {
			t.Errorf("Linux leaf revoke missing %q: %s", want, leaf)
		}
	}
	if !strings.Contains(traverse, "setfacl -x u:a-local-agent /home/alice") {
		t.Errorf("Linux traverse revoke must drop the named-user entry: %s", traverse)
	}
}

// TestRemoveSudoersAnchorsOnRunasSpec pins that the shared drop-in's line
// filter is the parenthesised runas spec, never the bare name — a bare-name
// filter would also strike a prefix sibling's line (`alice-local-agent-2`).
func TestRemoveSudoersAnchorsOnRunasSpec(t *testing.T) {
	remove := strings.Join(RemoveSudoersCmd("alice-local-agent").Args, " ")
	if !strings.Contains(remove, "(alice-local-agent)") {
		t.Fatalf("removal must filter on the parenthesised runas spec: %s", remove)
	}
	if strings.Contains(remove, " alice-local-agent ") || strings.Contains(remove, "'alice-local-agent'") {
		t.Errorf("removal must not filter on the bare name (would hit prefix siblings): %s", remove)
	}
}

// TestValidateHomeDirAcceptsManagedHomes pins that the homes older releases
// created (<AgentHomeRoot>/<user>) pass validation, so reset can tear them down.
func TestValidateHomeDirAcceptsManagedHomes(t *testing.T) {
	for _, h := range []string{
		filepath.Join(AgentHomeRoot(), "alice-local-agent"),
		ServiceHomeDir("_jentic-cursor"),
	} {
		if err := ValidateHomeDir(h); err != nil {
			t.Errorf("ValidateHomeDir(%q) = %v, want nil", h, err)
		}
	}
}
