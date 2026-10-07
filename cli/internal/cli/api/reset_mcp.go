package api

// reset_mcp.go tears down what `jentic setup`'s MCP isolation step created
// (local-MCP §3.7.5 rung 2): the per-runtime `_jentic-<runtime>` service
// accounts, their argv-pinned NOPASSWD lines in /etc/sudoers.d/jentic-agent,
// the exported key material in each 0700 service home, and the root-owned
// jentic copy those lines pin. Folded into `jentic reset` (the same
// whole-slate confirmation covers it), mirroring the agent-account teardown:
// survey → print → validate → ordered privileged steps, fail-closed on any
// path that doesn't verify as the managed one.

import (
	"context"
	"fmt"

	"github.com/jentic/jentic-one/cli/internal/localagent"
	"github.com/jentic/jentic-one/cli/internal/mcpcfg"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

// mcpServicePlan is one existing MCP service account resolved for teardown.
type mcpServicePlan struct {
	user    string
	homeDir string
}

// mcpIsolationPlan is everything the isolation step left on the machine: the
// surveyed service accounts plus whether the shared root-owned jentic copy is
// installed (it can outlive the accounts after an interrupted run).
type mcpIsolationPlan struct {
	accounts []mcpServicePlan
	binary   bool
}

// any reports whether there is anything to tear down.
func (p mcpIsolationPlan) any() bool { return len(p.accounts) > 0 || p.binary }

// surveyMcpIsolation probes the closed set of per-runtime service accounts the
// isolation step can create and the root-owned jentic copy. Live OS probing
// (not config): the isolation step records nothing, so the account database
// and the fixed copy path are the source of truth — which also catches state
// orphaned by a failed isolation run. Nothing is modified.
func surveyMcpIsolation(ctx context.Context) mcpIsolationPlan {
	var out mcpIsolationPlan
	for _, rt := range mcpcfg.SupportedRuntimes() {
		user := localagent.ServiceUserName(string(rt))
		if !localagent.UserExists(ctx, user) {
			continue
		}
		out.accounts = append(out.accounts, mcpServicePlan{user: user, homeDir: localagent.ServiceHomeDir(user)})
	}
	out.binary = localagent.ServiceBinaryInstalled()
	return out
}

// printMcpServicePlan renders the MCP-isolation section of the reset plan.
func (a *app) printMcpServicePlan(plan mcpIsolationPlan) {
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Warn.Render("  MCP isolation to remove (created by `jentic setup`'s isolation step):"))
	for _, p := range plan.accounts {
		fmt.Fprintln(a.Out, theme.Dim.Render("    - service account "+p.user+" (its sudoers line, exported key material in "+p.homeDir+", and the account)"))
	}
	if plan.binary {
		fmt.Fprintln(a.Out, theme.Dim.Render("    - the root-owned jentic copy "+localagent.ServiceBinaryPath()+" the service accounts run"))
	}
}

// execMcpServiceReset runs the privileged teardown for every surveyed MCP
// service account. Per account, fail-closed: the derived name/home must
// re-validate and the account's LIVE home must be the managed one
// (VerifyManagedHome) before any privileged command runs — a name collision
// with a pre-existing account must never let root rm the wrong tree. A
// failing account is reported and skipped so the rest (and the caller's
// identity wipe) still complete; the survivors show up again on a re-run.
// The shared root-owned jentic copy goes LAST, and only once every account
// (and so every sudoers line naming it) is gone.
func (a *app) execMcpServiceReset(plan mcpIsolationPlan) {
	anyFailed := false
	for _, p := range plan.accounts {
		if err := localagent.ValidateAccount(p.user, p.homeDir); err != nil {
			fmt.Fprintln(a.Out, theme.Warnf("skipping MCP service account %q: %v", p.user, err))
			anyFailed = true
			continue
		}
		if err := localagent.VerifyManagedHome(p.user, p.homeDir); err != nil {
			fmt.Fprintln(a.Out, theme.Warnf("skipping MCP service account %q: %v", p.user, err))
			anyFailed = true
			continue
		}
		failed := false
		for _, step := range localagent.McpServiceTeardownCmds(p.user, p.homeDir, true) {
			fmt.Fprintln(a.Out, theme.Infof("• %s", step.What))
			c := step.Cmd
			c.Stdout, c.Stderr = a.Out, a.Err
			if err := c.Run(); err != nil {
				fmt.Fprintln(a.Out, theme.Warnf("%s: %v (re-run `jentic reset` to finish)", step.What, err))
				failed = true
				break
			}
		}
		if failed {
			anyFailed = true
			continue
		}
		fmt.Fprintln(a.Out, theme.Successf("Removed MCP service account %q.", p.user))
	}

	if !plan.binary {
		return
	}
	if anyFailed {
		fmt.Fprintln(a.Out, theme.Warnf("keeping %s while an MCP service account remains (re-run `jentic reset` to finish)", localagent.ServiceBinaryPath()))
		return
	}
	what := "remove the root-owned jentic copy " + localagent.ServiceBinaryPath()
	fmt.Fprintln(a.Out, theme.Infof("• %s", what))
	c := localagent.RemoveServiceBinaryCmd()
	c.Stdout, c.Stderr = a.Out, a.Err
	if err := c.Run(); err != nil {
		fmt.Fprintln(a.Out, theme.Warnf("%s: %v (re-run `jentic reset` to finish)", what, err))
	}
}
