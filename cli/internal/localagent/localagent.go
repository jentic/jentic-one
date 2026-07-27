// Package localagent holds the OS-level primitives behind `jentic run`: the
// known coding-agent descriptors, and the helpers that probe/grant/launch as a
// dedicated agent user. It is deliberately free of any cobra/config coupling so
// the command layer (internal/cmd) stays a thin orchestrator over these.
//
// The security model this implements is documented in
// docs/security/analysis/agent-as-unix-user/ (docs 05 and 07): the agent runs
// as its own unprivileged Unix user, the operator's home is locked 700, and the
// agent is granted access to individual working directories via inherited ACLs
// rather than by widening any human's home.
package localagent

import (
	"context"
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
}

// Registry is the set of known agents. New agents are added as rows here.
var Registry = map[string]Descriptor{
	"claude": {
		ID:           "claude",
		Binary:       "claude",
		ProbePaths:   []string{"~/.local/bin/claude"},
		Install:      "curl -fsSL https://claude.ai/install.sh | bash",
		SingleBinary: true,
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

// GrantDirCmd returns the platform command that grants agentUser inherited
// read/write/execute access to dir (and its future children), without touching
// any other path. macOS uses an ACL; Linux uses setfacl access + default ACLs.
func GrantDirCmd(agentUser, dir string) *exec.Cmd {
	if runtime.GOOS == "darwin" {
		return exec.Command("sudo", "chmod", "+a", //nolint:gosec // args are a config account name + resolved path.
			"user:"+agentUser+" allow read,write,execute,file_inherit,directory_inherit", dir)
	}
	// Linux: apply both the access ACL (recursive) and the default ACL so new
	// files inherit. Two invocations wrapped in a single sh -c.
	script := "setfacl -R -m u:" + shellQuote(agentUser) + ":rwX " + shellQuote(dir) +
		" && setfacl -R -d -m u:" + shellQuote(agentUser) + ":rwX " + shellQuote(dir)
	return exec.Command("sudo", "sh", "-c", script) //nolint:gosec // args are a config account name + resolved path.
}

// RevokeDirCmd returns the platform command that removes agentUser's ACL entry
// from dir, reversing GrantDirCmd.
func RevokeDirCmd(agentUser, dir string) *exec.Cmd {
	if runtime.GOOS == "darwin" {
		return exec.Command("sudo", "chmod", "-a", //nolint:gosec // args are a config account name + resolved path.
			"user:"+agentUser+" allow read,write,execute,file_inherit,directory_inherit", dir)
	}
	script := "setfacl -R -x u:" + shellQuote(agentUser) + " " + shellQuote(dir) +
		" && setfacl -R -d -x u:" + shellQuote(agentUser) + " " + shellQuote(dir)
	return exec.Command("sudo", "sh", "-c", script) //nolint:gosec // args are a config account name + resolved path.
}

// LaunchCmd builds the interactive launch: become the agent user in a login
// shell (fresh env, cd to the agent's home) and exec the binary. When dir is
// non-empty the shell cd's there first. The caller wires os.Stdin/out/err.
func LaunchCmd(ctx context.Context, agentUser, binary, dir string) *exec.Cmd {
	inner := "exec " + shellQuote(binary)
	if dir != "" {
		inner = "cd " + shellQuote(dir) + " && " + inner
	}
	return exec.CommandContext(ctx, "sudo", "-u", agentUser, "-i", "bash", "-lc", inner) //nolint:gosec // agentUser/binary/dir are config-derived, shell-quoted.
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
	return exec.Command("sudo", "-u", agentUser, "-i", "bash", "-lc", installCmd) //nolint:gosec // agentUser is a config account name; installCmd is a fixed descriptor value.
}

// runAsAgent runs a shell snippet as the agent user in a login shell and returns
// its error (nil on exit 0). Output is discarded; callers only need the verdict.
func runAsAgent(ctx context.Context, agentUser, snippet string) error {
	cmd := exec.CommandContext(ctx, "sudo", "-u", agentUser, "-i", "bash", "-lc", snippet) //nolint:gosec // agentUser is a config account name; snippet is built from shell-quoted values.
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

// DangerReason classifies whether granting the agent access to dir would
// re-open the credential boundary. It returns a non-empty human reason when dir
// is sensitive (so the caller demotes "Allow" to a typed-confirm), and "" when
// dir is an ordinary, safe-to-grant location.
//
// operatorHome is the operator's own home (os.UserHomeDir). The checks are
// against the cleaned absolute path.
func DangerReason(dir, operatorHome string) string {
	abs, err := filepath.Abs(dir)
	if err != nil {
		abs = dir
	}
	abs = filepath.Clean(abs)

	if operatorHome != "" {
		home := filepath.Clean(operatorHome)
		if abs == home {
			return "this is the operator's home — granting here re-opens the credential boundary (keys, browser profile, SSH)"
		}
		// Direct sensitive dotfile dirs under the operator's home.
		for _, d := range sensitiveDotDirs {
			if abs == filepath.Join(home, d) {
				return "this is a sensitive dir in the operator's home (" + d + ") holding keys/credentials"
			}
		}
	}

	// Any other human's home root (/Users/<name> or /home/<name> exactly). The
	// operator's own home was already caught above; the agent's home normally
	// lives under /Users/Shared or /opt, which is not a direct child here.
	for _, base := range []string{"/Users", "/home"} {
		if isDirectChild(abs, base) {
			return "this is another user's home directory"
		}
	}

	// System trees.
	for _, sys := range systemTrees {
		if abs == sys || strings.HasPrefix(abs, sys+string(filepath.Separator)) {
			return "this is a system directory (" + sys + ")"
		}
	}
	return ""
}

// sensitiveDotDirs are the dotfile directories under a home that must never be
// handed to the agent.
var sensitiveDotDirs = []string{
	".ssh", ".jentic", ".aws", ".config", ".gnupg", ".gcloud", ".kube",
	".docker", "Library/Keychains", ".mozilla", ".config/google-chrome",
	"Library/Application Support/Google/Chrome",
}

// systemTrees are OS-owned roots that should never be agent-granted.
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
