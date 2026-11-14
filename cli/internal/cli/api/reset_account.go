package api

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os/user"
	"path/filepath"
	"strings"

	"github.com/charmbracelet/huh"

	"github.com/jentic/jentic-one/cli/internal/cli/prompt"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/localagent"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

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
// already-confirmed agent account and removes its record last.
func (a *app) execAccountReset(paths config.Paths, plan resetPlan, deleteHome bool) error {
	// Re-validate every operator-editable path the teardown is about to hand to a
	// privileged command BEFORE the first one runs. The steps do `rm -rf` on the
	// identity dirs under <homeDir>, chown/`rm -rf <homeDir>`, and ACL edits on
	// each grant dir; a hand-edited record that pointed home_dir at a system tree
	// would otherwise make reset a privileged wipe of the wrong directory. Failing
	// closed here leaves the record intact so nothing is touched.
	if err := localagent.ValidateAgentUser(plan.user); err != nil {
		return fmt.Errorf("refusing to reset: recorded agent user is invalid: %w", err)
	}
	// homeDir may be empty on a legacy account recorded before it was tracked
	// (buildResetSteps skips the steps that use it); validate only what is set.
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
	for _, acl := range plan.acls {
		if err := localagent.ValidateGrantPath(acl.dir); err != nil {
			return fmt.Errorf("refusing to reset: recorded grant path is invalid: %w", err)
		}
	}

	// Act. Steps run in a fixed order (processes → ACLs → home → sudoers →
	// account); a failure stops the run with the config entry still recorded so a
	// re-run can finish. Every removal path is checked again after its command is
	// built and immediately before it runs, including the elevated fallback.
	for _, step := range buildResetSteps(plan, deleteHome) {
		fmt.Fprintln(a.Out, theme.Infof("• %s", step.What))
		c := step.Cmd
		run := func() error {
			c.Stdout = a.Out
			// Best-effort steps produce expected per-file stderr (SIP/TCC "Operation
			// not permitted", or `chmod -a` reporting an ACE already absent); we
			// summarise it ourselves rather than flood the terminal.
			if step.BestEffort {
				c.Stderr = io.Discard
			} else {
				c.Stderr = a.Err
			}
			if step.NoSymlinkHome != "" {
				for _, path := range step.NoSymlinkPaths {
					if err := localagent.AssertNoSymlinkComponents(step.NoSymlinkHome, path); err != nil {
						return fmt.Errorf("removal path failed its safety check: %w", err)
					}
				}
			}
			return c.Run()
		}

		err := run()
		if err != nil && step.FallbackCmd != nil {
			fmt.Fprintln(a.Out, theme.Warn.Render(
				"  (agent-privileged removal did not complete; retrying component-checked paths with elevated privileges)"))
			c = step.FallbackCmd
			err = run()
		}
		if err != nil {
			if step.BestEffort {
				fmt.Fprintln(a.Out, theme.Dim.Render(fmt.Sprintf(
					"  (%s: some operations couldn't be completed; continuing)", step.What)))
				continue
			}
			return fmt.Errorf("%s: %w", step.What, err)
		}
	}

	// Remove the account record LAST, so a mid-way failure above leaves the
	// record of what still needs cleaning for the next run. This also clears any
	// legacy copy still sitting in ~/.jentic/config.yaml.
	if _, err := config.MutateAgentState(paths, func(st *config.AgentState) error {
		st.ClearAgentAccount()
		return nil
	}); err != nil {
		return err
	}
	fmt.Fprintln(a.Out, theme.Successf("Reset complete for the agent account (user %q).", plan.user))
	if !deleteHome && plan.homeDir != "" {
		fmt.Fprintln(a.Out, theme.Dim.Render("  The agent's home was kept and re-owned to you: "+plan.homeDir))
		fmt.Fprintln(a.Out, theme.Dim.Render("  Its jentic identity (keys, tokens) and any seeded agent/provider config "+
			"(e.g. ~/.claude, ~/.aws, ~/.codex) were cleared from it; re-run with --delete-home to remove the whole home."))
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

// resetSkippedPath records a removal candidate omitted from the plan.
type resetSkippedPath struct {
	path   string
	reason string
}

// resetPlan is the fully-resolved teardown for the agent account, built by
// surveyReset and rendered/executed without further disk probing of the record.
type resetPlan struct {
	user          string
	homeDir       string
	operator      string
	acls          []aclRemoval
	accountExists bool
	skipped       []resetSkippedPath
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

	plan := resetPlan{
		user:          agentUser,
		homeDir:       acct.HomeDir,
		operator:      operator,
		acls:          acls,
		accountExists: localagent.UserExists(ctx, agentUser),
	}
	_, plan.skipped = filterResetRemovalPaths(plan.homeDir, resetRemovalPaths(plan.homeDir))
	return plan
}

// resetRemovalPaths lists every identity and seeded-config path reset can
// remove independently of the whole home.
func resetRemovalPaths(home string) []string {
	if home == "" {
		return nil
	}
	paths := localagent.AgentIdentityDirs(home)
	return append(paths, localagent.SeededConfigDirs(home)...)
}

// filterResetRemovalPaths refuses paths that cannot be inspected safely or that
// cross an intermediate symlink. Missing paths remain in the removal list so rm
// can treat them as a no-op.
func filterResetRemovalPaths(home string, paths []string) (accepted []string, skipped []resetSkippedPath) {
	for _, path := range paths {
		err := localagent.AssertNoSymlinkComponents(home, path)
		if err == nil {
			accepted = append(accepted, path)
			continue
		}
		reason := "path check failed: " + err.Error()
		if errors.Is(err, localagent.ErrSymlinkedPath) {
			reason = "symlinked path component"
		}
		skipped = append(skipped, resetSkippedPath{path: path, reason: reason})
	}
	return accepted, skipped
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

	// (0) Stop processes that could mutate the agent-owned tree during teardown.
	if plan.accountExists {
		if kill := localagent.KillAgentProcessesCmd(plan.user); kill != nil {
			steps = append(steps, localagent.AccountStep{
				What:       "terminate all processes for agent account " + plan.user,
				Cmd:        kill,
				BestEffort: true,
			})
		}
	}

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

	// (1c) Remove the agent's OWN jentic identity dirs — the exported XDG store
	// (<home>/.config/jentic with the signing key, <home>/.local/state/jentic
	// with the tokens) plus the legacy <home>/.jentic — before the home is
	// settled. This is where the agent's platform identity lives: registration,
	// tokens, signing key. It must go even when the home is KEPT, or the
	// credential material would survive the teardown in the now-operator-owned
	// home and a later `jentic setup` that reuses the same home could
	// resurrect the torn-down registration. When the home is being deleted the
	// rm below covers it, so this only runs on keep.
	if plan.homeDir != "" && !deleteHome {
		paths, _ := filterResetRemovalPaths(plan.homeDir, localagent.AgentIdentityDirs(plan.homeDir))
		rootCmd := localagent.RemoveAgentIdentityCmd(paths)
		if rootCmd != nil {
			cmd := localagent.RemoveAsAgentCmd(plan.user, paths)
			fallback := rootCmd
			if !plan.accountExists {
				cmd = rootCmd
				fallback = nil
			}
			if cmd != nil {
				steps = append(steps, localagent.AccountStep{
					What:           "remove the agent's jentic identity (keys, tokens) from " + plan.homeDir,
					Cmd:            cmd,
					FallbackCmd:    fallback,
					NoSymlinkHome:  plan.homeDir,
					NoSymlinkPaths: paths,
				})
			}
		}
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
		paths, _ := filterResetRemovalPaths(plan.homeDir, localagent.SeededConfigDirs(plan.homeDir))
		rootCmd := localagent.ScrubSeededConfigCmd(paths)
		if rootCmd != nil {
			cmd := localagent.RemoveAsAgentCmd(plan.user, paths)
			fallback := rootCmd
			if !plan.accountExists {
				cmd = rootCmd
				fallback = nil
			}
			if cmd != nil {
				steps = append(steps, localagent.AccountStep{
					What:           "remove seeded agent/provider config from the kept home",
					Cmd:            cmd,
					BestEffort:     true,
					FallbackCmd:    fallback,
					NoSymlinkHome:  plan.homeDir,
					NoSymlinkPaths: paths,
				})
			}
		}
	}

	// (2) Settle the home: delete only on explicit acceptance, else re-own it to
	// the operator so it survives the account deletion and stays readable.
	if plan.homeDir != "" {
		if deleteHome {
			steps = append(steps, localagent.AccountStep{
				What:           "delete the agent's home " + plan.homeDir,
				Cmd:            localagent.DeleteHomeCmd(plan.homeDir),
				BestEffort:     true,
				NoSymlinkHome:  filepath.Dir(plan.homeDir),
				NoSymlinkPaths: []string{plan.homeDir},
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
	if plan.accountExists {
		fmt.Fprintln(a.Out, theme.Warn.Render("  Process termination: sudo pkill -KILL -u "+plan.user+
			" runs before any filesystem step and ends all live agent sessions."))
	}

	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Warn.Render("  Directory ACLs to remove (agent access granted to the account):"))
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
	fmt.Fprintln(a.Out, theme.Dim.Render("    - agent account record   ~/.config/jentic/agent-account.yaml (and any legacy copy)"))
	if plan.homeDir != "" {
		skipped := make(map[string]bool, len(plan.skipped))
		for _, skip := range plan.skipped {
			skipped[skip.path] = true
		}
		for _, dir := range localagent.AgentIdentityDirs(plan.homeDir) {
			if !skipped[dir] {
				fmt.Fprintln(a.Out, theme.Dim.Render("    - agent identity         "+dir+" (its registration, tokens, key)"))
			}
		}
		for _, skip := range plan.skipped {
			fmt.Fprintln(a.Out, theme.Dim.Render("    - skipped: "+skip.reason+"  "+skip.path))
		}
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
		fmt.Fprintln(a.Out, theme.Dim.Render("      with its jentic identity and any seeded agent/provider config (~/.claude, ~/.aws, ~/.codex, …) cleared from it."))
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
