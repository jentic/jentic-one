package api

import (
	"context"
	"errors"
	"fmt"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/internal/cli/prompt"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

// resetOptions collects the two escape-hatch flags. Neither is a convenience:
//   - --force skips typed-name confirmation and is required for non-interactive
//     reset; without --delete-home, forced reset preserves the agent's home.
//   - --force --delete-home enables non-interactive home deletion; forced
//     home deletion requires --delete-home.
//   - Without --force, an interactive reset with an agent home asks a separate
//     home-deletion question, and its answer determines deletion even when
//     --delete-home was supplied.
//   - Without --force in a non-interactive shell, reset is refused before home
//     deletion.
type resetOptions struct {
	deleteHome bool
	force      bool
}

func newResetCmd(app *app) *cobra.Command {
	opts := &resetOptions{}
	cmd := &cobra.Command{
		Use:   "reset",
		Short: "Wipe this machine's jentic identity state and remove any leftover agent account",
		Long: "reset is the clean-slate hatch for this machine's jentic identity state.\n\n" +
			"If an older jentic release left a local agent account on this machine, reset\n" +
			"tears it down first — the dedicated Unix user (`<you>-local-agent`), the\n" +
			"named-user ACLs stamped across your home (both the leaf read/write grants and\n" +
			"the execute-only ancestor traverse grants), and the passwordless-launch\n" +
			"sudoers drop-in — plus any MCP-isolation service accounts (`_jentic-<runtime>`,\n" +
			"their sudoers lines, their exported key material, and the root-owned jentic\n" +
			"copy they ran). Current releases never create these. Then it wipes\n" +
			"your OWN jentic identity state: the store\n" +
			"(contexts, environments, identities, keys, tokens under ~/.config/jentic and\n" +
			"~/.local/state/jentic) and any legacy V1 profile tree under ~/.jentic. The\n" +
			"account's home is PRESERVED by default (re-owned to you); deleting it takes\n" +
			"a separate, explicit confirmation.\n\n" +
			"To remove a single context/identity instead of everything, use\n" +
			"`jentic context delete` / `jentic identity delete`.\n\n" +
			"Deleting an agent account and stripping ACLs are privileged, so reset\n" +
			"requires sudo for those steps: run it as yourself and you'll be prompted for your\n" +
			"password when it reaches the privileged steps. It shows the full plan before\n" +
			"touching anything. It only ever removes the agent's own named-user ACLs and\n" +
			"never touches another user's files.",
		Example: "  jentic reset                              # full clean slate: any agent account + your identity state\n" +
			"  jentic reset --force                      # non-interactive; keeps the home\n" +
			"  jentic reset --force --delete-home        # non-interactive; deletes the home",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.resetE(cmd.Context(), opts)
		},
	}
	cmd.Flags().BoolVar(&opts.force, "force", false,
		"skip the typed-name confirmation (the only non-interactive escape hatch; use with care)")
	cmd.Flags().BoolVar(&opts.deleteHome, "delete-home", false,
		"also delete the agent's home directory (non-interactive opt-in; pairs with --force)")
	return cmd
}

func (a *app) resetE(ctx context.Context, opts *resetOptions) error {
	// reset runs as the operator (not `sudo jentic reset`): it reads the operator's
	// own config and only the teardown STEPS are privileged (each is sudo-fronted),
	// so a single password prompt is triggered when it reaches them (sudo-last).
	// We flag that up front so the prompt isn't a surprise.
	operator, operatorHome := resetOperator()
	st, err := config.LoadAgentState(a.Paths)
	if err != nil {
		return err
	}

	interactive := prompt.Promptable()
	return a.resetAll(ctx, st, opts, interactive, operator, operatorHome)
}

// resetAll is the full clean slate: it tears down the shared agent account (Unix
// user, ACLs, sudoers, home disposition), removes any MCP-isolation service
// accounts (their sudoers lines, exported key material, and the root-owned
// jentic copy they run), and then wipes the
// operator's OWN jentic identity state — the XDG store and any legacy V1 tree.
// Everything is previewed first, then a single "reset" confirmation authorises
// the lot.
func (a *app) resetAll(ctx context.Context, st *config.AgentState, opts *resetOptions, interactive bool, operator, operatorHome string) error {
	acct, hasAcct := st.AgentAccount()
	hasPlan := hasAcct && acct.User != ""
	mcpPlan := surveyMcpIsolation(ctx)

	var plan resetPlan
	if hasPlan || mcpPlan.any() {
		fmt.Fprintln(a.Out, theme.Dim.Render(
			"Removing the agent account and ACLs is privileged (requires sudo) — you'll be "+
				"prompted for your password when reset reaches those steps."))
	}
	if hasPlan {
		plan = surveyReset(ctx, operator, operatorHome, acct)
		a.printResetPlan(plan)
	}
	if mcpPlan.any() {
		a.printMcpServicePlan(mcpPlan)
	}

	// Preview the operator's own identity-state wipe alongside the account plan.
	wipe := surveyIdentityWipe(a.Paths)
	if wipe.any() {
		a.printIdentityWipePlan(wipe)
	}

	// Nothing to do at all — no account, no MCP service accounts, and no
	// identity state — is a friendly no-op.
	if !hasPlan && !mcpPlan.any() && !wipe.any() {
		fmt.Fprintln(a.Out, theme.Dim.Render("Nothing to reset (no agent account, no jentic identity state)."))
		return nil
	}

	// Single whole-slate confirmation: type "reset".
	if !opts.force {
		if !interactive {
			return errors.New("refusing to reset non-interactively without --force (no safe default for destruction)")
		}
		ok, err := a.confirmFullReset()
		if err != nil {
			return err
		}
		if !ok {
			fmt.Fprintln(a.Out, theme.Dim.Render("Aborted — nothing was changed."))
			return nil
		}
	}

	// Execute: tear the account down, then wipe the operator's own identity state
	// LAST so a failure tearing down the account never leaves the operator without
	// the config that records what still needs cleaning. The whole-slate "reset"
	// confirmation authorises the teardown, but NOT home deletion — that stays a
	// separate, opt-in decision, so a full reset preserves the agent home unless
	// the operator explicitly asks otherwise.
	if hasPlan {
		deleteHome := opts.deleteHome
		if !opts.force && interactive && plan.homeDir != "" {
			accepted, err := a.confirmDeleteHome(plan.homeDir)
			if err != nil {
				return err
			}
			deleteHome = accepted
		}
		if err := a.execAccountReset(a.Paths, plan, deleteHome); err != nil {
			return err
		}
	}
	// MCP-isolation service accounts next, then the root-owned jentic copy
	// they ran: per-account fail-closed validation and best-effort
	// continuation live in execMcpServiceReset (a skipped account — and the
	// copy it still needs — resurfaces on the next run's survey).
	if mcpPlan.any() {
		a.execMcpServiceReset(mcpPlan)
	}
	if wipe.any() {
		if err := a.execIdentityWipe(wipe); err != nil {
			return err
		}
	}
	return nil
}
