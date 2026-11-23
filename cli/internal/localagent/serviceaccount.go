package localagent

// serviceaccount.go names the per-runtime MCP SERVICE ACCOUNTS
// (`_jentic-<runtime>`) older releases' MCP isolation step created — a
// no-login system user per runtime whose 0700 home held the exported context
// key — and builds the privileged steps `jentic reset` uses to remove them.

import (
	"path/filepath"
)

// serviceUserPrefix is the naming convention for MCP service accounts. The
// `_` prefix is the macOS role-account convention; on Linux it simply keeps
// the namespace disjoint from human and agent-launch accounts.
const serviceUserPrefix = "_jentic-"

// ServiceUserName derives the service-account name for one agent runtime
// (e.g. "cursor" → "_jentic-cursor"). Callers validate the result with
// ValidateAccount before any privileged command is built.
func ServiceUserName(runtimeName string) string {
	return serviceUserPrefix + runtimeName
}

// ServiceHomeDir is the service account's home — its own 0700 state dir under
// the shared agent-home root (outside every human's home), holding the
// exported context material in its XDG layout.
func ServiceHomeDir(serviceUser string) string {
	return filepath.Join(AgentHomeRoot(), serviceUser)
}

// McpServiceTeardownCmds returns the ordered privileged steps that remove
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
