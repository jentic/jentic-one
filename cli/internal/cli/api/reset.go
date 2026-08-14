package api

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/user"
	"path/filepath"
	"strings"

	"github.com/charmbracelet/huh"
	"github.com/charmbracelet/x/term"
	"github.com/spf13/cobra"

	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/cli/prompt"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/localagent"
	"github.com/jentic/jentic-one/cli/internal/theme"
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

func newResetCmd(app *app) *cobra.Command {
	opts := &resetOptions{}
	cmd := &cobra.Command{
		Use:   "reset",
		Short: "Tear down the agent account and wipe this machine's jentic identity state",
		Long: "reset is the inverse of the agent-user setup folded into `jentic bootstrap`,\n" +
			"and the clean-slate hatch for this machine's jentic identity state.\n\n" +
			"It tears down the agent account — the dedicated Unix user, the named-user\n" +
			"ACLs stamped across your home (both the leaf read/write grants and the\n" +
			"execute-only ancestor traverse grants), and the passwordless-launch sudoers\n" +
			"drop-in — and then also wipes your OWN jentic identity state: the store\n" +
			"(contexts, environments, identities, keys, tokens under ~/.config/jentic and\n" +
			"~/.local/state/jentic) and any legacy V1 profile tree under ~/.jentic. The\n" +
			"account's home is PRESERVED by default (re-owned to you); deleting it takes\n" +
			"a separate, explicit confirmation.\n\n" +
			"To remove a single context/identity instead of everything, use\n" +
			"`jentic context delete` / `jentic identity delete`.\n\n" +
			"Deleting the account and stripping ACLs are privileged, so reset requires\n" +
			"sudo to complete: run it as yourself and you'll be prompted for your\n" +
			"password when it reaches the privileged steps. It shows the full plan before\n" +
			"touching anything. It only ever removes the agent's own named-user ACLs and\n" +
			"never touches another user's files.",
		Example: "  jentic reset                              # full clean slate: the account + your identity state\n" +
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
	// so a single password prompt is triggered when it reaches them — the same
	// sudo-last posture as agent-user creation. We flag that up front so the
	// prompt isn't a surprise, mirroring the "(requires sudo)" account gate.
	operator, operatorHome := resetOperator()
	cfg, err := config.Load(a.Paths)
	if err != nil {
		return err
	}

	interactive := term.IsTerminal(os.Stdin.Fd())
	return a.resetAll(ctx, cfg, opts, interactive, operator, operatorHome)
}

// resetAll is the full clean slate: it tears down the shared agent account (Unix
// user, ACLs, sudoers, home disposition) and then wipes the operator's OWN
// jentic identity state — the XDG store and any legacy V1 tree. Everything is
// previewed first, then a single "reset" confirmation authorises the lot.
func (a *app) resetAll(ctx context.Context, cfg *config.FileConfig, opts *resetOptions, interactive bool, operator, operatorHome string) error {
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

	// Preview the operator's own identity-state wipe alongside the account plan.
	wipe := surveyIdentityWipe(a.Paths)
	if wipe.any() {
		a.printIdentityWipePlan(wipe)
	}

	// Nothing to do at all — no account and no identity state — is a friendly no-op.
	if !hasPlan && !wipe.any() {
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
	if wipe.any() {
		if err := a.execIdentityWipe(wipe); err != nil {
			return err
		}
	}
	return nil
}

// identityWipe is the resolved plan for wiping the operator's own jentic
// identity state: the XDG trees and (if still present) the legacy V1 tree.
// Directories are recorded only when they exist, so the plan is truthful.
type identityWipe struct {
	// configDir is the config tree (~/.config/jentic): config.yaml + keys/.
	configDir string
	// stateDir is the state tree (~/.local/state/jentic): tokens + API keys.
	stateDir string
	// legacyRoot is the V1 ~/.jentic profile tree (profiles/ + MIGRATED marker).
	// Only the identity material is wiped — the rest of ~/.jentic is jenticctl's
	// install root and reset must not touch it.
	legacyProfilesDir string
	legacyMarker      string
}

func (w identityWipe) any() bool {
	return w.configDir != "" || w.stateDir != "" || w.legacyProfilesDir != ""
}

// surveyIdentityWipe resolves which identity trees exist on this machine. It
// never errors: an unresolvable dir simply isn't wiped (and was never readable
// by the CLI either).
func surveyIdentityWipe(paths config.Paths) identityWipe {
	var w identityWipe
	if dir, err := sdkconfig.ConfigDir(); err == nil {
		if _, serr := os.Stat(dir); serr == nil {
			w.configDir = dir
		}
	}
	if dir, err := sdkconfig.StateDir(); err == nil {
		if _, serr := os.Stat(dir); serr == nil {
			w.stateDir = dir
		}
	}
	if dir := paths.ProfilesDir(); dir != "" {
		if _, serr := os.Stat(dir); serr == nil {
			w.legacyProfilesDir = dir
		}
	}
	if marker := filepath.Join(paths.Dir(), "MIGRATED"); marker != "" {
		if _, serr := os.Stat(marker); serr == nil {
			w.legacyMarker = marker
		}
	}
	return w
}

// execIdentityWipe deletes the surveyed identity trees. It runs entirely as the
// operator (reset is never launched under sudo), so it can only ever touch the
// invoking account's own dirs — never another user's. The plan was already shown
// and confirmed by the caller; this is the execution tail only. Local deletion is
// the whole job — we deliberately do NOT try to revoke tokens server-side (the
// tokens are typically expired, so revocation just prints 401 noise; and deleting
// the local key/tokens already severs this machine's access).
func (a *app) execIdentityWipe(w identityWipe) error {
	for _, dir := range []string{w.configDir, w.stateDir, w.legacyProfilesDir} {
		if dir == "" {
			continue
		}
		if err := os.RemoveAll(dir); err != nil {
			return fmt.Errorf("removing %s: %w", dir, err)
		}
		fmt.Fprintln(a.Out, theme.Infof("• removed %s", dir))
	}
	// Drop the MIGRATED marker with the legacy profiles: a marker without a
	// store is stale, and a future V1 tree (downgrade) should gate again.
	if w.legacyMarker != "" {
		if err := os.Remove(w.legacyMarker); err != nil && !os.IsNotExist(err) {
			return fmt.Errorf("removing %s: %w", w.legacyMarker, err)
		}
	}
	fmt.Fprintln(a.Out, theme.Successf("Your jentic identity state was reset."))
	return nil
}

// printIdentityWipePlan shows exactly which identity trees will be deleted, as
// part of the full-reset preview.
func (a *app) printIdentityWipePlan(w identityWipe) {
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Error.Render("⚠  DANGER ZONE — a full reset will PERMANENTLY remove YOUR OWN jentic "+
		"identity state from this account. This cannot be undone."))
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Warn.Render("  Identity state to delete (contexts, environments, identities, keys, tokens):"))
	for _, line := range []struct{ label, dir string }{
		{"config ", w.configDir},
		{"state  ", w.stateDir},
		{"legacy ", w.legacyProfilesDir},
	} {
		if line.dir == "" {
			continue
		}
		fmt.Fprintln(a.Out, theme.Dim.Render("    - "+line.label+" "+line.dir))
	}
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Success.Render("  NOT touched:"))
	fmt.Fprintln(a.Out, theme.Dim.Render("    - another user's config — this only affects the account you ran reset from"))
	fmt.Fprintln(a.Out, theme.Dim.Render("    - the jentic-one install itself (~/.jentic install root, data, logs)"))
	fmt.Fprintln(a.Out)
}

// confirmFullReset is the single whole-slate acknowledgement for `jentic
// reset`: type "reset" to tear down the agent account and wipe the operator's
// own identity state. The operator has already been shown every plan above.
func (a *app) confirmFullReset() (bool, error) {
	var typed string
	if err := prompt.NewForm(huh.NewGroup(
		prompt.Input().
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

// execAccountReset runs the privileged teardown steps for the already-surveyed,
// already-confirmed agent account and clears its config record last. It is the
// shared execution tail for both a full `jentic reset` and the optional
// account-teardown offered after removing the last agent-owned profile.
func (a *app) execAccountReset(paths config.Paths, plan resetPlan, deleteHome bool) error {
	// Re-validate every operator-editable path the teardown is about to hand to a
	// privileged command BEFORE the first one runs. The steps do `rm -rf
	// <configDir>`, chown/`rm -rf <homeDir>`, and ACL edits on each grant dir; a
	// hand-edited config.yaml that pointed home_dir/config_dir at a system tree
	// would otherwise make reset a privileged wipe of the wrong directory. Failing
	// closed here leaves the record intact so nothing is touched.
	if err := localagent.ValidateAgentUser(plan.user); err != nil {
		return fmt.Errorf("refusing to reset: recorded agent user is invalid: %w", err)
	}
	// homeDir/configDir may be empty on a legacy account recorded before they were
	// tracked (buildResetSteps skips the steps that use them); validate only what is
	// set. But if EITHER is set, both must be — and the config dir must be the home's
	// own .jentic — so a partial or mismatched record can't reach `rm -rf`.
	if plan.homeDir != "" {
		if err := localagent.ValidateHomeDir(plan.homeDir); err != nil {
			return fmt.Errorf("refusing to reset: %w", err)
		}
		// If the Unix account still exists, its LIVE home must match the recorded
		// home before we reown/delete it — a hand-edited config whose home_dir has
		// drifted from the account's real home, or a name that has since been
		// reassigned to a different account, would otherwise make reset chown or
		// `rm -rf` the wrong directory. When the account is already gone we cannot
		// look it up; the ValidateHomeDir check above (managed root) then stands
		// alone for reowning the orphaned home.
		if plan.accountExists {
			if err := localagent.VerifyManagedHome(plan.user, plan.homeDir); err != nil {
				return fmt.Errorf("refusing to reset: %w", err)
			}
		}
	}
	if plan.configDir != "" {
		if err := localagent.ValidateConfigDir(plan.homeDir, plan.configDir); err != nil {
			return fmt.Errorf("refusing to reset: %w", err)
		}
	}
	for _, acl := range plan.acls {
		if err := localagent.ValidateGrantPath(acl.dir); err != nil {
			return fmt.Errorf("refusing to reset: recorded grant path is invalid: %w", err)
		}
	}

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
	// of what still needs cleaning for the next run. Under the config lock (Mutate)
	// so it can't race a concurrent grant write.
	if _, err := config.Mutate(paths, func(c *config.FileConfig) error {
		c.ClearAgentAccount()
		return nil
	}); err != nil {
		return err
	}
	fmt.Fprintln(a.Out, theme.Successf("Reset complete for the agent account (user %q).", plan.user))
	if !deleteHome && plan.homeDir != "" {
		fmt.Fprintln(a.Out, theme.Dim.Render("  The agent's home was kept and re-owned to you: "+plan.homeDir))
		fmt.Fprintln(a.Out, theme.Dim.Render("  Any seeded agent/provider config (e.g. ~/.claude, ~/.aws, ~/.codex) was cleared from it; "+
			"re-run with --delete-home to remove the whole home."))
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

	// (1d) When the home is KEPT (re-owned to the operator), clear the agent/
	// provider config dirs from it. These are where seeding copies the operator's
	// credentials (~/.claude.json's key, ~/.aws, ~/.codex/auth.json), so a live
	// secret the operator handed the agent must not survive the teardown in the
	// now-operator-owned home. We clear the whole set for every known operator, not
	// just the one used — the account is being torn down, so a clean home is the
	// intent, and provenance (seeded vs agent-created) isn't tracked. When the home
	// is being deleted the rm below covers all of this, so this only runs on keep.
	if plan.homeDir != "" && !deleteHome {
		if scrub := localagent.ScrubSeededConfigCmd(localagent.SeededConfigDirs(plan.homeDir)); scrub != nil {
			steps = append(steps, localagent.AccountStep{
				What:       "remove seeded agent/provider config from the kept home",
				Cmd:        scrub,
				BestEffort: true,
			})
		}
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
func (a *app) printResetPlan(plan resetPlan) {
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
		fmt.Fprintln(a.Out, theme.Dim.Render("      Default: KEPT on disk and re-owned to you ("+plan.operator+"),"))
		fmt.Fprintln(a.Out, theme.Dim.Render("      with any seeded agent/provider config (~/.claude, ~/.aws, ~/.codex, …) cleared from it."))
		fmt.Fprintln(a.Out, theme.Dim.Render("      You'll be asked separately whether to delete the home entirely."))
	}

	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Success.Render("  NOT touched:"))
	fmt.Fprintln(a.Out, theme.Dim.Render("    - your own home's permissions — reset only drops the agent's named-user ACLs"))
	fmt.Fprintln(a.Out, theme.Dim.Render("    - your own files, config, keys, and Jentic One itself"))
	fmt.Fprintln(a.Out)
}

// confirmDeleteHome is the separate, explicit acceptance for deleting the agent's
// home. Preserve is the default: only an exact "delete home" opts into deletion.
func (a *app) confirmDeleteHome(homeDir string) (bool, error) {
	fmt.Fprintln(a.Out, theme.Warnf("  The agent's home %s will be KEPT and re-owned to you.", homeDir))
	var typed string
	if err := prompt.NewForm(huh.NewGroup(
		prompt.Input().
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
