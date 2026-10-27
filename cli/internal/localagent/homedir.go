package localagent

import (
	"context"
	"fmt"
	"os/exec"
	"os/user"
	"path/filepath"
	"runtime"
	"strings"
)

// ReownHomeCmd changes ownership of the agent's home tree to the operator, so
// after the agent account is deleted the operator can still read the agent's
// work. It is the default `jentic reset` disposition for the home (preserve, not
// delete). The `-f` suppresses per-file errors on the SIP/TCC-protected template
// files a macOS home carries (Library/Mail, Library/Containers, …) that nobody
// can chown; the command still re-owns everything it can, and reset treats it as
// best-effort so those unavoidable entries don't abort the teardown. The `-h`
// (no-dereference) is a security boundary, not a nicety: the tree is agent-owned,
// so the agent could plant a symlink to a file OUTSIDE it (e.g. /etc/passwd) and a
// dereferencing recursive chown would re-own that target to the operator. With
// `-h` chown acts on the link itself, never its target. For the same reason
// the walk skips non-directories with more than one hard link (see
// chownWalkFilter). Runs as root.
func ReownHomeCmd(operator, homeDir string) *exec.Cmd {
	return recursiveChownCmd(operator, homeDir, true)
}

// ReclaimAgentHomeCmd (re-)establishes the agent as the owner of its whole home
// tree. It is run when setting up the agent account, and matters most when the
// home ALREADY EXISTS: a prior `jentic reset` that kept the home re-owned it to
// the operator (ReownHomeCmd), and `createhomedir` only creates missing files —
// it never reclaims ownership of existing content. Without this, a re-run of setup
// over that home leaves .claude/.aws/etc. operator-owned, so the agent can read
// but not WRITE them (fresh-config screens, provider token-cache failures,
// EACCES transcript writes). It mirrors ReownHomeCmd's `-Rf`: a macOS home carries
// SIP/TCC-protected template files nobody can chown, so it re-owns everything it
// can and the caller treats the residual non-zero exit as best-effort. Extended
// ACLs (the operator's inherited read/write grant) survive chown on both macOS and
// Linux, so this doesn't cost the operator access. The `-h` (no-dereference)
// matches ReownHomeCmd: an existing agent-owned home can contain agent-planted
// symlinks, and a dereferencing recursive chown would re-own their targets outside
// the tree; multiply hard-linked non-directories are skipped likewise
// (chownWalkFilter). Runs as root.
func ReclaimAgentHomeCmd(agentUser, homeDir string) *exec.Cmd {
	return recursiveChownCmd(agentUser, homeDir, true)
}

// ChownToAgentCmd gives the agent ownership of dir (recursively). It is used after
// the operator writes the agent's jentic identity into the agent's ~/.jentic:
// files the operator creates there are operator-owned, but the agent's 0600 key
// and tokens must be readable by the agent when it later runs as itself, so we
// hand the whole config dir to the agent. The `-h` (no-dereference) keeps the
// recursive chown from following a symlink out of the config dir and re-owning its
// target, and multiply hard-linked files are skipped — the same boundary
// ReownHomeCmd/ReclaimAgentHomeCmd hold. Runs as root.
func ChownToAgentCmd(agentUser, dir string) *exec.Cmd {
	return recursiveChownCmd(agentUser, dir, false)
}

// DeleteHomeCmd permanently removes the agent's home tree. `jentic reset` runs it
// ONLY when the operator has separately and explicitly accepted home deletion
// (the second runtime confirmation, or --delete-home paired with --force) — never
// by default. Runs as root.
func DeleteHomeCmd(homeDir string) *exec.Cmd {
	return exec.Command("sudo", "rm", "-rf", homeDir) //nolint:gosec // homeDir is a resolved, config-recorded path; deletion is explicitly confirmed by the caller.
}

// RemoveAgentIdentityCmd permanently removes the agent's own jentic identity
// dirs (see AgentIdentityDirs: the exported XDG config/state trees plus the
// legacy ~/.jentic — registration, tokens, and signing key). `jentic reset`
// runs it even when the agent's home is KEPT, so a later `jentic setup`
// that reuses the same home can't resurrect a torn-down (now-archived) agent
// registration, and no credential material outlives the account in the
// re-owned home. `rm -f` makes absent dirs a no-op. Runs as root because the
// dirs are owned by the agent account (and are settled before the home
// re-own/delete step). Returns nil when there is nothing to remove. `--` ends
// option parsing so a path can never be mistaken for an rm flag.
func RemoveAgentIdentityCmd(dirs []string) *exec.Cmd {
	if len(dirs) == 0 {
		return nil
	}
	args := append([]string{"rm", "-rf", "--"}, dirs...)
	return exec.Command("sudo", args...) //nolint:gosec // dirs are fixed joins under the validated, config-recorded agent home.
}

// RemoveAgentProfileCmd deletes a single profile directory (key, tokens, metadata)
// from the shared agent account's home — <configDir>/profiles/<name>. It is the
// per-profile counterpart of RemoveAgentIdentityCmd, used by `jentic reset
// <profile>` when the named profile is agent-owned: the files are owned by the
// agent uid, so removal is privileged. The account itself, its grants, and its
// other profiles are untouched.
func RemoveAgentProfileCmd(configDir, name string) *exec.Cmd {
	dir := filepath.Join(configDir, "profiles", name)
	return exec.Command("sudo", "rm", "-rf", dir) //nolint:gosec // configDir is config-recorded; name is a discovered profile dir name.
}

// IsUnderHome reports whether dir is the operator's home or a descendant of it.
func IsUnderHome(home, dir string) bool {
	home = filepath.Clean(home)
	dir = filepath.Clean(dir)
	if home == "" {
		return false
	}
	return dir == home || strings.HasPrefix(dir, home+string(filepath.Separator))
}

// LookupHomeDir resolves the agent account's home directory from the OS account
// database (os/user), returning an error if the account does not exist. This is
// the authoritative home. Resolving in Go means the account name is never
// interpreted by a shell — a shell expansion like `$(eval echo ~<user>)` would
// splice the name into a command line where a crafted name could escape into
// arbitrary shell.
func LookupHomeDir(agentUser string) (string, error) {
	u, err := user.Lookup(agentUser)
	if err != nil {
		return "", fmt.Errorf("resolve home of agent user %q: %w", agentUser, err)
	}
	if u.HomeDir == "" {
		return "", fmt.Errorf("agent user %q has no home directory recorded", agentUser)
	}
	return u.HomeDir, nil
}

// VerifyManagedHome confirms that the OS account agentUser is genuinely a
// jentic-managed agent account whose home is exactly recordedHome. It requires
// recordedHome to be a valid managed home (a strict descendant of AgentHomeRoot)
// AND the account's LIVE home in the OS database to equal it. This is the guard in
// front of every privileged reclaim/reown/delete of the home: reusing or tearing
// down an account name that happens to collide with a pre-existing human or system
// account (whose home is a real person's home, not /opt/<name> or
// /Users/Shared/<name>), or acting on a hand-edited config whose home_dir has
// drifted from the account's real home, would otherwise let root chown or rm the
// wrong directory. Fails closed: any mismatch, missing account, or invalid path is
// an error, so the caller touches nothing.
func VerifyManagedHome(agentUser, recordedHome string) error {
	if err := ValidateHomeDir(recordedHome); err != nil {
		return err
	}
	actual, err := LookupHomeDir(agentUser)
	if err != nil {
		return err
	}
	if filepath.Clean(actual) != recordedHome {
		return fmt.Errorf("agent account %q has home %q, not the expected managed home %q — "+
			"refusing to touch it (it may be a pre-existing account, not the jentic-managed one)",
			agentUser, actual, recordedHome)
	}
	return nil
}

// HomeClaimedBy returns the name of an existing OS account, OTHER than agentUser,
// whose recorded home directory is exactly homeDir — or "" when no other account
// claims it. It is the guard in front of CREATING an account: the account-setup
// form prefixes the home from the DEFAULT name, so an operator who edits the name
// but not the home (or hand-edits config) can point a brand-new account at an
// EXISTING agent account's live home — and the create path (unlike reuse, which
// goes through VerifyManagedHome) would then stamp operator ACLs over that home
// and chown it wholesale to the new account. Refusing when another account claims
// the home closes that.
//
// Enumeration shells to the platform account database (`dscl . -list /Users
// NFSHomeDirectory` on macOS, `getent passwd` on Linux). It is best-effort in the
// safe direction for a pure lookup: an enumeration failure returns "" (no claim
// found) rather than blocking setup, because the post-create UserExists
// verification still backstops a create that the OS refused.
func HomeClaimedBy(ctx context.Context, agentUser, homeDir string) string {
	target := filepath.Clean(homeDir)
	for name, home := range accountHomes(ctx) {
		if name != agentUser && filepath.Clean(home) == target {
			return name
		}
	}
	return ""
}

// accountHomes enumerates the OS account database as a name→home map, returning
// nil on any failure (see HomeClaimedBy for why that is the safe direction).
func accountHomes(ctx context.Context) map[string]string {
	out := map[string]string{}
	if runtime.GOOS == "darwin" {
		data, err := exec.CommandContext(ctx, "dscl", ".", "-list", "/Users", "NFSHomeDirectory").Output()
		if err != nil {
			return nil
		}
		// Each line is "<name><spaces><home>"; the home may itself contain
		// spaces, so split off the FIRST field as the name and rejoin the rest
		// as the home. This is robust to variable dscl column spacing and to a
		// name that happens to be a prefix of its home path (a plain TrimPrefix
		// would mangle those).
		for _, line := range strings.Split(string(data), "\n") {
			fields := strings.Fields(line)
			if len(fields) < 2 {
				continue
			}
			out[fields[0]] = strings.Join(fields[1:], " ")
		}
		return out
	}
	data, err := exec.CommandContext(ctx, "getent", "passwd").Output()
	if err != nil {
		return nil
	}
	for _, line := range strings.Split(string(data), "\n") {
		parts := strings.Split(line, ":")
		if len(parts) >= 6 && parts[0] != "" {
			out[parts[0]] = parts[5]
		}
	}
	return out
}
