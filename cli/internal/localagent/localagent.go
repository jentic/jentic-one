// Package localagent holds the OS-level primitives behind `jentic run`: the
// known coding-agent descriptors, and the helpers that probe/grant/launch as a
// dedicated agent user. It is deliberately free of any cobra/config coupling so
// the command layer (internal/cmd) stays a thin orchestrator over these.
//
// The security model this implements is documented in
// docs/security/local-agent/local-agent-isolation.md: the agent runs as its own
// unprivileged Unix user, is granted access to individual working directories via
// inherited ACLs rather than by widening any human's home, and is launched under a
// per-session process-confinement profile (confine.go) that trims its view of the
// operator's home to just those grants.
package localagent

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
)

// Descriptor describes one known coding agent so adding an agent is data, not
// code. Keyed in Registry by the identifier the operator types (e.g. "claude").
type Descriptor struct {
	// ID is the identifier the operator types: `jentic run <ID>`.
	ID string
	// Binary is the executable name to probe with `command -v` and to exec.
	Binary string
	// ProbePaths are well-known install locations, used to tell "missing"
	// apart from "installed but not on PATH". Tilde is expanded per user.
	ProbePaths []string
	// Install is the documented fresh-install command, run as the agent user.
	Install string
	// SingleBinary reports whether the agent is a self-contained single file,
	// so copying the operator's binary is offered as the default provision route.
	SingleBinary bool
	// ConfigPaths are the agent's non-secret configuration files/dirs under the
	// operator's home (tilde-relative, e.g. "~/.claude.json"), which `jentic
	// run` can seed into the agent's home so the agent inherits the operator's
	// settings. They may include provider-specific credentials the operator has
	// stored locally — see CopyConfigCmd's caveat.
	ConfigPaths []string
}

// Registry is the set of known agents. New agents are added as rows here.
var Registry = map[string]Descriptor{
	"claude": {
		ID:           "claude",
		Binary:       "claude",
		ProbePaths:   []string{"~/.local/bin/claude"},
		Install:      "curl -fsSL https://claude.ai/install.sh | bash",
		SingleBinary: true,
		// Claude Code keeps its user settings in ~/.claude/ (settings, agents,
		// commands) and ~/.claude.json (the top-level config). Seeding these
		// gives the agent account the operator's Claude Code setup; the operator
		// still authenticates separately on first launch.
		ConfigPaths: []string{"~/.claude", "~/.claude.json"},
	},
}

// Known returns the sorted list of known agent identifiers, for error messages.
func Known() []string {
	ids := make([]string, 0, len(Registry))
	for id := range Registry {
		ids = append(ids, id)
	}
	// Small, stable ordering without importing sort for one call site.
	for i := 1; i < len(ids); i++ {
		for j := i; j > 0 && ids[j-1] > ids[j]; j-- {
			ids[j-1], ids[j] = ids[j], ids[j-1]
		}
	}
	return ids
}

// Lookup returns the descriptor for id and whether it is known.
func Lookup(id string) (Descriptor, bool) {
	d, ok := Registry[id]
	return d, ok
}

// DefaultUserName derives the single-user default agent account name for the
// current operator: "<operator>-local-agent". Matches doc 05's recipe.
func DefaultUserName(operator string) string { return operator + "-local-agent" }

// UserExists reports whether an OS account with the given name exists. It shells
// to `id -u <user>`, which is portable across macOS and Linux.
func UserExists(ctx context.Context, user string) bool {
	return exec.CommandContext(ctx, "id", "-u", user).Run() == nil //nolint:gosec // user is a config-derived account name.
}

// DefaultHomeDir returns the default home directory for a freshly-created agent
// account: a subdirectory of an existing shared parent that the operator can be
// granted into without touching any human's home. macOS uses /Users/Shared,
// Linux uses /opt — both are world-traversable roots owned by root, matching the
// setup recipe in docs/security/local-agent/local-agent-isolation.md.
func DefaultHomeDir(agentUser string) string {
	if runtime.GOOS == "darwin" {
		return "/Users/Shared/" + agentUser
	}
	return "/opt/" + agentUser
}

// AgentConfigDir returns the agent's own jentic config directory (~/.jentic
// inside the agent's home). This is the single source of truth for a self-user
// agent's platform identity — the operator's config only references it (see
// config.LocalAgent.ConfigDir). It matches the default JENTIC_HOME layout
// (<home>/.jentic) so the agent, running as itself, finds its identity with no
// extra configuration.
func AgentConfigDir(homeDir string) string {
	return filepath.Join(homeDir, ".jentic")
}

// AccountStep is one privileged step in creating the agent account, paired with
// a human description for progress output and error wrapping. Callers run the
// steps in order and stop on the first failure.
type AccountStep struct {
	// What describes the step for progress/error messages ("create the account").
	What string
	// Cmd is the command to run; it is sudo-fronted where elevation is required.
	Cmd *exec.Cmd
	// BestEffort marks a step whose non-zero exit should be reported but not abort
	// the run. Used by reset for re-owning/deleting the agent home: a macOS home
	// materialised by createhomedir contains SIP/TCC-protected template files (e.g.
	// Library/Mail, Library/Containers) that NOBODY — not even root — can chown or
	// remove, so the operation processes everything it can and then exits non-zero
	// on those. That is expected and harmless (the agent's actual work is re-owned),
	// so it must not stop the teardown before the account is deleted.
	BestEffort bool
}

// CreateAccountCmds returns the ordered, platform-specific steps that create the
// agent's Unix account, materialise its home, and grant the operator inherited
// read/write into that home — the privileged half of the setup recipe in
// docs/security/local-agent/local-agent-isolation.md. It does NOT touch the
// operator's own home: in-home confidentiality against the agent is enforced per
// session by the process-confinement layer (see confine.go), not by locking ~.
//
// macOS: `sysadminctl -addUser` provisions a password-less account and only
// *records* the home path, so `createhomedir -c -u` is needed to actually create
// the directory; the operator ACL is an inherited `chmod +a` allow. Linux:
// `useradd -m` creates the home in one step, then two `setfacl` calls lay down
// the operator's access ACL and a matching default ACL for future contents.
func CreateAccountCmds(operator, agentUser, homeDir string) []AccountStep {
	if runtime.GOOS == "darwin" {
		return []AccountStep{
			{
				What: "create the agent account",
				//nolint:gosec // operator/agentUser/homeDir are config-derived account names and a resolved path.
				Cmd: exec.Command("sudo", "sysadminctl", "-addUser", agentUser,
					"-fullName", operator+" Local Agent", "-home", homeDir, "-password", "-"),
			},
			{
				What: "create the agent's home directory",
				Cmd:  exec.Command("sudo", "createhomedir", "-c", "-u", agentUser), //nolint:gosec // agentUser is a config-derived account name.
			},
			{
				What: "grant the operator read/write into the agent's home",
				Cmd:  GrantOperatorHomeCmd(operator, homeDir),
			},
		}
	}
	return []AccountStep{
		{
			What: "create the agent account",
			Cmd:  exec.Command("sudo", "useradd", "-m", "-d", homeDir, "-s", "/bin/bash", agentUser), //nolint:gosec // agentUser is a config-derived account name; homeDir is a resolved path.
		},
		{
			What: "grant the operator read/write into the agent's home",
			Cmd:  GrantOperatorHomeCmd(operator, homeDir),
		},
	}
}

// GrantOperatorHomeCmd gives the operator RECURSIVE, inherited read/write on the
// agent's home, so the operator can seed config, write the agent's jentic identity
// (mkdir <home>/.jentic) before handing it over, AND later read the agent's own
// profiles back out of <home>/.jentic (which `jentic profile` enumerates). It is
// part of CreateAccountCmds and is ALSO re-applied when reusing an existing
// account, because the agent home may be owned by the agent (after reclaim) with
// only a stale, too-narrow operator ACL.
//
// The grant is recursive so profiles the AGENT writes after handover (owned by the
// agent uid, 0700) are still operator-readable, and inherited (file_inherit,
// directory_inherit in macLeafACE) so anything created later stays readable
// without a re-stamp. On macOS the recursion is driven by `find ! -type l` (see
// LeafGrantCmd) — chmod -R would follow symlinks to their targets and error on
// dangling links, and macOS chmod refuses -R with -h. The explicit macLeafACE
// permission set is used rather than the "read,write,execute" shorthand: on a
// *directory* that shorthand expands to only list,add_file,search — WITHOUT
// add_subdirectory — so the operator could create files but not directories, and
// `mkdir <home>/.jentic` would fail with EACCES. The grant is additive (duplicate/
// narrower ACEs are harmless — allow ACEs union), so re-applying on reuse simply
// widens access to the correct set. Runs as root.
func GrantOperatorHomeCmd(operator, homeDir string) *exec.Cmd {
	if runtime.GOOS == "darwin" {
		return exec.Command("sudo", "find", homeDir, "!", "-type", "l", //nolint:gosec // operator is the current login user; homeDir is a resolved path.
			"-exec", "chmod", "+a#", "0", "user:"+operator+" allow "+macLeafACE, "{}", "+")
	}
	setfacl := "setfacl -R -m u:" + shellQuote(operator) + ":rwX " + shellQuote(homeDir) +
		" && setfacl -R -d -m u:" + shellQuote(operator) + ":rwX " + shellQuote(homeDir)
	return exec.Command("sudo", "sh", "-c", setfacl) //nolint:gosec // operator/homeDir are config-derived, shell-quoted.
}

// BinaryStatus is the outcome of probing whether an agent's binary is runnable
// as the agent user.
type BinaryStatus int

const (
	// BinaryOnPath means `command -v <binary>` resolved as the agent user.
	BinaryOnPath BinaryStatus = iota
	// BinaryFoundOffPath means the binary exists at a known probe path but is
	// not on the agent's PATH (a PATH fix, not a reinstall).
	BinaryFoundOffPath
	// BinaryMissing means the binary is genuinely absent for the agent user.
	BinaryMissing
)

// ProbeBinary checks whether desc.Binary is runnable as the agent user, in a
// login shell so the probe sees exactly what the launch will. It distinguishes
// on-PATH, found-off-PATH, and missing so the caller can fix vs. reinstall.
func ProbeBinary(ctx context.Context, agentUser string, desc Descriptor) BinaryStatus {
	if runAsAgent(ctx, agentUser, "command -v "+shellQuote(desc.Binary)) == nil {
		return BinaryOnPath
	}
	for _, p := range desc.ProbePaths {
		// A leading "~" is expanded by the agent's login shell, so it resolves
		// to the agent's home — quote only the non-tilde remainder.
		if runAsAgent(ctx, agentUser, "test -x "+quoteProbePath(p)) == nil {
			return BinaryFoundOffPath
		}
	}
	return BinaryMissing
}

// DirAccess reports whether the agent user can read, write, and traverse dir.
func DirAccess(ctx context.Context, agentUser, dir string) bool {
	// -r -a -w -a -x: readable AND writable AND traversable, all as the agent.
	return runAsAgent(ctx, agentUser, "test -r "+shellQuote(dir)+" -a -w "+shellQuote(dir)+" -a -x "+shellQuote(dir)) == nil
}

// The directory-access model implemented below is "700 home + traverse-walk +
// rwx-leaf". It lets the agent be granted read/write to a specific directory
// *inside* the operator's home without gaining access to the rest of the home,
// and without ever walking or stamping the whole home tree. Two layers, both
// scoped to the single agent user (they never touch the operator's own access):
//
//	Layer 1 — traverse-walk (AncestorsNeedingTraverse + TraverseGrantCmd): an
//	  execute-only (search, not list/read) grant on the home and each ancestor
//	  down to the leaf's parent, so the agent can *pass through* to reach the
//	  leaf. Dirs it can already traverse are skipped.
//	Layer 2 — rwx-leaf (LeafGrantCmd): full read/write/execute on the workspace
//	  and everything created inside it (inherited).
//
// The default-deny is provided by the operator's home already being mode 0700
// (the machine-independent isolation guarantee from doc 05): with `~` at 0700
// the agent — like every other non-owner user — cannot even traverse it, so it
// reaches *nothing* inside until we open a specific path. We deliberately do
// NOT add a recursive agent-scoped `deny` ACL across `~`: an earlier design did,
// and it was a mistake — walking the whole home is slow, races against churning
// temp files, trips macOS TCC privacy prompts (e.g. Photos) when it descends
// into protected bundles, and on macOS the inherited deny fights the leaf allow
// on first-match ordering. Relying on the 0700 bits avoids all of that.
//
// Accepted residual (documented in doc 07): because Layer 1 grants execute on an
// ancestor, if some directory *inside* `~` is world-readable (mode o+r, e.g. a
// 0755 project dir) the agent could read it once it can traverse the path to it,
// without an explicit leaf grant. The narrow-traverse mitigation (open execute
// on only the exact ancestor chain, and optionally strip world bits on those
// ancestors) is discussed in the docs; we currently accept the gap rather than
// re-introduce a home-wide sweep.
//
// macOS ordering note: with no home-wide deny there is no inherited deny to beat,
// so leaf-allow ordering is not load-bearing. We still insert the leaf allow at a
// low index for robustness against any pre-existing deny ACEs on the subtree.

// AncestorChain returns the directories that must be traversable for the agent
// to reach leaf from home: home first, then each intermediate directory down to
// (but not including) leaf. Returns nil if leaf is not under home.
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

// AncestorsNeedingTraverse returns the subset of the home→leaf ancestor chain
// that the agent cannot already traverse, by probing `test -x` as the agent. In
// the common layout (home 700, nothing else granted) this is just the home and
// the untouched intermediate dirs; a re-run after an earlier grant skips the
// ones already opened.
func AncestorsNeedingTraverse(ctx context.Context, agentUser, home, leaf string) []string {
	var need []string
	for _, d := range AncestorChain(home, leaf) {
		if runAsAgent(ctx, agentUser, "test -x "+shellQuote(d)) != nil {
			need = append(need, d)
		}
	}
	return need
}

// TraverseGrantCmd returns the command that grants the agent execute-only
// (traverse/search — not list or read) on a single directory (Layer 1). It is
// non-recursive and non-inherited: it opens pass-through on exactly this dir.
func TraverseGrantCmd(agentUser, dir string) *exec.Cmd {
	if runtime.GOOS == "darwin" {
		return exec.Command("sudo", "chmod", "+a", //nolint:gosec // agentUser is a config account name; dir is a resolved path.
			"user:"+agentUser+" allow execute", dir)
	}
	return exec.Command("sudo", "setfacl", "-m", "u:"+agentUser+":--x", dir) //nolint:gosec // agentUser is a config account name; dir is a resolved path.
}

// macLeafACE is the macOS ACL permission set granted on the rwx-leaf. It must be
// spelled out explicitly rather than using the "read,write,execute" shorthand:
// on a *directory* macOS expands that shorthand to only `list,add_file,search`,
// which lets the agent create a file but NOT delete or rename one — so a common
// write-to-temp-then-rename (e.g. an editor or `Write` tool) fails with EACCES,
// and `test -w` on the dir returns false (which made `jentic run` re-prompt for
// the same directory on every launch). We therefore include the directory-
// mutation bits (add_subdirectory, delete, delete_child) and the file bits, all
// inheritable, so the leaf and everything created inside it is fully read/write.
const macLeafACE = "list,add_file,add_subdirectory,search,delete,delete_child," +
	"read,write,execute,append,readattr,writeattr,readextattr,writeextattr," +
	"readsecurity,file_inherit,directory_inherit"

// LeafGrantCmd returns the command that grants the agent full read/write/execute
// on the leaf workspace and everything inside it, existing and future (Layer 2).
// On macOS the allow is inserted at a low index and applied recursively so it
// wins over any pre-existing deny ACE on the subtree (see the ordering note
// above); on Linux it is a recursive access + default ACL.
//
// The macOS form drives the recursion with find and excludes symlinks (! -type l)
// rather than using chmod -R: a workspace like a Node/JS install is full of
// relative and dangling symlinks (pnpm/npm layouts), and chmod -R follows each one
// to its target, failing with "No such file or directory" for every broken link.
// An ACL on a symlink is ignored by the kernel anyway (access is decided by the
// target, which find stamps directly when it visits it), so skipping links is both
// correct and quiet. (macOS chmod also refuses -R and -h together, ruling out the
// obvious alternative.)
func LeafGrantCmd(agentUser, dir string) *exec.Cmd {
	if runtime.GOOS == "darwin" {
		return exec.Command("sudo", "find", dir, "!", "-type", "l", //nolint:gosec // agentUser is a config account name; dir is a resolved path.
			"-exec", "chmod", "+a#", "0", "user:"+agentUser+" allow "+macLeafACE, "{}", "+")
	}
	script := "setfacl -R -m u:" + shellQuote(agentUser) + ":rwX " + shellQuote(dir) +
		" && setfacl -R -d -m u:" + shellQuote(agentUser) + ":rwX " + shellQuote(dir)
	return exec.Command("sudo", "sh", "-c", script) //nolint:gosec // agentUser is a config account name; dir is a resolved path.
}

// LeafRevokeCmd removes the agent's rwx-leaf allow from dir (and its subtree),
// reversing LeafGrantCmd. Any ancestor traverse grants stay in place, but with
// the leaf allow gone the agent can no longer read or write the directory — and
// (unless the directory is world-readable) can no longer reach its contents. The
// permission string must match LeafGrantCmd's exactly so macOS removes the ACE.
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
// directory, reversing TraverseGrantCmd (Layer 1). It is the teardown counterpart
// used by `jentic reset`: where `--revoke` intentionally leaves ancestor traverse
// grants in place for the next grant, a full reset walks the ancestor chain and
// drops them too. Non-recursive, matching the grant.
func TraverseRevokeCmd(agentUser, dir string) *exec.Cmd {
	if runtime.GOOS == "darwin" {
		return exec.Command("sudo", "chmod", "-a", //nolint:gosec // agentUser is a config account name; dir is a resolved path.
			"user:"+agentUser+" allow execute", dir)
	}
	// -x removes the named-user entry entirely; on an ancestor that only ever
	// carried the execute-only traverse ACE this reverses TraverseGrantCmd exactly.
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
	// macOS prints "N: user:<name> allow …"; Linux getfacl prints "user:<name>:…"
	// (and "default:user:<name>:…"). Both contain the "user:<name>" needle.
	return strings.Contains(string(out), "user:"+agentUser)
}

// ReownHomeCmd changes ownership of the agent's home tree to the operator, so
// after the agent account is deleted the operator can still read the agent's
// work. It is the default `jentic reset` disposition for the home (preserve, not
// delete). The `-f` suppresses per-file errors on the SIP/TCC-protected template
// files a macOS home carries (Library/Mail, Library/Containers, …) that nobody
// can chown; the command still re-owns everything it can, and reset treats it as
// best-effort so those unavoidable entries don't abort the teardown. Runs as root.
func ReownHomeCmd(operator, homeDir string) *exec.Cmd {
	return exec.Command("sudo", "chown", "-Rf", operator, homeDir) //nolint:gosec // operator is the login user; homeDir is a resolved path.
}

// ReclaimAgentHomeCmd (re-)establishes the agent as the owner of its whole home
// tree. It is run when setting up the agent account, and matters most when the
// home ALREADY EXISTS: a prior `jentic reset` that kept the home re-owned it to
// the operator (ReownHomeCmd), and `createhomedir` only creates missing files —
// it never reclaims ownership of existing content. Without this, a re-bootstrap
// over that home leaves .claude/.aws/etc. operator-owned, so the agent can read
// but not WRITE them (fresh-config screens, provider token-cache failures,
// EACCES transcript writes). It mirrors ReownHomeCmd's `-Rf`: a macOS home carries
// SIP/TCC-protected template files nobody can chown, so it re-owns everything it
// can and the caller treats the residual non-zero exit as best-effort. Extended
// ACLs (the operator's inherited read/write grant) survive chown on both macOS and
// Linux, so this doesn't cost the operator access. Runs as root.
func ReclaimAgentHomeCmd(agentUser, homeDir string) *exec.Cmd {
	return exec.Command("sudo", "chown", "-Rf", agentUser, homeDir) //nolint:gosec // agentUser is a config account name; homeDir is a resolved path.
}

// ChownToAgentCmd gives the agent ownership of dir (recursively). It is used after
// the operator writes the agent's jentic identity into the agent's ~/.jentic:
// files the operator creates there are operator-owned, but the agent's 0600 key
// and tokens must be readable by the agent when it later runs as itself, so we
// hand the whole config dir to the agent. Runs as root.
func ChownToAgentCmd(agentUser, dir string) *exec.Cmd {
	return exec.Command("sudo", "chown", "-R", agentUser, dir) //nolint:gosec // agentUser is a config account name; dir is a resolved path under the agent's home.
}

// DeleteHomeCmd permanently removes the agent's home tree. `jentic reset` runs it
// ONLY when the operator has separately and explicitly accepted home deletion
// (the second runtime confirmation, or --delete-home paired with --force) — never
// by default. Runs as root.
func DeleteHomeCmd(homeDir string) *exec.Cmd {
	return exec.Command("sudo", "rm", "-rf", homeDir) //nolint:gosec // homeDir is a resolved, config-recorded path; deletion is explicitly confirmed by the caller.
}

// RemoveAgentIdentityCmd permanently removes the agent's own jentic config dir
// (its ~/.jentic — the reference-model home of the agent's platform identity: the
// registration, tokens, and signing key). `jentic reset` runs it even when the
// agent's home is KEPT, so a later `jentic bootstrap` that reuses the same home
// can't resurrect a torn-down (now-archived) agent registration from a stale
// ~/.jentic. It is a no-op when the dir is absent. Runs as root because the dir is
// owned by the agent account (and is settled before the home re-own/delete step).
func RemoveAgentIdentityCmd(configDir string) *exec.Cmd {
	return exec.Command("sudo", "rm", "-rf", configDir) //nolint:gosec // configDir is the config-recorded agent ~/.jentic path.
}

// RemoveSudoersCmd drops the agent user's passwordless-launch lines from the
// shared /etc/sudoers.d/jentic-agent drop-in, deleting the file if it becomes
// empty. It edits through a temp file validated with `visudo -c` before install,
// so a malformed result can never brick sudo, and is a no-op when the file is
// absent (the passwordless drop-in is optional). Runs as root.
func RemoveSudoersCmd(agentUser string) *exec.Cmd {
	q := shellQuote(agentUser)
	script := `f=/etc/sudoers.d/jentic-agent; [ -f "$f" ] || exit 0; ` +
		`tmp="$(mktemp)"; grep -v ` + q + ` "$f" > "$tmp" || true; ` +
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

// agentBashArgs builds the sudo argv that runs snippet as agentUser in a login
// bash. Shared by every agent invocation (probe, grant, and the confined launch).
//
// We use `sudo -u <user> -H bash -lc` rather than `sudo -i`: `-i` re-serializes
// the command through the login shell (mangling any multi-token/multi-line
// snippet), while plain sudo passes argv straight through. `-H` points HOME at
// the agent's home and `bash -l` still sources the agent's login profiles (so a
// PATH export we added there is honoured).
func agentBashArgs(agentUser, snippet string) []string {
	return []string{"-u", agentUser, "-H", "bash", "-lc", snippet}
}

// agentCmd builds `sudo -u <user> -H bash -lc <snippet>` with the working
// directory pinned to "/". Pinning is essential: the parent process's cwd is
// typically inside the operator's now-700 home, which the agent user cannot
// read — inheriting it makes bash spew `getcwd: Permission denied` before the
// snippet even runs. "/" is traversable by everyone.
func agentCmd(agentUser, snippet string) *exec.Cmd {
	cmd := exec.Command("sudo", agentBashArgs(agentUser, snippet)...) //nolint:gosec // agentUser is a config account name; snippet is shell-quoted / a fixed literal.
	cmd.Dir = "/"
	return cmd
}

// agentCmdContext is agentCmd with a cancellation context (for the launch).
func agentCmdContext(ctx context.Context, agentUser, snippet string) *exec.Cmd {
	cmd := exec.CommandContext(ctx, "sudo", agentBashArgs(agentUser, snippet)...) //nolint:gosec // agentUser is a config account name; snippet is shell-quoted.
	cmd.Dir = "/"
	return cmd
}

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
// dirs are deliberately NOT shared; see docs/security/local-agent/
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
// ~/.local/bin and chowns it to the agent. It runs as root (sudo sh -c) so it
// can write into the agent's home and change ownership in one step.
func CopyBinaryCmd(agentUser, src, binary string) *exec.Cmd {
	// Resolve the agent's home and place the copy at ~/.local/bin/<binary>.
	dest := "$(eval echo ~" + agentUser + ")/.local/bin"
	script := "mkdir -p " + dest + " && cp " + shellQuote(src) + " " + dest + "/" + shellQuote(binary) +
		" && chown -R " + shellQuote(agentUser) + ": " + dest + "/" + shellQuote(binary)
	return exec.Command("sudo", "sh", "-c", script) //nolint:gosec // agentUser/src/binary are config/descriptor-derived, shell-quoted.
}

// InstallBinaryCmd runs an agent's documented fresh-install command as the
// agent user in a login shell, so the toolchain lands in the agent's home.
func InstallBinaryCmd(agentUser, installCmd string) *exec.Cmd {
	return agentCmd(agentUser, installCmd)
}

// AgentHasConfig reports whether the agent user already has any of the
// descriptor's config paths in its own home, so the caller only offers to seed
// them once (a re-run won't clobber the agent's evolved config).
func AgentHasConfig(ctx context.Context, agentUser string, desc Descriptor) bool {
	for _, p := range desc.ConfigPaths {
		if runAsAgent(ctx, agentUser, "test -e "+quoteProbePath(p)) == nil {
			return true
		}
	}
	return false
}

// ExistingConfigPaths expands the descriptor's tilde-relative ConfigPaths
// against operatorHome and returns those that actually exist on disk, so the
// caller only offers to copy what's there.
func ExistingConfigPaths(operatorHome string, desc Descriptor) []string {
	var found []string
	for _, p := range desc.ConfigPaths {
		abs := expandTilde(p, operatorHome)
		if abs == "" {
			continue
		}
		if _, err := os.Stat(abs); err == nil {
			found = append(found, abs)
		}
	}
	return found
}

// CopyConfigCmd copies the operator's agent config paths (already expanded to
// absolute paths under the operator's home) into the agent user's home at the
// same tilde-relative location, then chowns them to the agent. It runs as root
// so it can read out of the operator's 700 home and write into the agent's.
//
// CAUTION: these files may carry provider-specific secrets (e.g. an API key the
// operator saved in the agent's own config). This deliberately hands the agent
// a copy of those; it is the operator's settings the agent is meant to inherit.
func CopyConfigCmd(agentUser, operatorHome string, srcs []string) *exec.Cmd {
	agentHome := "$(eval echo ~" + agentUser + ")"
	var b strings.Builder
	for _, src := range srcs {
		rel := strings.TrimPrefix(src, filepath.Clean(operatorHome)+string(filepath.Separator))
		dest := agentHome + "/" + shellQuote(rel)
		// Recreate the parent dir, copy recursively (dir or file), then chown.
		b.WriteString("mkdir -p \"$(dirname " + dest + ")\" && ")
		b.WriteString("cp -R " + shellQuote(src) + " " + dest + " && ")
		b.WriteString("chown -R " + shellQuote(agentUser) + ": " + dest + " && ")
	}
	script := strings.TrimSuffix(b.String(), " && ")
	return exec.Command("sudo", "sh", "-c", script) //nolint:gosec // agentUser/paths are config/descriptor-derived, shell-quoted.
}

// ProviderConfig describes the LLM provider an operator's Claude Code setup
// authenticates against, and the local config paths that hold that provider's
// settings. It is derived from the env block of ~/.claude/settings.json.
type ProviderConfig struct {
	// Name is the human provider label ("aws", "vertex", "anthropic").
	Name string
	// ConfigPaths are tilde-relative provider config paths under the operator's
	// home to seed into the agent's home (e.g. "~/.aws"). Empty for the default
	// Anthropic API, where the credential travels in the agent config itself and
	// there is no separate provider config to copy.
	ConfigPaths []string
}

// claudeSettings is the subset of ~/.claude/settings.json we read: the env block
// carries the provider selection (CLAUDE_CODE_USE_BEDROCK / _USE_VERTEX) that
// tells Claude Code which cloud provider's credentials to authenticate with.
type claudeSettings struct {
	Env map[string]string `json:"env"`
}

// DetectProvider reads the operator's ~/.claude/settings.json env block and
// returns the LLM provider that Claude Code will authenticate against, plus the
// provider config paths (under the operator's home) that back it.
//
//   - CLAUDE_CODE_USE_BEDROCK=1 → AWS Bedrock; config lives in ~/.aws (profiles,
//     SSO session). Only the *config* is seeded — Claude Code performs the SSO
//     login programmatically, so the cached SSO token is deliberately excluded.
//   - CLAUDE_CODE_USE_VERTEX=1 → Google Vertex; config lives in ~/.config/gcloud,
//     plus any explicit GOOGLE_APPLICATION_CREDENTIALS file.
//   - otherwise → the default Anthropic API, whose key (if any) is already in the
//     agent config we seed separately, so there is no extra provider config.
//
// A missing/unparseable settings.json returns the Anthropic default (no extra
// paths) rather than an error: seeding is best-effort and always opt-in.
func DetectProvider(operatorHome string) ProviderConfig {
	env := readClaudeEnv(operatorHome)
	switch {
	case isTruthy(env["CLAUDE_CODE_USE_BEDROCK"]):
		return ProviderConfig{Name: "aws", ConfigPaths: []string{"~/.aws"}}
	case isTruthy(env["CLAUDE_CODE_USE_VERTEX"]):
		paths := []string{"~/.config/gcloud"}
		if creds := env["GOOGLE_APPLICATION_CREDENTIALS"]; creds != "" {
			paths = append(paths, creds)
		}
		return ProviderConfig{Name: "vertex", ConfigPaths: paths}
	default:
		return ProviderConfig{Name: "anthropic"}
	}
}

// readClaudeEnv parses the env map from ~/.claude/settings.json, returning an
// empty map when the file is absent or malformed.
func readClaudeEnv(operatorHome string) map[string]string {
	if operatorHome == "" {
		return nil
	}
	data, err := os.ReadFile(filepath.Join(operatorHome, ".claude", "settings.json"))
	if err != nil {
		return nil
	}
	var s claudeSettings
	if err := json.Unmarshal(data, &s); err != nil {
		return nil
	}
	return s.Env
}

// isTruthy reports whether a settings env value enables a boolean flag. Claude
// Code treats "1"/"true" as on; anything else (incl. "0", "") is off.
func isTruthy(v string) bool {
	switch strings.ToLower(strings.TrimSpace(v)) {
	case "1", "true":
		return true
	default:
		return false
	}
}

// ProviderConfigPaths expands a provider's tilde-relative ConfigPaths against
// operatorHome and returns those that actually exist on disk, so the caller only
// offers to copy what's there.
func ProviderConfigPaths(operatorHome string, pc ProviderConfig) []string {
	var found []string
	for _, p := range pc.ConfigPaths {
		abs := expandTilde(p, operatorHome)
		if abs == "" {
			continue
		}
		if _, err := os.Stat(abs); err == nil {
			found = append(found, abs)
		}
	}
	return found
}

// AgentHasPaths reports whether the agent user already has any of the given
// tilde-relative-or-absolute paths in its own home, so provider config is only
// seeded once (a re-run won't clobber the agent's evolved config).
func AgentHasPaths(ctx context.Context, agentUser string, paths []string) bool {
	for _, p := range paths {
		if runAsAgent(ctx, agentUser, "test -e "+quoteProbePath(agentRelPath(p))) == nil {
			return true
		}
	}
	return false
}

// agentRelPath maps an operator-side absolute provider path back to a
// tilde-relative path so AgentHasPaths can probe the equivalent location in the
// agent's home. A path already under "~/" is returned unchanged; an absolute
// path under a home is re-rooted to "~/…"; anything else is returned as-is.
func agentRelPath(p string) string {
	if strings.HasPrefix(p, "~/") || p == "~" {
		return p
	}
	// Absolute paths from ProviderConfigPaths live under the operator's home;
	// re-root the last home-relative segment onto "~/". We only know the operator
	// home here via OperatorHome(); fall back to the basename if it's not a child.
	home := OperatorHome()
	if home != "" {
		if rel, ok := strings.CutPrefix(p, filepath.Clean(home)+string(filepath.Separator)); ok {
			return "~/" + rel
		}
	}
	return p
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

// runAsAgent runs a shell snippet as the agent user in a login shell and returns
// its error (nil on exit 0). Output is discarded; callers only need the verdict.
func runAsAgent(ctx context.Context, agentUser, snippet string) error {
	cmd := agentCmdContext(ctx, agentUser, snippet)
	cmd.Stdout = nil
	cmd.Stderr = nil
	return cmd.Run()
}

// quoteProbePath quotes a probe path for a `bash -lc` test, leaving a leading
// "~/" unquoted so the agent's login shell expands it to the agent's home
// (single-quoting the whole string would make bash treat "~" literally).
func quoteProbePath(p string) string {
	if rest, ok := strings.CutPrefix(p, "~/"); ok {
		return "~/" + shellQuote(rest)
	}
	return shellQuote(p)
}

// shellQuote wraps s in single quotes, escaping any embedded single quotes, so
// it is safe to interpolate into a `bash -lc` snippet.
func shellQuote(s string) string {
	return "'" + strings.ReplaceAll(s, "'", `'\''`) + "'"
}

// BanClass describes how a path is protected from being handed to the agent.
// The two classes are handled differently at the grant prompt (see run.go): a
// SoftBan blocks only the path itself, a HardBan blocks its whole subtree.
type BanClass int

const (
	// NotBanned means the path is an ordinary, safe-to-grant location.
	NotBanned BanClass = iota
	// SoftBan means the path itself must not be granted because it holds
	// secrets directly (e.g. the operator's home, another user's home), but a
	// grant on a *subdirectory* below it is still allowed.
	SoftBan
	// HardBan means nothing anywhere in this subtree may be granted — the path
	// and every descendant is off-limits (e.g. ~/.ssh, ~/.jentic, ~/.aws,
	// keychains, browser profiles, and OS-owned system trees).
	HardBan
)

// DangerVerdict is the result of classifying a candidate grant path: its ban
// class and a human-readable reason (empty when NotBanned).
type DangerVerdict struct {
	Class  BanClass
	Reason string
}

// Banned reports whether the path may not be granted at all.
func (v DangerVerdict) Banned() bool { return v.Class != NotBanned }

// Classify decides how (if at all) granting the agent access to dir is
// restricted. It distinguishes two protection classes:
//
//   - HardBan: the path or any ancestor of it is a sensitive-subtree root
//     (dotfile credential dirs like ~/.ssh, ~/.jentic, ~/.aws; keychains;
//     browser profiles; OS system trees). NOTHING under these may be granted,
//     so the check matches the path itself AND any descendant.
//   - SoftBan: the path is a home root that holds secrets directly (the
//     operator's own home, or any other human's home) — it must not be granted
//     as-is, but a subdirectory beneath it still can be.
//
// operatorHome is the operator's own home (os.UserHomeDir). Checks are against
// the cleaned absolute path.
func Classify(dir, operatorHome string) DangerVerdict {
	abs, err := filepath.Abs(dir)
	if err != nil {
		abs = dir
	}
	abs = filepath.Clean(abs)

	// HardBan first: sensitive dotfile subtrees under the operator's home. The
	// whole subtree is off-limits, so match the root or any descendant.
	if operatorHome != "" {
		home := filepath.Clean(operatorHome)
		for _, d := range sensitiveDotDirs {
			root := filepath.Join(home, d)
			if abs == root || strings.HasPrefix(abs, root+string(filepath.Separator)) {
				return DangerVerdict{HardBan,
					"this is inside a sensitive dir in the operator's home (" + d + ") holding keys/credentials"}
			}
		}
	}

	// HardBan: OS-owned system trees (root and everything below).
	for _, sys := range systemTrees {
		if abs == sys || strings.HasPrefix(abs, sys+string(filepath.Separator)) {
			return DangerVerdict{HardBan, "this is inside a system directory (" + sys + ")"}
		}
	}

	// SoftBan: the operator's own home root — holds secrets directly, but a
	// subdirectory below it may still be granted.
	if operatorHome != "" && abs == filepath.Clean(operatorHome) {
		return DangerVerdict{SoftBan,
			"this is the operator's home — granting here re-opens the credential boundary (keys, browser profile, SSH)"}
	}

	// SoftBan: any other human's home root (/Users/<name> or /home/<name>
	// exactly). The agent's own home lives under /Users/Shared or /opt, which is
	// not a direct child here.
	for _, base := range []string{"/Users", "/home"} {
		if isDirectChild(abs, base) {
			return DangerVerdict{SoftBan, "this is another user's home directory"}
		}
	}

	return DangerVerdict{NotBanned, ""}
}

// DangerReason returns the human reason a path is restricted (empty when it is
// freely grantable). It is a thin wrapper over Classify for display-only callers
// that don't need the ban class.
func DangerReason(dir, operatorHome string) string {
	return Classify(dir, operatorHome).Reason
}

// sensitiveDotDirs are the dotfile directories under a home whose entire subtree
// must never be handed to the agent (HardBan).
var sensitiveDotDirs = []string{
	".ssh", ".jentic", ".aws", ".config", ".gnupg", ".gcloud", ".kube",
	".docker", "Library/Keychains", ".mozilla", ".config/google-chrome",
	"Library/Application Support/Google/Chrome",
}

// systemTrees are OS-owned roots whose entire subtree must never be
// agent-granted (HardBan).
var systemTrees = []string{"/etc", "/usr", "/var", "/System", "/Library", "/bin", "/sbin", "/"}

// isDirectChild reports whether abs is exactly base/<one-segment> (a home root
// like /Users/alice), not base itself and not a deeper descendant.
func isDirectChild(abs, base string) bool {
	if !strings.HasPrefix(abs, base+string(filepath.Separator)) {
		return false
	}
	rest := strings.TrimPrefix(abs, base+string(filepath.Separator))
	return rest != "" && !strings.Contains(rest, string(filepath.Separator))
}

// OperatorHome returns the operator's home directory (os.UserHomeDir), or "".
func OperatorHome() string {
	h, err := os.UserHomeDir()
	if err != nil {
		return ""
	}
	return h
}
