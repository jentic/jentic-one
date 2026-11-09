// Package localagent holds the OS-level teardown primitives for agent accounts
// provisioned by older jentic releases: the dedicated `<operator>-local-agent`
// Unix user (its named-user directory ACLs, passwordless-launch sudoers line,
// and home) and the per-runtime `_jentic-<runtime>` MCP service accounts plus
// the root-owned jentic copy they ran. Nothing in this CLI creates any of
// these any more; `jentic reset` is the only caller, and it uses these
// helpers to remove what an existing machine still carries.
//
// The package is stdlib-only and returns `*exec.Cmd` values and pure data;
// the command layer (internal/cli/api/reset*.go) decides when to run them.
// Every operator-editable value is constrained by validate.go before it
// reaches a privileged command.
package localagent

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"os/user"
	"path/filepath"
	"runtime"
	"strings"
)

// DefaultUserName is the agent account name older releases provisioned for an
// operator ("<operator>-local-agent"); reset falls back to it when a record
// carries no explicit user.
func DefaultUserName(operator string) string { return operator + "-local-agent" }

// UserExists reports whether an OS account with the given name exists. It shells
// to `id -u <user>`, which is portable across macOS and Linux.
func UserExists(ctx context.Context, user string) bool {
	return exec.CommandContext(ctx, "id", "-u", user).Run() == nil //nolint:gosec // user is a config-derived account name.
}

// AgentConfigDir returns the agent's own legacy V1 jentic config directory
// (~/.jentic inside the agent's home), one of the AgentIdentityDirs reset clears.
func AgentConfigDir(homeDir string) string {
	return filepath.Join(homeDir, ".jentic")
}

// AgentIdentityDirs returns every agent-home location that holds the agent's
// own jentic identity — the registration, tokens, and Ed25519 signing key:
//
//   - <home>/.config/jentic       (exported context: minimal config + key)
//   - <home>/.local/state/jentic  (exported context: tokens)
//   - <home>/.jentic              (legacy V1 identity/profiles)
//
// `jentic reset` removes all of them even when the home is KEPT, so credential
// material handed to the agent never survives a teardown in the re-owned home. Every path is a fixed join under the (validated)
// home, so the list is safe to hand to a privileged rm.
func AgentIdentityDirs(homeDir string) []string {
	return []string{
		filepath.Join(homeDir, ".config", "jentic"),
		filepath.Join(homeDir, ".local", "state", "jentic"),
		AgentConfigDir(homeDir),
	}
}

// AccountStep is one privileged account-teardown step, paired with a human
// description for progress output and error wrapping. Callers run the steps in order and stop on the
// first failure unless the step is BestEffort.
type AccountStep struct {
	// What describes the step for progress/error messages ("delete the account").
	What string
	// Cmd is the command to run; it is sudo-fronted where elevation is required.
	Cmd *exec.Cmd
	// BestEffort marks a step whose non-zero exit should be reported but not abort
	// the run. Used by reset for re-owning/deleting the agent home: a macOS home
	// materialised by createhomedir can contain SIP/TCC-protected template files (e.g.
	// Library/Mail, Library/Containers) that NOBODY — not even root — can chown or
	// remove, so the operation processes everything it can and then exits non-zero
	// on those. That is expected and harmless (the agent's actual work is re-owned),
	// so it must not stop the teardown before the account is deleted.
	BestEffort bool
}

// chownWalkFilter is the find(1) predicate recursive chowns walk with. They DO
// visit symlinks (`chown -h` re-owns the link itself, never its target), but
// skip non-directories with more than one hard link: the tree is agent-owned,
// so the agent could hard-link a file from elsewhere on the same filesystem
// into it, and a chown of that inode would re-own the original file wherever
// it lives. Directories cannot be hard-linked by an unprivileged user.
var chownWalkFilter = []string{"(", "-type", "d", "-o", "-links", "1", ")"}

// recursiveChownCmd re-owns dir's tree to owner without following symlinks
// (-h, find's default -P walk) and without touching multiply hard-linked
// non-directories (chownWalkFilter). force adds -f for callers that expect
// unchangeable entries (macOS SIP/TCC template files). Runs as root.
func recursiveChownCmd(owner, dir string, force bool) *exec.Cmd {
	flags := "-h"
	if force {
		flags = "-fh"
	}
	args := append([]string{"find", dir}, chownWalkFilter...)
	args = append(args, "-exec", "chown", flags, owner, "{}", "+")
	return exec.Command("sudo", args...) //nolint:gosec // owner is a config account name; dir is a resolved path.
}

// AncestorChain returns the directories an older release stamped with an
// execute-only traverse ACL so the agent could reach leaf from home: home
// first, then each intermediate directory down to (but not including) leaf.
// Returns nil if leaf is not under home. reset walks it to find the traverse
// grants to revoke.
func AncestorChain(home, leaf string) []string {
	home = filepath.Clean(home)
	leaf = filepath.Clean(leaf)
	if !IsUnderHome(home, leaf) {
		return nil
	}
	var chain []string
	for d := filepath.Dir(leaf); ; d = filepath.Dir(d) {
		chain = append(chain, d)
		if d == home || d == filepath.Dir(d) {
			break
		}
	}
	// Reverse to home-first order.
	for i, j := 0, len(chain)-1; i < j; i, j = i+1, j-1 {
		chain[i], chain[j] = chain[j], chain[i]
	}
	return chain
}

// macLeafACE is the exact macOS ACL permission set older releases granted on a
// read/write leaf directory. macOS `chmod -a` removes an ACE only when the
// permission string matches it exactly, so the revoke must spell out the same
// set.
const macLeafACE = "list,add_file,add_subdirectory,search,delete,delete_child," +
	"read,write,execute,append,readattr,writeattr,readextattr,writeextattr," +
	"readsecurity,file_inherit,directory_inherit"

// LeafRevokeCmd removes the agent's read/write leaf allow from dir and its
// subtree (never following symlinks). On Linux it drops both the access and the
// default (inherited) named-user entries.
func LeafRevokeCmd(agentUser, dir string) *exec.Cmd {
	if runtime.GOOS == "darwin" {
		return exec.Command("sudo", "find", dir, "!", "-type", "l", //nolint:gosec // agentUser is a config account name; dir is a resolved path.
			"-exec", "chmod", "-a", "user:"+agentUser+" allow "+macLeafACE, "{}", "+")
	}
	script := "setfacl -R -x u:" + shellQuote(agentUser) + " " + shellQuote(dir) +
		" && setfacl -R -d -x u:" + shellQuote(agentUser) + " " + shellQuote(dir)
	return exec.Command("sudo", "sh", "-c", script) //nolint:gosec // agentUser is a config account name; dir is a resolved path.
}

// TraverseRevokeCmd removes the agent's execute-only traverse ACL from a single
// directory (non-recursive). reset runs it for every ancestor in AncestorChain.
func TraverseRevokeCmd(agentUser, dir string) *exec.Cmd {
	if runtime.GOOS == "darwin" {
		return exec.Command("sudo", "chmod", "-a", //nolint:gosec // agentUser is a config account name; dir is a resolved path.
			"user:"+agentUser+" allow execute", dir)
	}
	// -x removes the named-user entry entirely; an ancestor only ever carried the
	// execute-only traverse ACE, so nothing else is lost.
	return exec.Command("sudo", "setfacl", "-x", "u:"+agentUser, dir) //nolint:gosec // agentUser is a config account name; dir is a resolved path.
}

// AgentACLPresent reports whether dir currently carries any ACL entry for the
// agent user. `jentic reset` uses it to (a) show a truthful teardown plan —
// listing only the grants that actually exist on disk and flagging config
// entries whose ACL has already drifted away — and (b) skip revoking an ACE that
// is already gone (so macOS `chmod -a` never errors on a missing entry). It runs
// as whatever user invokes reset (root), reading the ACL, never modifying it.
func AgentACLPresent(ctx context.Context, agentUser, dir string) bool {
	var cmd *exec.Cmd
	if runtime.GOOS == "darwin" {
		cmd = exec.CommandContext(ctx, "ls", "-lde", dir) //nolint:gosec // dir is a config-recorded path.
	} else {
		cmd = exec.CommandContext(ctx, "getfacl", "-pc", dir) //nolint:gosec // dir is a config-recorded path.
	}
	out, err := cmd.Output()
	if err != nil {
		return false
	}
	return strings.Contains(string(out), aclUserNeedle(agentUser))
}

// aclUserNeedle is the substring that appears in an ACL listing IFF it carries an
// entry for exactly agentUser. It includes the delimiter that terminates the name
// in each tool's output so a prefix can't false-match: macOS `ls -lde` prints
// "N: user:<name> allow …" (space after the name), Linux `getfacl` prints
// "user:<name>:<perms>" and "default:user:<name>:<perms>" (colon after the name).
// Without the terminator, probing "alice-local-agent" would match an ACE for
// "alice-local-agent-2" and report access that doesn't exist for this user.
func aclUserNeedle(agentUser string) string {
	if runtime.GOOS == "darwin" {
		return "user:" + agentUser + " "
	}
	return "user:" + agentUser + ":"
}

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
// runs it even when the agent's home is KEPT, so no credential material
// outlives the account in the re-owned home. `rm -f` makes absent dirs a no-op. Runs as root because the
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

// sudoersPath is the shared drop-in older releases installed for the agents'
// passwordless-launch rules (and the MCP service accounts' argv-pinned lines),
// one line per account. It is only ever edited through `visudo -cf`-validated
// writes so a malformed edit can never brick sudo.
const sudoersPath = "/etc/sudoers.d/jentic-agent"

// RemoveSudoersCmd drops the agent user's passwordless-launch lines from the
// shared /etc/sudoers.d/jentic-agent drop-in, deleting the file if it becomes
// empty. It edits through a temp file validated with `visudo -c` before install,
// so a malformed result can never brick sudo, and is a no-op when the file is
// absent (the passwordless drop-in is optional). Runs as root.
//
// The line filter is ANCHORED on the runas spec `(<agentUser>)` — the exact token
// every installed rule carries — rather than the bare name, because the drop-in is SHARED
// across every agent account and a bare-substring match would also delete a
// co-resident agent's rule whose name shares this one as a prefix (removing
// `alice-local-agent` must not wipe `alice-local-agent-2`). The parentheses of the
// runas spec bound the name on both sides, so only the exact account's line is
// dropped. `-F` keeps the pattern a fixed string.
func RemoveSudoersCmd(agentUser string) *exec.Cmd {
	q := shellQuote("(" + agentUser + ")")
	script := `f=` + shellQuote(sudoersPath) + `; [ -f "$f" ] || exit 0; ` +
		`tmp="$(mktemp)"; grep -vF ` + q + ` "$f" > "$tmp" || true; ` +
		`if [ -s "$tmp" ]; then ` +
		`  if visudo -cf "$tmp" >/dev/null 2>&1; then install -m 0440 "$tmp" "$f"; fi; ` +
		`else rm -f "$f"; fi; ` +
		`rm -f "$tmp"`
	return exec.Command("sudo", "sh", "-c", script) //nolint:gosec // agentUser is shell-quoted; the script edits a fixed sudoers path.
}

// DeleteAccountCmd deletes the agent's Unix account WITHOUT removing its home —
// the home is settled separately (re-owned or deleted) before this runs, so the
// account-delete must not touch it. On macOS we delete the DirectoryService
// record directly with `dscl . -delete /Users/<user>`: it removes only the
// account record and never touches the filesystem, so the home survives. We
// deliberately avoid `sysadminctl -deleteUser -keepHome` — `-keepHome` is listed
// in the man page but is rejected at runtime on recent macOS ("'-keepHome'
// options is not available on this system"), and `-deleteUser` without it would
// delete the home. Linux `userdel` (no -r) likewise leaves the home in place.
// Runs as root.
func DeleteAccountCmd(agentUser string) *exec.Cmd {
	if runtime.GOOS == "darwin" {
		return exec.Command("sudo", "dscl", ".", "-delete", "/Users/"+agentUser) //nolint:gosec // agentUser is a config account name.
	}
	return exec.Command("sudo", "userdel", agentUser) //nolint:gosec // agentUser is a config account name.
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

// seededConfigPaths are the tilde-relative agent and provider config paths
// older releases could seed from the operator's home into an agent home:
// the coding agents' own config trees plus the Bedrock/Vertex provider dirs.
var seededConfigPaths = []string{
	"~/.claude", "~/.claude.json", "~/.codex", "~/.cursor", "~/.hermes",
	"~/.aws", "~/.config/gcloud",
}

// SeededConfigDirs returns the absolute config/credential paths that config
// seeding could have copied INTO the agent's home (seededConfigPaths),
// expanded against agentHome and constrained to lie under it. `jentic reset` uses this to purge seeded operator credentials from a
// KEPT (re-owned) home, so a live API key the operator handed the agent doesn't
// outlive the account teardown. Deduplicated; order is stable for display.
func SeededConfigDirs(agentHome string) []string {
	cleanHome := filepath.Clean(agentHome)
	seen := map[string]bool{}
	var out []string
	add := func(rel string) {
		abs := expandTilde(rel, agentHome)
		// Strict descendant only: never let a "~" entry (which cleans to the home
		// itself) turn this into `rm -rf $HOME`. IsUnderHome accepts the home root,
		// so exclude it explicitly.
		if abs == "" || filepath.Clean(abs) == cleanHome || !IsUnderHome(cleanHome, abs) || seen[abs] {
			return
		}
		seen[abs] = true
		out = append(out, abs)
	}
	for _, p := range seededConfigPaths {
		add(p)
	}
	return out
}

// ScrubSeededConfigCmd removes the given absolute seeded-config paths from a kept
// agent home, run as root because the tree may still be agent-owned when this
// runs. Recursive (`rm -rf`) because these are directories, but every path is
// pre-constrained to the agent's home by SeededConfigDirs. Returns nil when there
// is nothing to scrub so the caller can skip the step cleanly.
func ScrubSeededConfigCmd(paths []string) *exec.Cmd {
	if len(paths) == 0 {
		return nil
	}
	// `--` ends option parsing so a path can never be mistaken for an rm flag
	// (belt-and-braces — every path here is an absolute home descendant).
	args := append([]string{"rm", "-rf", "--"}, paths...)
	return exec.Command("sudo", args...) //nolint:gosec // paths are fixed and home-constrained.
}

// expandTilde resolves a leading "~/" (or bare "~") in p against home.
func expandTilde(p, home string) string {
	if home == "" {
		return ""
	}
	if p == "~" {
		return home
	}
	if rest, ok := strings.CutPrefix(p, "~/"); ok {
		return filepath.Join(home, rest)
	}
	return p
}

// agentSystemPATH is the fixed PATH the privileged teardown scripts run with:
// the standard system binary directories, so their helpers never resolve
// through whatever PATH the invoking environment carried (sudo keeps the
// caller's PATH when sudoers sets no secure_path, as on macOS).
const agentSystemPATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

// fixedPATHPrefix pins PATH to agentSystemPATH at the top of a shell script.
const fixedPATHPrefix = "PATH=" + agentSystemPATH + "; export PATH; "

// shellQuote wraps s in single quotes, escaping any embedded single quotes, so
// it is safe to interpolate into a `bash -c` snippet.
func shellQuote(s string) string {
	return "'" + strings.ReplaceAll(s, "'", `'\''`) + "'"
}

// OperatorHome returns the operator's home directory (os.UserHomeDir), or "".
func OperatorHome() string {
	h, err := os.UserHomeDir()
	if err != nil {
		return ""
	}
	return h
}
