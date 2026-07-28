package cmd

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/user"
	"strings"

	"github.com/charmbracelet/huh"
	"github.com/charmbracelet/x/term"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/install"
	"github.com/jentic/jentic-one/cli/internal/localagent"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

// resetOptions collects the two escape-hatch flags. Neither is a convenience:
// --force is the ONLY way to skip the typed-name confirmation (there is no safe
// default for destruction), and --delete-home is the ONLY non-interactive way to
// opt into deleting the agent's home (interactively it is a separate typed
// prompt). Used together they enable scripted teardown; alone, --delete-home
// still requires the interactive home confirmation to have no effect other than
// pre-answering it, and is inert without --force in a non-interactive shell.
type resetOptions struct {
	deleteHome bool
	force      bool
}

func newResetCmd(app *App) *cobra.Command {
	opts := &resetOptions{}
	cmd := &cobra.Command{
		Use:   "reset [agent]",
		Short: "Tear down a local agent: account, home, ACLs, sudoers, and config entry",
		Long: "reset is the inverse of the agent-user setup folded into `jentic bootstrap`:\n" +
			"it removes the system state a local agent accumulated — the dedicated Unix\n" +
			"account, the named-user ACLs stamped across the operator's home (both the\n" +
			"leaf read/write grants and the execute-only ancestor traverse grants), the\n" +
			"passwordless-launch sudoers drop-in, and the local_agents entry in your\n" +
			"config. The agent's home is PRESERVED by default (re-owned to you); deleting\n" +
			"it takes a separate, explicit confirmation.\n\n" +
			"Deleting an account and stripping ACLs are privileged, so reset requires\n" +
			"sudo to complete: run it as yourself and you'll be prompted for your\n" +
			"password when it reaches the privileged steps. It shows the full plan before\n" +
			"touching anything. With no [agent] it targets every configured local agent.\n" +
			"It never reverts `chmod 700 ~` and never touches your own files.",
		Example: "  jentic reset\n" +
			"  jentic reset claude\n" +
			"  jentic reset claude --force               # non-interactive; keeps the home\n" +
			"  jentic reset claude --force --delete-home # non-interactive; deletes the home",
		Args: cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return app.resetE(cmd.Context(), opts, args)
		},
	}
	cmd.Flags().BoolVar(&opts.force, "force", false,
		"skip the typed-name confirmation (the only non-interactive escape hatch; use with care)")
	cmd.Flags().BoolVar(&opts.deleteHome, "delete-home", false,
		"also delete the agent's home directory (non-interactive opt-in; pairs with --force)")
	return cmd
}

func (a *App) resetE(ctx context.Context, opts *resetOptions, args []string) error {
	// reset runs as the operator (not `sudo jentic reset`): it reads the operator's
	// own config and only the teardown STEPS are privileged (each is sudo-fronted),
	// so a single password prompt is triggered when it reaches them — the same
	// sudo-last posture as agent-user creation. We flag that up front so the
	// prompt isn't a surprise, mirroring the "(requires sudo)" account gate.
	operator, operatorHome := resetOperator()
	cfg, err := config.Load(a.Paths)
	if err != nil {
		return err
	}

	targets, err := resetTargets(cfg, args)
	if err != nil {
		return err
	}

	fmt.Fprintln(a.Out, theme.Dim.Render(
		"Removing an agent's account and ACLs is privileged (requires sudo) — you'll be "+
			"prompted for your password when reset reaches those steps."))

	interactive := term.IsTerminal(os.Stdin.Fd())
	for _, agentID := range targets {
		if err := a.resetAgent(ctx, a.Paths, cfg, opts, interactive, operator, operatorHome, agentID); err != nil {
			return err
		}
	}
	return nil
}

// resetTargets resolves which agents to tear down: the single named one (which
// must be configured) or, with no argument, every configured local agent.
func resetTargets(cfg *config.FileConfig, args []string) ([]string, error) {
	if len(args) == 1 {
		if _, ok := cfg.LocalAgent(args[0]); !ok {
			return nil, fmt.Errorf("no configured local agent %q (nothing to reset)", args[0])
		}
		return []string{args[0]}, nil
	}
	if len(cfg.LocalAgents) == 0 {
		return nil, errors.New("no configured local agents to reset")
	}
	ids := make([]string, 0, len(cfg.LocalAgents))
	for id := range cfg.LocalAgents {
		ids = append(ids, id)
	}
	// Stable order without pulling in sort for one call site.
	for i := 1; i < len(ids); i++ {
		for j := i; j > 0 && ids[j-1] > ids[j]; j-- {
			ids[j-1], ids[j] = ids[j], ids[j-1]
		}
	}
	return ids, nil
}

// resetAgent surveys, confirms, and tears down a single agent. Nothing is changed
// during the survey; every removal happens after the typed-name confirmation.
func (a *App) resetAgent(ctx context.Context, paths config.Paths, cfg *config.FileConfig, opts *resetOptions, interactive bool, operator, operatorHome, agentID string) error {
	entry, _ := cfg.LocalAgent(agentID)
	plan := surveyReset(ctx, operator, operatorHome, agentID, entry)

	a.printResetPlan(plan)

	// Confirmation: a typed agent name, not a keypress. --force is the only skip.
	if !opts.force {
		if !interactive {
			return errors.New("refusing to reset non-interactively without --force (no safe default for destruction)")
		}
		ok, err := a.confirmResetName(agentID)
		if err != nil {
			return err
		}
		if !ok {
			fmt.Fprintln(a.Out, theme.Dim.Render("Aborted — nothing was changed."))
			return nil
		}
	}

	// Home disposition: preserved (re-owned) by default. Deleting it needs a
	// separate acceptance — the second typed prompt interactively, or
	// --delete-home paired with --force for scripted teardown.
	deleteHome := opts.deleteHome
	if !opts.force && interactive && plan.homeDir != "" {
		accepted, err := a.confirmDeleteHome(plan.homeDir)
		if err != nil {
			return err
		}
		deleteHome = accepted
	}

	// Act. Steps run in a fixed order (ACLs → home → sudoers → account); a failure
	// stops the run with the config entry still recorded so a re-run can finish.
	for _, step := range buildResetSteps(plan, deleteHome) {
		fmt.Fprintln(a.Out, theme.Infof("• %s", step.What))
		c := step.Cmd
		c.Stdout, c.Stderr = a.Out, a.Err
		if err := c.Run(); err != nil {
			return fmt.Errorf("%s: %w", step.What, err)
		}
	}

	// Remove the config entry LAST, so a mid-way failure above leaves the record
	// of what still needs cleaning for the next run.
	delete(cfg.LocalAgents, agentID)
	if err := cfg.Save(paths); err != nil {
		return err
	}
	fmt.Fprintln(a.Out, theme.Successf("Reset complete for agent %q.", agentID))
	if !deleteHome && plan.homeDir != "" {
		fmt.Fprintln(a.Out, theme.Dim.Render("  The agent's home was kept and re-owned to you: "+plan.homeDir))
	}
	return nil
}

// aclRemoval is one ACL entry the reset will drop, tagged by layer and whether it
// is actually present on disk right now (so the plan is truthful and we never ask
// macOS to remove an ACE that isn't there).
type aclRemoval struct {
	// traverse marks a Layer-1 execute-only ancestor grant; false is a Layer-2 leaf.
	traverse bool
	dir      string
	// present reports whether the agent ACL is currently on disk for dir.
	present bool
}

// resetPlan is the fully-resolved teardown for one agent, built by surveyReset
// and rendered/executed without further disk probing of the config.
type resetPlan struct {
	agentID       string
	user          string
	homeDir       string
	operator      string
	acls          []aclRemoval
	accountExists bool
}

// surveyReset resolves the teardown plan for one agent from two sources: the
// recorded config entry (user, home, granted dirs) AND a live re-scan of the
// on-disk ACLs, so grants that drifted from the config are still caught. Nothing
// is modified. Leaf grants come from GrantedDirs; the ancestor traverse grants
// are recomputed by walking each granted dir's chain up to the operator's home
// (deduped) and checking which still carry the agent's ACL.
func surveyReset(ctx context.Context, operator, operatorHome, agentID string, entry config.LocalAgent) resetPlan {
	agentUser := entry.User
	if agentUser == "" {
		agentUser = localagent.DefaultUserName(operator)
	}

	var acls []aclRemoval
	seenTraverse := map[string]bool{}

	for _, dir := range entry.GrantedDirs {
		acls = append(acls, aclRemoval{
			traverse: false,
			dir:      dir,
			present:  localagent.AgentACLPresent(ctx, agentUser, dir),
		})
		// Ancestor traverse grants: walk home→leaf and record each ancestor that
		// still carries the agent's execute ACL. Deduped across all granted dirs.
		if operatorHome != "" && localagent.IsUnderHome(operatorHome, dir) {
			for _, anc := range localagent.AncestorChain(operatorHome, dir) {
				if seenTraverse[anc] {
					continue
				}
				seenTraverse[anc] = true
				acls = append(acls, aclRemoval{
					traverse: true,
					dir:      anc,
					present:  localagent.AgentACLPresent(ctx, agentUser, anc),
				})
			}
		}
	}

	return resetPlan{
		agentID:       agentID,
		user:          agentUser,
		homeDir:       entry.HomeDir,
		operator:      operator,
		acls:          acls,
		accountExists: localagent.UserExists(ctx, agentUser),
	}
}

// buildResetSteps turns a plan into the ordered, privileged commands the reset
// runs. Order is load-bearing: drop access (leaf grants, then ancestor traverse)
// before removing the account, so a failure part-way never leaves a live account
// with dangling grants; settle the home next; then the sudoers drop-in; then the
// account. ACL entries no longer present on disk are skipped (the plan already
// flagged them) so macOS `chmod -a` never errors on a missing ACE. The account
// delete deliberately keeps the home — the home was already settled by the step
// before it. The config-entry removal is NOT a step here: resetAgent does it last
// in Go, after every step below has succeeded.
func buildResetSteps(plan resetPlan, deleteHome bool) []localagent.AccountStep {
	var steps []localagent.AccountStep

	// (1a) Leaf rwx grants first.
	for _, acl := range plan.acls {
		if acl.traverse || !acl.present {
			continue
		}
		steps = append(steps, localagent.AccountStep{
			What: "remove read/write grant on " + acl.dir,
			Cmd:  localagent.LeafRevokeCmd(plan.user, acl.dir),
		})
	}
	// (1b) Ancestor traverse grants next.
	for _, acl := range plan.acls {
		if !acl.traverse || !acl.present {
			continue
		}
		steps = append(steps, localagent.AccountStep{
			What: "remove traverse grant on " + acl.dir,
			Cmd:  localagent.TraverseRevokeCmd(plan.user, acl.dir),
		})
	}

	// (2) Settle the home: delete only on explicit acceptance, else re-own it to
	// the operator so it survives the account deletion and stays readable.
	if plan.homeDir != "" {
		if deleteHome {
			steps = append(steps, localagent.AccountStep{
				What: "delete the agent's home " + plan.homeDir,
				Cmd:  localagent.DeleteHomeCmd(plan.homeDir),
			})
		} else {
			steps = append(steps, localagent.AccountStep{
				What: "re-own the agent's home to " + plan.operator,
				Cmd:  localagent.ReownHomeCmd(plan.operator, plan.homeDir),
			})
		}
	}

	// (3) Passwordless-launch sudoers drop-in (no-op if absent).
	steps = append(steps, localagent.AccountStep{
		What: "remove the passwordless-launch sudoers drop-in",
		Cmd:  localagent.RemoveSudoersCmd(plan.user),
	})

	// (4) The Unix account itself, keeping the (already-settled) home.
	if plan.accountExists {
		steps = append(steps, localagent.AccountStep{
			What: "delete the Unix account " + plan.user,
			Cmd:  localagent.DeleteAccountCmd(plan.user),
		})
	}

	return steps
}

// printResetPlan renders the danger-zone banner and the resolved plan. It mirrors
// the dangerous-grant confirmation's bar: the irreversible nature is headlined,
// what is and isn't touched is explicit, and (for interactive runs) confirmation
// follows as a typed agent name.
func (a *App) printResetPlan(plan resetPlan) {
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Error.Render("⚠  DANGER ZONE — jentic reset will PERMANENTLY remove the following for agent "+
		"'"+plan.agentID+"' (user "+plan.user+"). This cannot be undone."))

	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Warn.Render("  Directory ACLs to remove (agent access granted by jentic run):"))
	any := false
	for _, acl := range plan.acls {
		if !acl.present {
			continue
		}
		any = true
		kind := "leaf grant"
		if acl.traverse {
			kind = "traverse   "
		}
		fmt.Fprintln(a.Out, theme.Dim.Render(fmt.Sprintf("    - %s  user:%s  %s", kind, plan.user, acl.dir)))
	}
	// Flag config-recorded grants whose ACL has already drifted off disk.
	for _, acl := range plan.acls {
		if acl.present || acl.traverse {
			continue
		}
		fmt.Fprintln(a.Out, theme.Dim.Render(fmt.Sprintf("    - (already gone) leaf grant  %s — recorded in config but no ACL on disk", acl.dir)))
	}
	if !any {
		fmt.Fprintln(a.Out, theme.Dim.Render("    - none found on disk"))
	}

	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Warn.Render("  Files & config to remove:"))
	fmt.Fprintln(a.Out, theme.Dim.Render("    - sudoers drop-in        /etc/sudoers.d/jentic-agent (if present)"))
	fmt.Fprintln(a.Out, theme.Dim.Render("    - local_agents['"+plan.agentID+"'] entry in your config"))

	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Warn.Render("  Account to delete:"))
	if plan.accountExists {
		fmt.Fprintln(a.Out, theme.Dim.Render("    - Unix user  "+plan.user))
	} else {
		fmt.Fprintln(a.Out, theme.Dim.Render("    - (none — user "+plan.user+" does not exist)"))
	}

	if plan.homeDir != "" {
		fmt.Fprintln(a.Out)
		fmt.Fprintln(a.Out, theme.Warn.Render("  Agent home:"))
		fmt.Fprintln(a.Out, theme.Dim.Render("    - "+plan.homeDir))
		fmt.Fprintln(a.Out, theme.Dim.Render("      Default: KEPT on disk and re-owned to you ("+plan.operator+")."))
		fmt.Fprintln(a.Out, theme.Dim.Render("      You'll be asked separately whether to delete it."))
	}

	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Success.Render("  NOT touched:"))
	fmt.Fprintln(a.Out, theme.Dim.Render("    - your own home stays chmod 700 — reset does not revert it"))
	fmt.Fprintln(a.Out, theme.Dim.Render("    - your own files, config, keys, and Jentic One itself"))
	fmt.Fprintln(a.Out)
}

// confirmResetName requires the operator to type the agent name to proceed —
// the same bar as a dangerous directory grant. Anything else aborts.
func (a *App) confirmResetName(agentID string) (bool, error) {
	var typed string
	if err := install.NewForm(huh.NewGroup(
		install.Input().
			Title("Type the agent name ('" + agentID + "') to confirm, or anything else to abort").
			Value(&typed),
	)).Run(); err != nil {
		if errors.Is(err, huh.ErrUserAborted) {
			return false, nil
		}
		return false, err
	}
	return strings.TrimSpace(typed) == agentID, nil
}

// confirmDeleteHome is the separate, explicit acceptance for deleting the agent's
// home. Preserve is the default: only an exact "delete home" opts into deletion.
func (a *App) confirmDeleteHome(homeDir string) (bool, error) {
	fmt.Fprintln(a.Out, theme.Warnf("  The agent's home %s will be KEPT and re-owned to you.", homeDir))
	var typed string
	if err := install.NewForm(huh.NewGroup(
		install.Input().
			Title("To PERMANENTLY DELETE it instead, type 'delete home' (anything else keeps it)").
			Value(&typed),
	)).Run(); err != nil {
		if errors.Is(err, huh.ErrUserAborted) {
			return false, nil
		}
		return false, err
	}
	return strings.TrimSpace(typed) == "delete home", nil
}

// resetOperator resolves the human operator whose config we act on and whose home
// receives the re-owned agent home. reset runs as the operator (not under sudo —
// only its individual steps are sudo-fronted), so the current user IS the operator;
// there is no root/SUDO_USER indirection to unwind.
func resetOperator() (name, home string) {
	if u, err := user.Current(); err == nil && u.Username != "" {
		return u.Username, u.HomeDir
	}
	return "user", localagent.OperatorHome()
}
