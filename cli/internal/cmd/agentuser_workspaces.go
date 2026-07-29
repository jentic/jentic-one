package cmd

import (
	"context"
	"fmt"

	"github.com/charmbracelet/huh"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/install"
	"github.com/jentic/jentic-one/cli/internal/localagent"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

// offerWorkspaceGrants reads the workspaces the operator has TRUSTED in this
// agent's own config (localagent.TrustedWorkspaces) and offers to grant the freshly
// created agent access to the ones the operator picks — so they don't have to
// re-grant every project by hand on first `jentic run`. Each chosen workspace is
// granted through the SAME scoped-ACL path (grantDir) an in-launch grant uses and
// is recorded, so it persists across sessions.
//
// The strict permission model always wins: TrustedWorkspaces already drops anything
// the grant rules would refuse (banned/sensitive/system trees, and paths no longer
// on disk), and as a belt-and-braces guard each selection is re-classified here and
// a banned one is skipped with a note. So a conflicting directory can never be
// granted, even if discovery and classification ever drifted.
//
// NOTE (documented in local-agent-isolation.md): agent selection is single-choice
// today, so this runs for the one selected operator, reading ITS trusted-projects
// list. When it becomes multi-choice this must run per selected operator — the
// trusted-projects source is per-agent (Claude Code's ~/.claude.json here), so each
// operator reads its own, not a shared home scan.
func (a *App) offerWorkspaceGrants(ctx context.Context, desc localagent.Descriptor, agentID, agentUser string) {
	workspaces := localagent.TrustedWorkspaces(localagent.OperatorHome(), desc)
	if len(workspaces) == 0 {
		return
	}

	chosen, err := a.pickWorkspaces(workspaces)
	if err != nil || len(chosen) == 0 {
		return
	}

	cfg, err := config.Load(a.Paths)
	if err != nil {
		fmt.Fprintln(a.Out, theme.Warnf("could not load config to record workspace grants: %v", err))
		return
	}

	home := localagent.OperatorHome()
	granted := 0
	for _, ws := range chosen {
		// Belt-and-braces: the strict rules take precedence over anything we offer.
		if v := localagent.Classify(ws, home); v.Banned() {
			fmt.Fprintln(a.Out, theme.Dim.Render(fmt.Sprintf("  Skipping %s — %s.", ws, v.Reason)))
			continue
		}
		if err := a.grantDir(ctx, cfg, agentID, agentUser, ws); err != nil {
			fmt.Fprintln(a.Out, theme.Warnf("could not grant %s: %v", ws, err))
			continue
		}
		granted++
	}
	if granted > 0 {
		fmt.Fprintln(a.Out, theme.Dim.Render(fmt.Sprintf(
			"Brought over %d workspace%s (`jentic run %s --list-grants` to review).",
			granted, plural(granted, "", "s"), agentID)))
	}
}

// pickWorkspaces shows the trusted workspaces as a pre-selected multiselect so the
// operator can trim the set before granting. All are checked by default: they are
// the operator's own trusted project dirs and bringing them over is the point.
func (a *App) pickWorkspaces(workspaces []string) ([]string, error) {
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Step.Render("Bring your workspaces over"))
	fmt.Fprintln(a.Out, theme.Dim.Render(
		"These are the workspaces you've trusted in this agent. Grant the isolated"))
	fmt.Fprintln(a.Out, theme.Dim.Render(
		"agent read/write to the ones you want it to work in — you can add more later"))
	fmt.Fprintln(a.Out, theme.Dim.Render("with `jentic run <agent> <path>`."))

	opts := make([]huh.Option[string], 0, len(workspaces))
	for _, ws := range workspaces {
		opts = append(opts, huh.NewOption(ws, ws))
	}
	selected := append([]string(nil), workspaces...) // pre-select all

	err := install.NewForm(huh.NewGroup(
		huh.NewMultiSelect[string]().
			Title("Grant the agent access to which workspaces?").
			Description("Space toggles, Enter confirms. Banned/sensitive dirs are never listed.").
			Options(opts...).
			Value(&selected),
	)).WithShowHelp(true).Run()
	if err != nil {
		return nil, err
	}
	return selected, nil
}
