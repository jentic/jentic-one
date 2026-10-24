package localagent

import (
	"context"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
)

// aclWalkFilter is the find(1) predicate every privileged recursive ACL grant
// walks with: never a symlink (chmod/setfacl would act on its target), and
// never a non-directory with more than one hard link. The trees these grants
// walk are writable by the agent, which could hard-link a file from elsewhere
// on the same filesystem into them; stamping an ACL on that inode would grant
// access to the original file too. Directories cannot be hard-linked by an
// unprivileged user, so they are always visited. find does not follow
// symlinks (-P is the default), including a symlinked starting point.
var aclWalkFilter = []string{"!", "-type", "l", "(", "-type", "d", "-o", "-links", "1", ")"}

// chownWalkFilter is aclWalkFilter for recursive chowns, which DO visit
// symlinks: `chown -h` re-owns the link itself, never its target. Multiply
// hard-linked non-directories are skipped for the same reason as above — a
// chown of that inode would re-own the original file wherever it lives.
var chownWalkFilter = []string{"(", "-type", "d", "-o", "-links", "1", ")"}

// shellFindFilter renders a find predicate for interpolation into a sh -c
// script (the parentheses and ! need escaping from the shell).
func shellFindFilter(filter []string) string {
	out := make([]string, len(filter))
	for i, tok := range filter {
		out[i] = shellQuote(tok)
	}
	return strings.Join(out, " ")
}

// linuxACLGrantScript is the Linux form of a recursive, inherited rwX grant
// for user on dir: an access ACL on every entry aclWalkFilter admits plus a
// default ACL on every directory, so later-created files inherit it. It is
// the find-driven equivalent of `setfacl -R -m … && setfacl -R -d -m …`,
// which would follow hard links out of the tree.
func linuxACLGrantScript(user, dir string) string {
	entry := "u:" + shellQuote(user) + ":rwX"
	return "find " + shellQuote(dir) + " " + shellFindFilter(aclWalkFilter) +
		" -exec setfacl -m " + entry + " {} +" +
		" && find " + shellQuote(dir) + " -type d -exec setfacl -d -m " + entry + " {} +"
}

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

// ProbeBinary checks whether desc.Binary is runnable as the agent user. The
// probe shell reads none of the agent's startup files (see agentBashArgs), so
// "on PATH" means on the fixed system/shared PATH; a binary in the agent's own
// ~/.local/bin is reported as found-off-PATH via desc.ProbePaths, and the
// caller then (idempotently) ensures the profile export the confined launch
// relies on. It distinguishes on-PATH, found-off-PATH, and missing so the
// caller can fix vs. reinstall.
func ProbeBinary(ctx context.Context, agentUser string, desc Descriptor) BinaryStatus {
	if runAsAgent(ctx, agentUser, "command -v "+shellQuote(desc.Binary)) == nil {
		return BinaryOnPath
	}
	for _, p := range desc.ProbePaths {
		// A leading "~" is expanded by the agent's shell (HOME is the agent's
		// home under `sudo -H`) — quote only the non-tilde remainder.
		if runAsAgent(ctx, agentUser, "test -x "+quoteProbePath(p)) == nil {
			return BinaryFoundOffPath
		}
	}
	return BinaryMissing
}

// CanRunAsAgentCmd builds a no-op `sudo -u <agent> … true` used as a preflight to
// confirm the operator can actually *become* the agent user before anything else
// runs. Every later step (ProbeBinary, the ACL grants, the launch) shells through
// the same sudo, and a failed sudo authentication (the operator declines the
// password prompt and no passwordless rule is installed) exits non-zero exactly
// like a genuine "command not found" — so without this check the binary probe
// misreads a refused password as a missing binary. `true` is a bash builtin, so
// it succeeds whenever sudo genuinely switched us to the agent and fails only when
// it could not. The caller must wire the command's stdio to the terminal so the
// sudo password prompt is visible and interactive.
func CanRunAsAgentCmd(ctx context.Context, agentUser string) *exec.Cmd {
	return agentCmdContext(ctx, agentUser, "true")
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

// sudoC builds a `sudo env LC_ALL=C <cmd>` invocation, forcing the elevated
// program's diagnostics into stable, byte-for-byte English regardless of the
// operator's locale. The grant path parses chmod/setfacl stderr to tell a benign
// mid-scan "No such file or directory" race apart from a real failure
// (classifyGrantStderr); under, say, a French or Japanese locale those messages
// are translated and the parse would misread a harmless race as a hard error and
// abort an already-applied grant. Setting the locale via `env` (the elevated
// program) rather than a `sudo LC_ALL=C` command-line assignment makes it
// independent of the sudoers env policy — `env` sets the variable itself before
// exec'ing the real command.
func sudoC(args ...string) *exec.Cmd {
	//nolint:gosec // callers pass config-derived account names and resolved paths.
	return exec.Command("sudo", append([]string{"env", "LC_ALL=C"}, args...)...)
}

// TraverseGrantCmd returns the command that grants the agent execute-only
// (traverse/search — not list or read) on a single directory (Layer 1). It is
// non-recursive and non-inherited: it opens pass-through on exactly this dir.
func TraverseGrantCmd(agentUser, dir string) *exec.Cmd {
	if runtime.GOOS == "darwin" {
		return sudoC("chmod", "+a", "user:"+agentUser+" allow execute", dir)
	}
	return sudoC("setfacl", "-m", "u:"+agentUser+":--x", dir)
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
//
// Both forms walk with aclWalkFilter: the leaf is agent-writable once granted,
// so a re-grant must not follow a hard link the agent placed in it.
func LeafGrantCmd(agentUser, dir string) *exec.Cmd {
	if runtime.GOOS == "darwin" {
		args := append([]string{"find", dir}, aclWalkFilter...)
		args = append(args, "-exec", "chmod", "+a#", "0", "user:"+agentUser+" allow "+macLeafACE, "{}", "+")
		return sudoC(args...)
	}
	return sudoC("sh", "-c", linuxACLGrantScript(agentUser, dir))
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
