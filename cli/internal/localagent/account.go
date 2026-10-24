package localagent

import (
	"context"
	"os/exec"
	"path/filepath"
	"runtime"
)

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
// setup recipe in docs/security/same-host/local-agent-isolation.md.
func DefaultHomeDir(agentUser string) string {
	if runtime.GOOS == "darwin" {
		return "/Users/Shared/" + agentUser
	}
	return "/opt/" + agentUser
}

// AgentConfigDir returns the agent's own LEGACY jentic config directory
// (~/.jentic inside the agent's home) — the V1 home of a self-user agent's
// platform identity, matching the default JENTIC_HOME layout. Current releases
// export the agent's identity into its home's XDG store instead; this remains
// only as the legacy entry in AgentIdentityDirs so reset keeps clearing it.
func AgentConfigDir(homeDir string) string {
	return filepath.Join(homeDir, ".jentic")
}

// AgentIdentityDirs returns every agent-home location that holds the agent's
// own jentic identity — the registration, tokens, and Ed25519 signing key:
//
//   - <home>/.config/jentic       (context export: minimal config + key)
//   - <home>/.local/state/jentic  (context export: tokens)
//   - <home>/.jentic              (legacy V1 identity/profiles)
//
// `jentic reset` removes all of them even when the home is KEPT, so credential
// material handed to the agent never survives a teardown in the re-owned home,
// and a later `jentic setup` that reuses the home can't resurrect a
// torn-down registration. Every path is a fixed join under the (validated)
// home, so the list is safe to hand to a privileged rm.
func AgentIdentityDirs(homeDir string) []string {
	return []string{
		filepath.Join(homeDir, ".config", "jentic"),
		filepath.Join(homeDir, ".local", "state", "jentic"),
		AgentConfigDir(homeDir),
	}
}

// AgentLocalBinDir returns the agent's own ~/.local/bin — the home-local
// executable route where CopyBinaryCmd / the install route place the launched
// agent binary and which EnsureLocalBinOnPathCmd prepends to the agent's login
// PATH. It is the single source of truth for that path, shared by the binary
// provisioner and the confinement layer (which marks it read-only so a
// compromised agent can't rewrite its own launched binary).
func AgentLocalBinDir(homeDir string) string {
	return filepath.Join(homeDir, ".local", "bin")
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
// docs/security/same-host/local-agent-isolation.md. It does NOT touch the
// operator's own home: in-home confidentiality against the agent is enforced per
// session by the process-confinement layer (see confine.go), not by locking ~.
//
// macOS: `sysadminctl -addUser` provisions a password-less account and only
// *records* the home path, so `createhomedir -c -u` is needed to actually create
// the directory; the operator ACL is an inherited `chmod +a` allow. Linux:
// `useradd -m` creates the home in one step, then two `setfacl` calls lay down
// the operator's access ACL and a matching default ACL for future contents.
//
// Note that sysadminctl can REFUSE the add yet still exit 0 (e.g. "User with
// full name '…' already exists"), so callers must verify the account actually
// exists afterwards (UserExists) rather than trusting the step's exit code.
func CreateAccountCmds(operator, agentUser, homeDir string) []AccountStep {
	if runtime.GOOS == "darwin" {
		return []AccountStep{
			{
				What: "create the agent account",
				//nolint:gosec // operator/agentUser/homeDir are config-derived account names and a resolved path.
				Cmd: exec.Command("sudo", "sysadminctl", "-addUser", agentUser,
					"-fullName", AccountFullName(operator, agentUser), "-home", homeDir, "-password", "-"),
			},
			{
				What: "create the agent's home directory",
				Cmd:  exec.Command("sudo", "createhomedir", "-c", "-u", agentUser), //nolint:gosec // agentUser is a config-derived account name.
			},
			{
				What: "grant the operator read/write into the agent's home",
				Cmd:  GrantOperatorHomeCmd(operator, homeDir),
				// Best-effort: the recursive grant descends into the macOS home
				// template, which carries SIP/TCC-protected entries (Library/Mail,
				// Library/Containers, ContainerManager, …) that NOBODY — not even
				// root — can ACL. `find`/`chmod` process everything they can and
				// exit non-zero on those; the home root (which carries the inherit
				// bits that actually matter) is stamped first, so a residual failure
				// on protected template files must not abort account creation.
				BestEffort: true,
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

// AccountFullName is the display (full) name recorded for the agent account on
// macOS. It MUST be unique per account name, not merely per operator: macOS Open
// Directory refuses a duplicate full name, so a constant "<operator> Local Agent"
// made every SECOND agent account for the same operator (any custom name) collide
// with the first's record — and sysadminctl reports that refusal on stderr while
// still exiting 0, so the collision surfaced only as a cascade of "unknown user"
// failures much later. Embedding the account name (which the OS already
// guarantees unique) makes the full name collision-free by construction.
func AccountFullName(operator, agentUser string) string {
	return agentUser + " (jentic agent of " + operator + ")"
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
		args := append([]string{"find", homeDir}, aclWalkFilter...)
		args = append(args, "-exec", "chmod", "+a#", "0", "user:"+operator+" allow "+macLeafACE, "{}", "+")
		return exec.Command("sudo", args...) //nolint:gosec // operator is the current login user; homeDir is a resolved path.
	}
	return exec.Command("sudo", "sh", "-c", linuxACLGrantScript(operator, homeDir)) //nolint:gosec // operator/homeDir are config-derived, shell-quoted.
}

// sudoersPath is the shared drop-in that holds the agents' passwordless-launch
// rules, one line per agent user. It is installed and removed only ever through
// `visudo -cf`-validated writes so a malformed edit can never brick sudo.
const sudoersPath = "/etc/sudoers.d/jentic-agent"

// agentLaunchShell is the command every agent invocation hands to sudo: the login
// bash that agentBashArgs runs as the agent user (probe, grant, and the confined
// launch all go through it). The passwordless rule is scoped to exactly this
// command, so the grant is "run bash as the agent user without a password" — not
// a general root grant. It is `/bin/bash` on both macOS and Linux, and the sudo
// argv names it by this ABSOLUTE path: sudo resolves a bare command name against
// its environment's PATH, which the curated launch env deliberately omits, and
// macOS's default sudoers sets no secure_path to fall back on.
const agentLaunchShell = "/bin/bash"

// SudoersRule is the single sudoers line that lets operator become agentUser via
// the launch shell with no password. The runas spec `(<agentUser>)` scopes it to
// that one unprivileged account; there is no root capability here.
func SudoersRule(operator, agentUser string) string {
	return operator + " ALL=(" + agentUser + ") NOPASSWD: " + agentLaunchShell
}

// InstallSudoersCmd adds operator's passwordless-launch rule for agentUser to the
// shared /etc/sudoers.d/jentic-agent drop-in. It is OPTIONAL and gated on explicit
// operator consent (setup's passwordless prompt): without it, every `jentic
// run` prompts for the operator's password (cached per-terminal ~5 min); with it,
// the operator can become the agent user (to launch it) with no prompt.
//
// The edit is idempotent (the exact rule line is added only if not already
// present) and safe: it builds the new content in a temp file, validates it with
// `visudo -cf` BEFORE installing, and only replaces the drop-in (mode 0440) if
// validation passes — so a bad edit can never lock the operator out of sudo. Runs
// as root. Mirrors RemoveSudoersCmd, the teardown that `jentic reset` runs.
func InstallSudoersCmd(operator, agentUser string) *exec.Cmd {
	return installSudoersRuleCmd(SudoersRule(operator, agentUser))
}

// installSudoersRuleCmd is the shared idempotent, visudo-validated sudoers
// edit behind InstallSudoersCmd (launch-shell rule) and InstallSudoersRuleCmd
// (argv-pinned MCP rule): build the new content in a temp file, add the exact
// rule line only if absent, validate with `visudo -cf` BEFORE installing, and
// only replace the 0440 drop-in when validation passes.
func installSudoersRuleCmd(sudoersRule string) *exec.Cmd {
	rule := shellQuote(sudoersRule)
	f := shellQuote(sudoersPath)
	script := `f=` + f + `; tmp="$(mktemp)"; ` +
		`[ -f "$f" ] && cat "$f" > "$tmp"; ` +
		`grep -qxF ` + rule + ` "$tmp" 2>/dev/null || echo ` + rule + ` >> "$tmp"; ` +
		`if visudo -cf "$tmp" >/dev/null 2>&1; then install -m 0440 "$tmp" "$f"; fi; ` +
		`rm -f "$tmp"`
	return exec.Command("sudo", "sh", "-c", script) //nolint:gosec // the rule is built from validated inputs and shell-quoted; the script edits a fixed sudoers path via visudo validation.
}

// RemoveSudoersCmd drops the agent user's passwordless-launch lines from the
// shared /etc/sudoers.d/jentic-agent drop-in, deleting the file if it becomes
// empty. It edits through a temp file validated with `visudo -c` before install,
// so a malformed result can never brick sudo, and is a no-op when the file is
// absent (the passwordless drop-in is optional). Runs as root.
//
// The line filter is ANCHORED on the runas spec `(<agentUser>)` — the exact token
// SudoersRule emits — rather than the bare name, because the drop-in is SHARED
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
