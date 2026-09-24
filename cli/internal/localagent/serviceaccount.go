package localagent

// serviceaccount.go holds the per-runtime MCP SERVICE-ACCOUNT profile
// (local-MCP §3.7.5 rung 2): a dedicated `_jentic-<runtime>` Unix user the
// sudo-shim MCP entry runs the server as. This is deliberately NEW
// user-creation behavior, not a reuse of the login-capable agent-account path
// (CreateAccountCmds): a service account gets the `_` prefix, a system uid,
// NO login shell, and a 0700 state dir the desktop user cannot read — the
// whole point is that the context's key material moves behind this uid. One
// service user per runtime/agent, never a shared catch-all uid: the isolation
// boundary must coincide with the identity boundary (one runtime ↔ one agent
// ↔ one context ↔ one uid ↔ one sudoers line).

import (
	"errors"
	"fmt"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
)

// serviceUserPrefix is the naming convention for MCP service accounts. The
// `_` prefix is the macOS role-account convention; on Linux it simply keeps
// the namespace disjoint from human and agent-launch accounts.
const serviceUserPrefix = "_jentic-"

// ServiceUserName derives the dedicated service-account name for one agent
// runtime (e.g. "cursor" → "_jentic-cursor"). Callers validate the result
// with ValidateAgentUser before any privileged command is built.
func ServiceUserName(runtimeName string) string {
	return serviceUserPrefix + runtimeName
}

// ServiceHomeDir is the service account's home — its own 0700 state dir under
// the shared agent-home root (outside every human's home), holding the moved
// context material in its XDG layout.
func ServiceHomeDir(serviceUser string) string {
	return filepath.Join(AgentHomeRoot(), serviceUser)
}

// ServiceAccountFullName is the display name recorded for the account on
// macOS; unique per account name (Open Directory refuses duplicate full
// names — see AccountFullName for the history).
func ServiceAccountFullName(serviceUser string) string {
	return serviceUser + " (jentic MCP service account)"
}

// noLoginShell is the platform's no-login shell for service accounts.
func noLoginShell() string {
	if runtime.GOOS == "darwin" {
		return "/usr/bin/false"
	}
	return "/usr/sbin/nologin"
}

// CreateServiceAccountCmds returns the ordered privileged steps that create
// the MCP service account and its 0700 state dir. Unlike CreateAccountCmds it
// grants the OPERATOR nothing on the home — after the context material moves
// in, only the service uid (and root) can read it; the desktop user keeps
// just the sanctioned sudo spawn line.
//
// macOS: `sysadminctl -addUser … -roleAccount` provisions a role account (the
// `_` prefix is required by -roleAccount; the uid lands in the role range)
// with no password and the no-login shell. Linux: `useradd --system` with the
// nologin shell. Both then materialise the home root-side and pin it
// 0700/owned by the service uid. Steps are enumerated (never run here) so the
// recipe is assertable in tests without sudo — the AccountStep pattern.
func CreateServiceAccountCmds(serviceUser, homeDir string) []AccountStep {
	steps := []AccountStep{}
	if runtime.GOOS == "darwin" {
		steps = append(steps, AccountStep{
			What: "create the MCP service account",
			//nolint:gosec // serviceUser/homeDir are validated (ValidateAccount) before any step runs.
			Cmd: exec.Command("sudo", "sysadminctl", "-addUser", serviceUser,
				"-fullName", ServiceAccountFullName(serviceUser),
				"-home", homeDir, "-shell", noLoginShell(), "-password", "-", "-roleAccount"),
		})
	} else {
		steps = append(steps, AccountStep{
			What: "create the MCP service account",
			//nolint:gosec // serviceUser/homeDir are validated (ValidateAccount) before any step runs.
			Cmd: exec.Command("sudo", "useradd", "--system",
				"--shell", noLoginShell(), "--home-dir", homeDir, "--no-create-home", serviceUser),
		})
	}
	// The state dir is created root-side (role/system accounts get no home
	// template materialisation) and pinned to exactly 0700 under the service
	// uid: nothing inside is readable by the desktop user.
	steps = append(steps,
		AccountStep{
			What: "create the service account's state dir",
			Cmd:  exec.Command("sudo", "mkdir", "-p", homeDir), //nolint:gosec // homeDir is ValidateHomeDir-validated.
		},
		AccountStep{
			What: "own the state dir to the service account",
			Cmd:  exec.Command("sudo", "chown", "-R", serviceUser+":", homeDir), //nolint:gosec // validated inputs.
		},
		AccountStep{
			What: "pin the state dir to 0700",
			Cmd:  exec.Command("sudo", "chmod", "700", homeDir), //nolint:gosec // validated inputs.
		},
	)
	return steps
}

// ExportInstallCmds returns the ordered privileged steps that move rendered
// context material from stagingDir (an operator-private temp dir) into the
// service account's home ROOT-SIDE. The service home is deliberately 0700
// under the service uid with NO operator grant, so the operator process can
// never write into it directly — `sudo install` places each directory
// (0700, service-owned) and file (0600, service-owned) without the operator
// ever needing access to the home. relDirs/relFiles are home-relative paths;
// files are copied from the same relative location under stagingDir. Steps
// are enumerated (never run here) so the recipe is assertable in tests
// without sudo — the AccountStep pattern. Callers validate serviceUser and
// homeDir (ValidateAccount) before running any step.
func ExportInstallCmds(serviceUser, homeDir, stagingDir string, relDirs, relFiles []string) []AccountStep {
	steps := make([]AccountStep, 0, len(relDirs)+len(relFiles))
	for _, rel := range relDirs {
		steps = append(steps, AccountStep{
			What: "create the service account's " + rel + " dir",
			// `install -d` creates missing parents with the same owner/mode,
			// so the whole XDG chain lands 0700 under the service uid.
			//nolint:gosec // serviceUser/homeDir are validated; rel is a fixed XDG-relative join.
			Cmd: exec.Command("sudo", "install", "-d", "-o", serviceUser, "-m", "0700",
				filepath.Join(homeDir, rel)),
		})
	}
	for _, rel := range relFiles {
		steps = append(steps, AccountStep{
			What: "install " + rel + " into the service account's home",
			//nolint:gosec // serviceUser/homeDir are validated; stagingDir is a Go-created private temp dir; rel is a fixed relative join.
			Cmd: exec.Command("sudo", "install", "-o", serviceUser, "-m", "0600",
				filepath.Join(stagingDir, rel), filepath.Join(homeDir, rel)),
		})
	}
	return steps
}

// McpServiceTeardownCmds returns the ordered privileged steps that reverse
// everything the MCP isolation step created for one service account: the
// account's `/etc/sudoers.d/jentic-agent` NOPASSWD line (RemoveSudoersCmd,
// anchored on the runas spec), the 0700 home holding the exported key
// material, and the Unix account itself. Sudoers first (drop the operator's
// passwordless path before anything else), then the home (the exported
// signing key must not outlive the account), then the account record.
// Callers guard the home with ValidateHomeDir + VerifyManagedHome before
// running any step, exactly like the agent-account teardown.
func McpServiceTeardownCmds(serviceUser, homeDir string, accountExists bool) []AccountStep {
	steps := []AccountStep{
		{
			What: "remove the " + serviceUser + " sudoers line",
			Cmd:  RemoveSudoersCmd(serviceUser),
		},
	}
	if homeDir != "" {
		steps = append(steps, AccountStep{
			What: "delete the service account's home " + homeDir + " (exported key material)",
			Cmd:  DeleteHomeCmd(homeDir),
		})
	}
	if accountExists {
		steps = append(steps, AccountStep{
			What: "delete the Unix account " + serviceUser,
			Cmd:  DeleteAccountCmd(serviceUser),
		})
	}
	return steps
}

// ValidateMcpSudoersInputs guards the two values the argv-pinned rule
// interpolates beyond the account names: the binary path and the context
// name. Both land verbatim on a sudoers line, where a space, comma, colon,
// backslash, or control character could open a second alias/command — so the
// shape is constrained at the source, mirroring ValidateAccount's posture.
func ValidateMcpSudoersInputs(binPath, contextName string) error {
	if err := rejectControlChars("jentic binary path", binPath); err != nil {
		return err
	}
	if !filepath.IsAbs(binPath) {
		return fmt.Errorf("jentic binary path %q must be absolute for the sudoers rule", binPath)
	}
	if strings.ContainsAny(binPath, " \t,:=\\") {
		return fmt.Errorf("jentic binary path %q contains characters unsafe in a sudoers rule", binPath)
	}
	if contextName == "" {
		return errors.New("context name is empty")
	}
	for i, r := range contextName {
		ok := (r >= 'a' && r <= 'z') || (r >= '0' && r <= '9') || (i > 0 && r == '-')
		if !ok {
			return fmt.Errorf("context name %q is not a valid config name (lowercase alnum + '-')", contextName)
		}
	}
	return nil
}

// McpSudoersRule is the argv-pinned NOPASSWD line for the sudo-shim MCP
// entry: one source user → one target service user → EXACTLY the pinned
// `jentic mcp --context <name>` argv. sudo matches the full command line, so
// the entry cannot be replayed with a different context or subcommand. There
// is no root capability here — the runas spec names only the unprivileged
// service account.
func McpSudoersRule(operator, serviceUser, binPath, contextName string) string {
	return operator + " ALL=(" + serviceUser + ") NOPASSWD: " +
		binPath + " mcp --context " + contextName
}

// InstallSudoersRuleCmd adds one exact rule line to the shared
// /etc/sudoers.d/jentic-agent drop-in, using the same idempotent,
// visudo-validated temp-file edit as InstallSudoersCmd (which now delegates
// here). Runs as root. Removal is RemoveSudoersCmd, anchored on the rule's
// runas spec.
func InstallSudoersRuleCmd(rule string) *exec.Cmd {
	return installSudoersRuleCmd(rule)
}
