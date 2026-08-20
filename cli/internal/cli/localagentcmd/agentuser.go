package localagentcmd

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os/user"
	"time"

	"github.com/charmbracelet/huh"
	"github.com/jentic/jentic-one/cli/internal/cli/prompt"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/localagent"
	"github.com/jentic/jentic-one/cli/internal/skillgen"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

// agentUserFields are the editable, prefilled values the operator confirms in
// the account-setup dialog. Name/home seed the OS account; the two port toggles
// drive config/provider seeding (see seedPrefs) without a second prompt.
type agentUserFields struct {
	name         string
	homeDir      string
	portConfig   bool
	portProvider bool
	// passwordless, when true, installs a scoped sudoers rule so the operator can
	// launch the agent (become its Unix user) without re-entering their password.
	passwordless bool
}

// agentSetup is the outcome of the agent-user step, returned to the caller
// (setupE) so it can target the platform registration correctly and offer
// to start a session in the new account's home.
type agentSetup struct {
	// created reports whether a dedicated Unix account now backs the agent.
	created bool
	// agentID is the operator identifier (e.g. "claude"), so the caller can offer
	// `jentic run <agentID>` without re-deriving it.
	agentID string
	// agentUser is the OS account name (set whether or not created — it is the
	// derived default when the operator declined).
	agentUser string
	// homeDir is the created agent's home directory (empty unless created), used to
	// offer starting a session there.
	homeDir string
}

// setupAgentUser is the shared agent-user-account step folded into both
// `jenticctl wizard` and `jentic setup`, right after the operator is
// selected. It mirrors how skills are shared (setup → chooseAdapters):
// wizard delegates to setup, so wiring it into setupE lands it in the
// wizard too.
//
// The flow is deliberately sudo-last: the "create an account? (requires sudo)"
// gate is asked BEFORE any privileged command runs, so an operator who declines
// never triggers a password prompt. Declining is recorded (AccountCreated=false)
// because that choice shapes later behaviour; opting in creates the dedicated
// Unix user — the true credential boundary between the agent and the operator's
// secrets — then seeds config per the operator's toggles.
//
// operators is the list of selected operator names (from chooseAdapters); we act
// on the first one that maps to a known local coding agent (claude, codex,
// cursor, or hermes — the runnable entries in localagent.Registry).
func (a *Cmd) setupAgentUser(ctx context.Context, operators []string, interactive bool) (agentSetup, error) {
	agentID, desc, ok := firstKnownAgent(operators)
	if !ok {
		// None of the selected operators is a launchable local agent (e.g. only
		// generic/codex skills) — nothing to isolate here.
		return agentSetup{}, nil
	}

	operator := currentOperator()
	defaultName := localagent.DefaultUserName(operator)

	// Non-interactive (--yes / no TTY): never trigger sudo unattended. Skip, but
	// leave a discoverable pointer and record that no account was created.
	if !interactive {
		a.recordAgentAccount(defaultName, "", false)
		fmt.Fprintln(a.Out, theme.Dim.Render(fmt.Sprintf(
			"Skipping agent-user isolation (non-interactive). Create it later with `jentic run %s`.", agentID)))
		return agentSetup{agentID: agentID, agentUser: defaultName}, nil
	}

	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Step.Render("Isolate your local agent"))
	fmt.Fprintln(a.Out, theme.Dim.Render(
		"A dedicated OS user is the true boundary between the agent and your credentials:"))
	fmt.Fprintln(a.Out, theme.Dim.Render(
		"running as its own user, the agent can't read your keys, browser session, or the"))
	fmt.Fprintln(a.Out, theme.Dim.Render(
		"jentic-one credential store — the same-user install can't promise that."))

	create := true
	if err := prompt.RunConfirm(huh.NewConfirm().
		Title("Create a dedicated user account for your local agent? (requires sudo)").
		Description("Recommended. You'll be asked for your password to create the account. Decline to keep running same-user.").
		Affirmative("Yes, isolate it").
		Negative("Not now").
		Value(&create)); err != nil {
		return agentSetup{}, err
	}
	if !create {
		a.recordAgentAccount(defaultName, "", false)
		fmt.Fprintln(a.Out, theme.Dim.Render(fmt.Sprintf(
			"Keeping same-user. You can isolate later with `jentic run %s`.", agentID)))
		return agentSetup{agentID: agentID, agentUser: defaultName}, nil
	}

	// The operator opted into isolation, so the machine must be able to confine the
	// agent's sessions BEFORE any privileged account-creation runs — a created
	// account that can never be launched under confinement is a dead end. If a
	// prerequisite is missing we stop the account-creation path here with the exact
	// install command, then offer to continue same-user now rather than force a full
	// re-run. Either branch records AccountCreated=false and returns cleanly, so the
	// missing dependency never blocks the rest of setup (identity, skills).
	if missing := localagent.MissingPrereqs(); len(missing) > 0 {
		return a.agentUserPrereqGate(agentID, defaultName, missing)
	}

	// Resolve WHICH operator files each toggle would port, so the dialog can show
	// the operator exactly what will be copied instead of a generic prompt. Both
	// are resolved from the operator's home up front (the same sources the seeding
	// step uses). A toggle defaults to Yes only when there is actually something to
	// port — an empty list defaults to No and says "none found".
	operatorHome := localagent.OperatorHome()
	configSrcs := localagent.ExistingConfigPaths(operatorHome, desc)
	provider := localagent.DetectProvider(operatorHome)
	providerSrcs := localagent.ProviderConfigPaths(operatorHome, provider)

	// Editable, prefilled dialog: account name, home, the two port toggles, and
	// the passwordless-launch consent (defaults to yes — see the form).
	fields := agentUserFields{
		name:         defaultName,
		homeDir:      localagent.DefaultHomeDir(defaultName),
		portConfig:   len(configSrcs) > 0,
		portProvider: len(providerSrcs) > 0,
		passwordless: true,
	}
	if err := a.promptAgentUserFields(&fields, configSrcs, provider.Name, providerSrcs); err != nil {
		return agentSetup{}, err
	}

	// The home field was prefilled from the DEFAULT account name; if the operator
	// renamed the account but left the home at that stale default, follow the name
	// rather than silently keeping a directory derived from a name they rejected —
	// which, when the default-named account already exists, is that OTHER account's
	// live home (see HomeClaimedBy for the guard behind this convenience).
	if home, changed := followRenamedHome(fields.name, defaultName, fields.homeDir); changed {
		fields.homeDir = home
		fmt.Fprintln(a.Out, theme.Dim.Render(
			"Agent home follows the account name: "+fields.homeDir))
	}

	if err := a.createAgentAccount(ctx, operator, fields); err != nil {
		return agentSetup{}, err
	}

	// The privileged half (Unix user, home, ACLs, sudoers) now exists, so RECORD the
	// account immediately — before the best-effort seeding below. Seeding can fail (a
	// provider-config copy hiccup, a home-lookup race) and recording only afterwards
	// would leave a fully-provisioned account with NO state entry: invisible residue
	// that `jentic reset` — which keys off the recorded account — could never tear
	// down. Recording first makes every created account reclaimable even if a later
	// step errors. The agent's own jentic identity lives in its home's XDG store
	// (exported per-launch by `jentic run`), so the operator's state need only
	// record the account name and home.
	a.recordAgentAccount(fields.name, fields.homeDir, true)

	// Seed config/provider per the operator's toggles — best-effort, since the account
	// is already usable without it (the operator can re-seed on `jentic run`) and a
	// copy failure must neither abort setup nor un-record the account above.
	a.seedAgentConfig(ctx, fields, desc)

	// Offer to bring the operator's trusted workspaces (from the agent's own config)
	// over to the new agent in one step, rather than making them re-grant each
	// project by hand on first `jentic run`. Best-effort: a discovery/grant hiccup
	// must not block the setup the operator came for.
	a.offerWorkspaceGrants(ctx, desc, agentID, fields.name)

	a.printAgentRunInstructions(agentID, fields.homeDir)
	return agentSetup{
		created:   true,
		agentID:   agentID,
		agentUser: fields.name,
		homeDir:   fields.homeDir,
	}, nil
}

// agentUserPrereqGate handles the "operator wants isolation but the machine is
// missing a prerequisite" case. It prints each missing dependency with the exact
// install command, then offers — inline — to continue same-user right now so the
// operator need not re-run the whole flow just to proceed. The alternative it
// spells out is to install the dependency and re-run for full agent isolation.
//
// It never runs a package manager itself (install commands are printed, not
// executed) and never returns a hard error for the missing dependency: whichever
// branch the operator picks, the account is recorded as not-created and setup
// hands back cleanly so the rest of setup continues.
func (a *Cmd) agentUserPrereqGate(agentID, defaultName string, missing []localagent.Prereq) (agentSetup, error) {
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Warnf(
		"Can't isolate the agent yet — this machine is missing what full agent isolation needs:"))
	for _, p := range missing {
		fmt.Fprintln(a.Out, theme.Dim.Render("  • "+p.Reason))
		if p.Hint != "" {
			fmt.Fprintln(a.Out, theme.Dim.Render("    "+p.Hint))
		}
	}
	fmt.Fprintln(a.Out, theme.Dim.Render(
		"Install the above and re-run to isolate the agent, or continue same-user now."))

	// Record the declined-by-necessity account up front so that even an aborted
	// prompt (Ctrl-C) leaves the same consistent not-created state as an explicit
	// "no".
	a.recordAgentAccount(defaultName, "", false)

	sameUser := true
	if err := prompt.RunConfirm(huh.NewConfirm().
		Title("Continue same-user for now?").
		Description("You can install the prerequisites later and re-run to isolate the agent.").
		Affirmative("Yes, continue same-user").
		Negative("No, I'll install and re-run").
		Value(&sameUser)); err != nil {
		if errors.Is(err, huh.ErrUserAborted) {
			// Aborting the follow-up prompt is not a failure of the step; treat it
			// like declining to continue and exit the isolation path cleanly.
			return agentSetup{agentID: agentID, agentUser: defaultName}, nil
		}
		return agentSetup{}, err
	}

	// Either choice continues setup same-user (the account was not created);
	// only the parting message differs so the operator's intent is reflected back.
	if sameUser {
		fmt.Fprintln(a.Out, theme.Dim.Render(fmt.Sprintf(
			"Keeping same-user. Once the prerequisites are installed, isolate with `jentic run %s`.", agentID)))
	} else {
		fmt.Fprintln(a.Out, theme.Dim.Render(
			"Install the prerequisites above, then re-run to isolate the agent."))
	}
	return agentSetup{agentID: agentID, agentUser: defaultName}, nil
}

// createAgentAccount runs the privileged account-creation recipe (idempotently):
// it creates (or safely reuses) the agent's Unix account and home, re-owns the home
// to the agent, grants the operator recursive access into it, and installs the
// optional passwordless-launch sudoers rule. Config/provider seeding is NOT done
// here — the caller records the account first, then seeds best-effort (see
// seedAgentConfig), so a seeding failure can't leave an unrecorded, un-reclaimable
// account behind.
func (a *Cmd) createAgentAccount(ctx context.Context, operator string, fields agentUserFields) error {
	// Re-validate at the point of use, not just in the form: a hand-edited
	// config.yaml or a future non-form caller could otherwise thread an unsafe
	// name/home into the privileged commands built below (sudo runas, sudoers rule,
	// ACL entries, --home). This is the choke point every privileged step is behind.
	if err := localagent.ValidateAccount(fields.name, fields.homeDir); err != nil {
		return err
	}
	if err := localagent.ValidateAgentUser(operator); err != nil {
		return fmt.Errorf("operator name %q is not a valid Unix username: %w", operator, err)
	}

	reused := localagent.UserExists(ctx, fields.name)
	if reused {
		// The name resolves to a PRE-EXISTING OS account. Only reuse it if it is
		// genuinely a jentic-managed agent account — its live home must be the
		// managed home under /opt or /Users/Shared. Otherwise the name has collided
		// with a real human or system account, and the reclaim/chown/grant steps
		// below would re-own and widen ACLs on that person's actual home. Fail closed.
		if err := localagent.VerifyManagedHome(fields.name, fields.homeDir); err != nil {
			return fmt.Errorf("refusing to reuse existing account %q: %w", fields.name, err)
		}
		fmt.Fprintln(a.Out, theme.Dim.Render(fmt.Sprintf("Account %q already exists — reusing it.", fields.name)))
	} else {
		// A NEW account must not be pointed at a home some OTHER existing account
		// already claims (typically another agent account's live home, left in the
		// form's prefill after the operator renamed the account): the reclaim/grant
		// steps below would chown that account's home wholesale to this one.
		// VerifyManagedHome only guards the reuse branch, so check here too.
		if claimant := localagent.HomeClaimedBy(ctx, fields.name, fields.homeDir); claimant != "" {
			return fmt.Errorf("home directory %s already belongs to existing account %q — "+
				"pick this account's own home (e.g. %s), or reuse that account by naming it %q",
				fields.homeDir, claimant, localagent.DefaultHomeDir(fields.name), claimant)
		}
		fmt.Fprintln(a.Out, theme.Infof("Creating agent account %q (home %s) ...", fields.name, fields.homeDir))
		for _, step := range localagent.CreateAccountCmds(operator, fields.name, fields.homeDir) {
			c := step.Cmd
			c.Stdout = a.Out
			// A best-effort step's stderr is expected noise (per-file "Operation not
			// permitted" on SIP/TCC-protected home-template files nobody can ACL); we
			// summarise it ourselves, so swallow the raw diagnostics.
			if step.BestEffort {
				c.Stderr = io.Discard
			} else {
				c.Stderr = a.Err
			}
			if err := c.Run(); err != nil {
				if step.BestEffort {
					fmt.Fprintln(a.Out, theme.Dim.Render(fmt.Sprintf(
						"  (%s: some protected system files couldn't be changed — that's expected; continuing)", step.What)))
					continue
				}
				return fmt.Errorf("%s: %w", step.What, err)
			}
		}
		// Fail closed on a create the OS refused: sysadminctl can reject the add
		// (e.g. a Directory Services record conflict) while still EXITING 0, so the
		// step loop above sails through with no account behind it. Without this
		// check every later step degrades into best-effort "unknown user" noise,
		// the account is recorded as created, and `jentic run` sends the operator
		// back to setup in a loop. Trust the account database, not exit codes.
		if !localagent.UserExists(ctx, fields.name) {
			return fmt.Errorf("agent account %q was not created — the account tool reported success "+
				"but the account does not exist (see its messages above; a conflicting Directory "+
				"Services record is the usual cause). Resolve the conflict or pick a different "+
				"account name and re-run", fields.name)
		}
	}

	// Make sure the agent owns its whole home. This is a no-op on a freshly created
	// home (createhomedir already made the agent the owner) but is load-bearing when
	// the home ALREADY EXISTS: a prior `jentic reset` that kept the home re-owned it
	// to the operator, and reusing that home would otherwise leave .claude/.aws/etc.
	// operator-owned — readable but not writable by the agent, which surfaces as
	// fresh-config screens, provider token-cache failures, and EACCES transcript
	// writes. Best-effort: a macOS home carries SIP/TCC-protected files nobody can
	// chown, so a residual non-zero exit is expected and must not fail setup.
	if fields.homeDir != "" {
		reclaim := localagent.ReclaimAgentHomeCmd(fields.name, fields.homeDir)
		reclaim.Stdout, reclaim.Stderr = a.Out, io.Discard // raw chown errors are expected; we summarise below
		if err := reclaim.Run(); err != nil {
			fmt.Fprintln(a.Out, theme.Dim.Render(
				"  (some protected system files in the home couldn't be re-owned to the agent — that's expected; continuing)"))
		}

		// On the reuse path CreateAccountCmds did NOT run, so the operator's inherited
		// grant on the agent home is whatever it was before — possibly a stale, too-
		// narrow ACE (an older build granted the macOS "write" shorthand, which on a
		// directory omits add_subdirectory, so the operator can create files but not
		// `mkdir <home>/.jentic` when writing the identity below). Re-apply the correct
		// grant idempotently; it is additive, so widening never removes anything.
		if reused {
			grant := localagent.GrantOperatorHomeCmd(operator, fields.homeDir)
			grant.Stdout, grant.Stderr = a.Out, io.Discard // raw find/chmod errors are expected; we summarise below
			if err := grant.Run(); err != nil {
				// Same best-effort rationale as CreateAccountCmds: the recursive
				// grant exits non-zero on SIP/TCC-protected home-template files
				// nobody can ACL. The home root and the agent's real content are
				// stamped regardless, so this must not fail reuse.
				fmt.Fprintln(a.Out, theme.Dim.Render(
					"  (some protected system files in the home couldn't be granted to you — that's expected; continuing)"))
			}
		}
	}

	// We no longer force the operator's home to `chmod 700`. In-home confidentiality
	// against the agent is now enforced per session by the process-confinement layer
	// (`jentic run` launches under sandbox-exec/bwrap and errors closed if that is
	// unavailable), which also closes the sibling-traversal leak a blanket 700 could
	// not. Real secrets keep their own 0700 modes regardless; the sensitivity rules
	// (DangerReason) still gate what may be granted. See
	// docs/security/local-agent/sandbox-exec-plan.md.

	// Optional passwordless launch (per the operator's consent toggle): a scoped
	// sudoers rule so `jentic run` need not prompt for the operator's password to
	// become the agent user. Idempotent and visudo-validated, so re-running setup
	// or reusing an account simply re-asserts the one rule. `jentic reset` removes
	// it (RemoveSudoersCmd). Best-effort: a sudoers write failure must not abort an
	// otherwise-successful account setup — the operator just keeps typing their
	// password, which we tell them.
	if fields.passwordless {
		grant := localagent.InstallSudoersCmd(operator, fields.name)
		grant.Stdout, grant.Stderr = a.Out, io.Discard
		if err := grant.Run(); err != nil {
			fmt.Fprintln(a.Out, theme.Warnf(
				"could not enable passwordless launch — you'll be asked for your password on `jentic run`: %v", err))
		} else {
			fmt.Fprintln(a.Out, theme.Dim.Render(
				"Passwordless launch enabled (scoped to becoming the agent user, never root)."))
		}
	} else if reused {
		// The toggle is authoritative BOTH ways: on a REUSE run where the operator
		// now declines passwordless, remove any rule an earlier run installed so a
		// stale NOPASSWD drop-in can't outlive the operator's current choice. Only
		// on reuse — a freshly created account never had a rule installed, so we
		// skip the (no-op) privileged call and its sudo prompt there.
		// Best-effort and idempotent — a no-op when no rule is present.
		revoke := localagent.RemoveSudoersCmd(fields.name)
		revoke.Stdout, revoke.Stderr = a.Out, io.Discard
		if err := revoke.Run(); err != nil {
			fmt.Fprintln(a.Out, theme.Warnf(
				"could not remove a previously-installed passwordless-launch rule: %v", err))
		}
	}

	return nil
}

// seedAgentConfig ports the operator's agent and provider config into the agent's
// home per the field toggles — the same porting logic `jentic run` uses. It runs
// AFTER the account has been recorded and is deliberately best-effort: the account
// is fully usable without seeding (the operator can re-seed later on `jentic run`),
// so a copy failure is warned about, not fatal, and must never un-record or block
// the setup the operator came for. The field bools drive the decision directly, so
// there is no second prompt; the per-copy warnings still print.
func (a *Cmd) seedAgentConfig(ctx context.Context, fields agentUserFields, desc localagent.Descriptor) {
	prefs := seedPrefs{forceSeed: fields.portConfig, interactive: false}
	if err := a.ensureAgentConfig(ctx, prefs, fields.name, desc); err != nil {
		fmt.Fprintln(a.Out, theme.Warnf(
			"could not seed the agent config (the account is set up; re-seed later with `jentic run`): %v", err))
	}
	provPrefs := seedPrefs{forceSeed: fields.portProvider, interactive: false}
	if err := a.ensureProviderConfig(ctx, provPrefs, fields.name); err != nil {
		fmt.Fprintln(a.Out, theme.Warnf(
			"could not seed the provider config (the account is set up; re-seed later with `jentic run`): %v", err))
	}
}

// recordAgentAccount persists the single shared agent-account entry, including
// the AccountCreated/Enabled booleans the rest of the CLI keys off. A fresh entry
// is stamped with CreatedAt; an existing one keeps its original stamp and its
// recorded grants. Enabled tracks AccountCreated: creating the account enables it,
// and recording a declined (created=false) account leaves it disabled. New
// records deliberately carry no config_dir — the legacy per-agent ~/.jentic is
// dead; the agent's identity is exported into its home's XDG store per launch.
func (a *Cmd) recordAgentAccount(userName, homeDir string, created bool) {
	// Route through MutateAgentState: it reloads under the state lock before
	// applying, so a concurrent grant that appended a dir to GrantedDirs isn't
	// clobbered by this record write (which preserves whatever grants the
	// reloaded state carries).
	if _, err := config.MutateAgentState(a.Paths, func(st *config.AgentState) error {
		acct, existed := st.AgentAccount()
		acct.User = userName
		acct.AccountCreated = created
		acct.Enabled = created
		if homeDir != "" {
			acct.HomeDir = homeDir
		}
		if !existed || acct.CreatedAt == "" {
			acct.CreatedAt = time.Now().UTC().Format(time.RFC3339)
		}
		st.SetAgentAccount(acct)
		return nil
	}); err != nil {
		fmt.Fprintln(a.Out, theme.Warnf("could not save the agent account: %v", err))
	}
}

// targetAdapters projects the resolved skill targets to their adapters. The
// skill-selection flow moved from a flat []skillgen.Adapter to []skillTarget
// (adapter + placement scope); the agent-user step only cares about the operator
// behind each target, so it unwraps the adapters here and reuses operatorNames.
func targetAdapters(targets []skillTarget) []skillgen.Adapter {
	adapters := make([]skillgen.Adapter, 0, len(targets))
	for _, t := range targets {
		adapters = append(adapters, t.adapter)
	}
	return adapters
}

// operatorNames projects the selected skill adapters to their operator names,
// the vocabulary localagent.Lookup understands (e.g. "claude").
func operatorNames(adapters []skillgen.Adapter) []string {
	names := make([]string, 0, len(adapters))
	for _, ad := range adapters {
		names = append(names, string(ad.Operator()))
	}
	return names
}

// followRenamedHome re-derives the agent home when the operator renamed the
// account in the setup form but left the home at the DEFAULT name's prefill:
// the two fields are edited independently, so the stale prefill would silently
// point the new account at a directory derived from a name the operator
// rejected — which, when the default-named account already exists, is that
// other account's live home. Returns the home to use and whether it changed;
// a deliberately customised home (anything but the exact stale prefill) is
// always kept.
func followRenamedHome(name, defaultName, homeDir string) (string, bool) {
	if name != defaultName && homeDir == localagent.DefaultHomeDir(defaultName) {
		return localagent.DefaultHomeDir(name), true
	}
	return homeDir, false
}

// firstKnownAgent returns the first selected operator that maps to a known local
// coding agent (localagent.Registry), so the account-setup step only fires for
// operators jentic can actually launch as their own user.
func firstKnownAgent(operators []string) (string, localagent.Descriptor, bool) {
	for _, op := range operators {
		if desc, ok := localagent.Lookup(op); ok {
			return op, desc, true
		}
	}
	return "", localagent.Descriptor{}, false
}

// currentOperator returns the current login user's name, or "user" as a last
// resort (matching resolveAgentUser's fallback).
func currentOperator() string {
	if u, err := user.Current(); err == nil && u.Username != "" {
		return u.Username
	}
	return "user"
}
