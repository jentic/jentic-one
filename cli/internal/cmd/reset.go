package cmd

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/user"
	"strings"

	"github.com/charmbracelet/huh"
	"github.com/charmbracelet/x/term"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/install"
	"github.com/jentic/jentic-one/cli/internal/localagent"
	"github.com/jentic/jentic-one/cli/internal/profile"
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
		Use:   "reset [profile]",
		Short: "Tear down the agent account, or remove a single profile",
		Long: "reset is the inverse of the agent-user setup folded into `jentic bootstrap`.\n" +
			"There is ONE dedicated agent Unix account shared by every profile, so reset\n" +
			"works at two granularities.\n\n" +
			"`jentic reset` with no argument is a full clean slate: it tears down the\n" +
			"agent account — the dedicated Unix user, the named-user ACLs stamped across\n" +
			"your home (both the leaf read/write grants and the execute-only ancestor\n" +
			"traverse grants), the passwordless-launch sudoers drop-in, and every agent\n" +
			"profile in the account's home — and then also wipes your OWN jentic CLI\n" +
			"config: every profile (keys, tokens, registration) under ~/.jentic/profiles\n" +
			"and the default profile. The account's home is PRESERVED by default (re-owned\n" +
			"to you); deleting it takes a separate, explicit confirmation.\n\n" +
			"`jentic reset <profile>` removes just that one profile. If it is an\n" +
			"agent-owned profile and the LAST one in the account, reset offers to also\n" +
			"tear down the whole account (grants, sudoers, Unix user, home) — otherwise\n" +
			"the account and its grants are left in place.\n\n" +
			"Deleting the account and stripping ACLs are privileged, so reset requires\n" +
			"sudo to complete: run it as yourself and you'll be prompted for your\n" +
			"password when it reaches the privileged steps. It shows the full plan before\n" +
			"touching anything. It only ever removes the agent's own named-user ACLs and\n" +
			"never touches another user's files.",
		Example: "  jentic reset                              # full clean slate: the account + your own config\n" +
			"  jentic reset work                         # just the 'work' profile\n" +
			"  jentic reset --force                      # non-interactive; keeps the home\n" +
			"  jentic reset --force --delete-home        # non-interactive; deletes the home",
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

	interactive := term.IsTerminal(os.Stdin.Fd())

	// Scope follows the argument. `jentic reset <profile>` removes a single
	// profile; a bare `jentic reset` is a full clean slate — the whole agent
	// account plus the operator's own jentic CLI config.
	if len(args) == 1 {
		return a.resetProfile(ctx, cfg, opts, interactive, operator, operatorHome, args[0])
	}
	return a.resetAll(ctx, cfg, opts, interactive, operator, operatorHome)
}

// resetAll is the full clean slate: it tears down the shared agent account (Unix
// user, ACLs, sudoers, home disposition, and every agent profile) and then wipes
// the operator's OWN jentic CLI config. Everything is previewed first, then a
// single "reset" confirmation authorises the lot.
func (a *App) resetAll(ctx context.Context, cfg *config.FileConfig, opts *resetOptions, interactive bool, operator, operatorHome string) error {
	acct, hasAcct := cfg.AgentAccount()
	hasPlan := hasAcct && acct.User != ""

	var plan resetPlan
	if hasPlan {
		fmt.Fprintln(a.Out, theme.Dim.Render(
			"Removing the agent account and ACLs is privileged (requires sudo) — you'll be "+
				"prompted for your password when reset reaches those steps."))
		plan = surveyReset(ctx, operator, operatorHome, acct)
		a.printResetPlan(plan)
	}

	// Preview the operator's own config wipe alongside the account plan.
	cfgNames, err := profile.List(a.Paths)
	if err != nil {
		return err
	}
	wipeConfig := len(cfgNames) > 0 || cfg.DefaultProfile != ""
	if wipeConfig {
		a.printConfigResetPlan(cfgNames, cfg.DefaultProfile)
	}

	// Nothing to do at all — no account and no config — is a friendly no-op.
	if !hasPlan && !wipeConfig {
		fmt.Fprintln(a.Out, theme.Dim.Render("Nothing to reset (no agent account, no jentic CLI config)."))
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

	// Execute: tear the account down, then wipe the operator's own config LAST so a
	// failure tearing down the account never leaves the operator without the config
	// that records what still needs cleaning. The whole-slate "reset" confirmation
	// authorises the teardown, but NOT home deletion — that stays a separate, opt-in
	// decision (the same bar as a named reset), so a full reset preserves the agent
	// home unless the operator explicitly asks otherwise.
	if hasPlan {
		deleteHome := opts.deleteHome
		if !opts.force && interactive && plan.homeDir != "" {
			accepted, err := a.confirmDeleteHome(plan.homeDir)
			if err != nil {
				return err
			}
			deleteHome = accepted
		}
		if err := a.execAccountReset(a.Paths, cfg, plan, deleteHome); err != nil {
			return err
		}
	}
	if wipeConfig {
		if err := a.execConfigWipe(cfgNames, cfg); err != nil {
			return err
		}
	}
	return nil
}

// resetProfile removes a single profile — operator-owned (in ~/.jentic/profiles)
// or agent-owned (in the shared account's home). It never touches the account,
// its grants, or its other profiles, with one exception: if the profile is the
// LAST agent-owned profile, reset offers to also tear the whole account down (the
// account exists only to run profiles, so an empty one is dead weight).
func (a *App) resetProfile(ctx context.Context, cfg *config.FileConfig, opts *resetOptions, interactive bool, operator, operatorHome, name string) error {
	// A profile name that collides with a known agent binary id is almost always a
	// mistake ("jentic reset claude" meaning the account) — say so rather than
	// silently trying to delete a profile literally named "claude".
	ref, found, err := a.findProfileRef(cfg, name)
	if err != nil {
		return err
	}
	if !found {
		if _, isAgent := localagent.Lookup(name); isAgent {
			return fmt.Errorf("no profile %q. To tear down the whole agent account, run `jentic reset` "+
				"with no argument (there is one shared account for every agent)", name)
		}
		return fmt.Errorf("no profile %q (nothing to reset); run `jentic profile list` to see profiles", name)
	}

	// Is this the last agent-owned profile? If so we may offer full account teardown.
	acct, hasAcct := cfg.AgentAccount()
	lastAgentProfile := false
	if ref.owned() && hasAcct && acct.ConfigDir != "" {
		agentNames, lerr := profile.List(config.Paths{Root: acct.ConfigDir})
		if lerr == nil && len(agentNames) == 1 && agentNames[0] == name {
			lastAgentProfile = true
		}
	}

	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Error.Render("⚠  jentic reset will PERMANENTLY remove profile '"+name+"'"+
		ownerTag(ref)+" (key, tokens, registration). This cannot be undone."))
	fmt.Fprintln(a.Out)

	// Confirm the profile removal (typed name; --force skips).
	if !opts.force {
		if !interactive {
			return errors.New("refusing to reset non-interactively without --force (no safe default for destruction)")
		}
		ok, cerr := a.confirmResetName(name)
		if cerr != nil {
			return cerr
		}
		if !ok {
			fmt.Fprintln(a.Out, theme.Dim.Render("Aborted — nothing was changed."))
			return nil
		}
	}

	// Optionally tear the whole account down when this was its last profile.
	tearDownAccount := false
	if lastAgentProfile {
		if opts.force {
			// Non-interactive: a lone --force removes just the profile; pair with
			// nothing else, so leave the (now-empty) account in place unless the
			// operator runs a full `jentic reset`.
			tearDownAccount = false
		} else if interactive {
			accepted, aerr := a.confirmTeardownLastProfile()
			if aerr != nil {
				return aerr
			}
			tearDownAccount = accepted
		}
	}

	if err := a.execProfileRemoval(ctx, cfg, ref, acct); err != nil {
		return err
	}
	fmt.Fprintln(a.Out, theme.Successf("Removed profile %q.", name))

	if tearDownAccount {
		plan := surveyReset(ctx, operator, operatorHome, acct)
		a.printResetPlan(plan)
		deleteHome := opts.deleteHome
		if !opts.force && interactive && plan.homeDir != "" {
			accepted, herr := a.confirmDeleteHome(plan.homeDir)
			if herr != nil {
				return herr
			}
			deleteHome = accepted
		}
		return a.execAccountReset(a.Paths, cfg, plan, deleteHome)
	}
	return nil
}

// ownerTag renders " (agent)" for an agent-owned profile, "" otherwise, for
// inclusion in a plan line.
func ownerTag(ref profileRef) string {
	if ref.owned() {
		return " (agent)"
	}
	return ""
}

// execProfileRemoval deletes one profile's on-disk directory. An operator-owned
// profile is removed directly (the operator owns it); an agent-owned one is
// removed with sudo (the files are owned by the agent uid). When the removed
// profile was the account's checked-out one, the agent-home default_profile
// pointer is cleared so nothing references a profile that no longer exists.
func (a *App) execProfileRemoval(_ context.Context, cfg *config.FileConfig, ref profileRef, acct config.AgentAccount) error {
	if ref.owned() {
		c := localagent.RemoveAgentProfileCmd(acct.ConfigDir, ref.name)
		c.Stdout, c.Stderr = a.Out, a.Err
		if err := c.Run(); err != nil {
			return fmt.Errorf("remove agent profile %q: %w", ref.name, err)
		}
		// Clear the checked-out pointer if it named this profile.
		agentPaths := config.Paths{Root: acct.ConfigDir}
		agentCfg, err := config.Load(agentPaths)
		if err == nil && agentCfg.DefaultProfile == ref.name {
			if serr := config.SetDefaultProfile(agentPaths, ""); serr != nil {
				fmt.Fprintln(a.Out, theme.Warnf("could not clear the checked-out profile pointer: %v", serr))
			}
		}
		return nil
	}
	p, err := profile.Open(a.Paths, ref.name)
	if err != nil {
		return err
	}
	if err := p.Delete(); err != nil {
		return err
	}
	// Clear the operator's default_profile if it named this profile.
	if cfg.DefaultProfile == ref.name {
		cfg.DefaultProfile = ""
		if err := cfg.Save(a.Paths); err != nil {
			return err
		}
	}
	return nil
}

// confirmTeardownLastProfile offers full account teardown after removing the last
// agent-owned profile. Declining leaves the (now-empty) account in place.
func (a *App) confirmTeardownLastProfile() (bool, error) {
	fmt.Fprintln(a.Out, theme.Dim.Render(
		"That was the agent account's last profile. You can also tear down the whole "+
			"account now (Unix user, ACLs, sudoers, home)."))
	var tear bool
	if err := install.RunConfirm(huh.NewConfirm().
		Title("Tear down the agent account too?").
		Description("Removes the dedicated Unix user and all its grants. Requires sudo.").
		Affirmative("Yes, tear it down").
		Negative("No, keep the account").
		Value(&tear)); err != nil {
		if errors.Is(err, huh.ErrUserAborted) {
			return false, nil
		}
		return false, err
	}
	return tear, nil
}

// confirmFullReset is the single whole-slate acknowledgement for a bare
// `jentic reset`: type "reset" to tear down every agent and wipe the operator's
// own config. It replaces the per-agent typed name and the separate "reset
// config" prompt, since the operator has already been shown every plan above.
func (a *App) confirmFullReset() (bool, error) {
	var typed string
	if err := install.NewForm(huh.NewGroup(
		install.Input().
			Title("Type 'reset' to tear down everything above, or anything else to abort").
			Value(&typed),
	)).Run(); err != nil {
		if errors.Is(err, huh.ErrUserAborted) {
			return false, nil
		}
		return false, err
	}
	return strings.TrimSpace(typed) == "reset", nil
}

// execConfigWipe deletes every profile under ~/.jentic/profiles and clears the
// default_profile pointer in config.yaml — the operator's OWN jentic CLI identity.
// It runs only on a full `jentic reset` (no named agent), and entirely as the
// operator (reset is never launched under sudo), so it can only ever touch the
// invoking account's ~/.jentic — never another user's. The plan was already shown
// and confirmed by the caller; this is the execution tail only. Local deletion is
// the whole job — we deliberately do NOT try to revoke tokens server-side (the
// tokens are typically expired, so revocation just prints 401 noise; and deleting
// the local key/tokens already severs this machine's access).
func (a *App) execConfigWipe(names []string, cfg *config.FileConfig) error {
	for _, name := range names {
		p, err := profile.Open(a.Paths, name)
		if err != nil {
			return err
		}
		if err := p.Delete(); err != nil {
			return err
		}
		fmt.Fprintln(a.Out, theme.Infof("• removed profile %q", name))
	}

	// Clear the default_profile pointer so the config no longer references a
	// profile that no longer exists.
	if cfg.DefaultProfile != "" {
		cfg.DefaultProfile = ""
		if err := cfg.Save(a.Paths); err != nil {
			return err
		}
	}

	fmt.Fprintln(a.Out, theme.Successf("Your jentic CLI config was reset (%d profile(s) removed).", len(names)))
	return nil
}

// printConfigResetPlan shows exactly which profiles will be deleted and that the
// default pointer will be cleared, as part of the full-reset preview.
func (a *App) printConfigResetPlan(names []string, defaultProfile string) {
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Error.Render("⚠  DANGER ZONE — a full reset will PERMANENTLY remove YOUR OWN jentic CLI "+
		"identity from this account's ~/.jentic. This cannot be undone."))
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Warn.Render("  Profiles to delete (key, tokens, registration metadata):"))
	if len(names) == 0 {
		fmt.Fprintln(a.Out, theme.Dim.Render("    - none"))
	}
	for _, name := range names {
		fmt.Fprintln(a.Out, theme.Dim.Render("    - "+name))
	}
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Warn.Render("  Config to clear:"))
	if defaultProfile != "" {
		fmt.Fprintln(a.Out, theme.Dim.Render("    - default_profile ('"+defaultProfile+"') in config.yaml"))
	} else {
		fmt.Fprintln(a.Out, theme.Dim.Render("    - default_profile (already unset)"))
	}
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Success.Render("  NOT touched:"))
	fmt.Fprintln(a.Out, theme.Dim.Render("    - another user's config — this only affects the account you ran reset from"))
	fmt.Fprintln(a.Out, theme.Dim.Render("    - installed skills, and Jentic One itself"))
	fmt.Fprintln(a.Out)
}

// execAccountReset runs the privileged teardown steps for the already-surveyed,
// already-confirmed agent account and clears its config record last. It is the
// shared execution tail for both a full `jentic reset` and the optional
// account-teardown offered after removing the last agent-owned profile.
func (a *App) execAccountReset(paths config.Paths, cfg *config.FileConfig, plan resetPlan, deleteHome bool) error {
	// Act. Steps run in a fixed order (ACLs → home → sudoers → account); a failure
	// stops the run with the config entry still recorded so a re-run can finish.
	// A best-effort step (settling the agent home) is the exception: a macOS home
	// carries SIP/TCC-protected template files that nobody can chown or remove, so
	// its re-own/delete legitimately exits non-zero after handling everything else.
	// We report that and press on to the account deletion rather than abort.
	for _, step := range buildResetSteps(plan, deleteHome) {
		fmt.Fprintln(a.Out, theme.Infof("• %s", step.What))
		c := step.Cmd
		c.Stdout = a.Out
		// Best-effort steps produce expected per-file stderr (SIP/TCC "Operation not
		// permitted", or `chmod -a` reporting an ACE already absent); we summarise it
		// ourselves, so swallow the raw diagnostics rather than flood the terminal.
		if step.BestEffort {
			c.Stderr = io.Discard
		} else {
			c.Stderr = a.Err
		}
		if err := c.Run(); err != nil {
			if step.BestEffort {
				// Expected residual non-zero exits: SIP/TCC-protected home-template
				// files nobody can chown (re-own/delete home), and macOS `chmod -a`
				// reporting the ACE already absent on some subtree entries (leaf
				// revoke). Neither leaves the teardown incomplete.
				fmt.Fprintln(a.Out, theme.Dim.Render(fmt.Sprintf(
					"  (%s: some entries couldn't be changed — that's expected; continuing)", step.What)))
				continue
			}
			return fmt.Errorf("%s: %w", step.What, err)
		}
	}

	// Clear the account record LAST, so a mid-way failure above leaves the record
	// of what still needs cleaning for the next run.
	cfg.ClearAgentAccount()
	if err := cfg.Save(paths); err != nil {
		return err
	}
	fmt.Fprintln(a.Out, theme.Successf("Reset complete for the agent account (user %q).", plan.user))
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

// resetPlan is the fully-resolved teardown for the agent account, built by
// surveyReset and rendered/executed without further disk probing of the config.
type resetPlan struct {
	user     string
	homeDir  string
	operator string
	// configDir is the agent's own ~/.jentic (the reference-model home of its
	// platform identity). Removed during teardown even when the home is kept, so a
	// later re-bootstrap can't resurrect a torn-down registration from it.
	configDir     string
	acls          []aclRemoval
	accountExists bool
}

// surveyReset resolves the teardown plan for the agent account from two sources:
// the recorded account (user, home, granted dirs) AND a live re-scan of the
// on-disk ACLs, so grants that drifted from the config are still caught. Nothing
// is modified. Leaf grants come from GrantedDirs; the ancestor traverse grants
// are recomputed by walking each granted dir's chain up to the operator's home
// (deduped) and checking which still carry the agent's ACL.
func surveyReset(ctx context.Context, operator, operatorHome string, acct config.AgentAccount) resetPlan {
	agentUser := acct.User
	if agentUser == "" {
		agentUser = localagent.DefaultUserName(operator)
	}

	var acls []aclRemoval
	seenTraverse := map[string]bool{}

	// A dir that is itself a leaf grant must never also be treated as a traverse
	// ancestor of a deeper leaf grant (e.g. `.../workspace` granted directly and
	// also on the chain to `.../workspace/Github/repo`). It carries the rwx leaf
	// ACE, not a bare execute ACE, so the leaf revoke below removes it — issuing a
	// traverse revoke (`chmod -a … allow execute`) on the same dir would error
	// "Entry not found" and abort the teardown.
	leafDirs := make(map[string]bool, len(acct.GrantedDirs))
	for _, dir := range acct.GrantedDirs {
		leafDirs[dir] = true
	}

	for _, dir := range acct.GrantedDirs {
		acls = append(acls, aclRemoval{
			traverse: false,
			dir:      dir,
			present:  localagent.AgentACLPresent(ctx, agentUser, dir),
		})
		// Ancestor traverse grants: walk home→leaf and record each ancestor that
		// still carries the agent's execute ACL. Deduped across all granted dirs,
		// and skipping any ancestor that is itself a leaf grant (handled above).
		if operatorHome != "" && localagent.IsUnderHome(operatorHome, dir) {
			for _, anc := range localagent.AncestorChain(operatorHome, dir) {
				if seenTraverse[anc] || leafDirs[anc] {
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
		user:          agentUser,
		homeDir:       acct.HomeDir,
		operator:      operator,
		configDir:     acct.ConfigDir,
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
// before it. The config-record removal is NOT a step here: execAccountReset does
// it last in Go, after every step below has succeeded.
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
			// Best-effort: the leaf revoke recurses (find ! -type l -exec chmod
			// -a) over the whole granted subtree, and macOS `chmod -a` exits
			// non-zero per entry that doesn't carry the exact ACE — "Entry not
			// found" on inherited-only children, "No ACL present" on files with no
			// ACL. Those are benign (the ACE is already absent there); the net
			// effect is the entry removed everywhere it existed, so a residual
			// non-zero exit must not abort the teardown before the account is gone.
			BestEffort: true,
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
			// Best-effort, for the same reason the leaf revoke is: the ancestor may
			// no longer carry the exact execute-only ACE (drifted off disk, or a
			// deeper grant re-shaped it), and macOS `chmod -a` exits non-zero on a
			// missing entry. That residual is benign — the ACE is already absent —
			// and must not abort the teardown before the account is gone.
			BestEffort: true,
		})
	}

	// (1c) Remove the agent's OWN jentic identity dir (its ~/.jentic) before the
	// home is settled. This is the reference-model home of the agent's platform
	// identity — registration, tokens, signing key. It must go even when the home
	// is KEPT, or a later `jentic bootstrap` that reuses the same home would find
	// the torn-down (now-archived) registration and try to re-use it. When the home
	// is being deleted the rm below covers it too, but running it here is harmless
	// and keeps the "identity gone" guarantee independent of the home disposition.
	if plan.configDir != "" && !deleteHome {
		steps = append(steps, localagent.AccountStep{
			What: "remove the agent's jentic identity " + plan.configDir,
			Cmd:  localagent.RemoveAgentIdentityCmd(plan.configDir),
		})
	}

	// (2) Settle the home: delete only on explicit acceptance, else re-own it to
	// the operator so it survives the account deletion and stays readable.
	if plan.homeDir != "" {
		if deleteHome {
			steps = append(steps, localagent.AccountStep{
				What:       "delete the agent's home " + plan.homeDir,
				Cmd:        localagent.DeleteHomeCmd(plan.homeDir),
				BestEffort: true,
			})
		} else {
			steps = append(steps, localagent.AccountStep{
				What:       "re-own the agent's home to " + plan.operator,
				Cmd:        localagent.ReownHomeCmd(plan.operator, plan.homeDir),
				BestEffort: true,
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
// follows as a typed acknowledgement.
func (a *App) printResetPlan(plan resetPlan) {
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Error.Render("⚠  DANGER ZONE — jentic reset will PERMANENTLY remove the following for the "+
		"agent account (user "+plan.user+"). This cannot be undone."))

	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Warn.Render("  Directory ACLs to remove (agent access granted by jentic run):"))
	found := false
	for _, acl := range plan.acls {
		if !acl.present {
			continue
		}
		found = true
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
	if !found {
		fmt.Fprintln(a.Out, theme.Dim.Render("    - none found on disk"))
	}

	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Warn.Render("  Files & config to remove:"))
	fmt.Fprintln(a.Out, theme.Dim.Render("    - sudoers drop-in        /etc/sudoers.d/jentic-agent (if present)"))
	fmt.Fprintln(a.Out, theme.Dim.Render("    - agent_account entry    in your config"))
	if plan.configDir != "" {
		fmt.Fprintln(a.Out, theme.Dim.Render("    - agent identity         "+plan.configDir+" (its registration, tokens, key)"))
	}

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
	fmt.Fprintln(a.Out, theme.Dim.Render("    - your own home's permissions — reset only drops the agent's named-user ACLs"))
	fmt.Fprintln(a.Out, theme.Dim.Render("    - your own files, config, keys, and Jentic One itself"))
	fmt.Fprintln(a.Out)
}

// confirmResetName requires the operator to type the profile name to proceed —
// the same bar as a dangerous directory grant. Anything else aborts.
func (a *App) confirmResetName(name string) (bool, error) {
	var typed string
	if err := install.NewForm(huh.NewGroup(
		install.Input().
			Title("Type the profile name ('" + name + "') to confirm, or anything else to abort").
			Value(&typed),
	)).Run(); err != nil {
		if errors.Is(err, huh.ErrUserAborted) {
			return false, nil
		}
		return false, err
	}
	return strings.TrimSpace(typed) == name, nil
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
