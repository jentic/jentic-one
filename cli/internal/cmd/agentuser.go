package cmd

import (
	"context"
	"fmt"
	"os/user"
	"time"

	"github.com/charmbracelet/huh"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/install"
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
}

// agentSetup is the outcome of the agent-user step, returned to the caller
// (bootstrapE) so it can target the platform registration correctly. When an
// account was created, the agent's jentic identity must be written into the
// agent's own config dir (the single source of truth) rather than the operator's,
// so the caller redirects bootstrapIdentity there and hands the dir to the agent.
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
	// configDir is the agent's ~/.jentic, where its identity is written and owned.
	// Empty unless created.
	configDir string
}

// setupAgentUser is the shared agent-user-account step folded into both
// `jenticctl wizard` and `jentic bootstrap`, right after the operator is
// selected. It mirrors how skills are shared (bootstrap → chooseAdapters):
// wizard delegates to bootstrap, so wiring it into bootstrapE lands it in the
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
// on the first one that maps to a known local coding agent (today: claude).
func (a *App) setupAgentUser(ctx context.Context, operators []string, interactive bool) (agentSetup, error) {
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
		a.recordAgentAccount(agentID, defaultName, desc.Binary, "", "", false)
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
	if err := install.RunConfirm(huh.NewConfirm().
		Title("Create a dedicated user account for your local agent? (requires sudo)").
		Description("Recommended. You'll be asked for your password to create the account. Decline to keep running same-user.").
		Affirmative("Yes, isolate it").
		Negative("Not now").
		Value(&create)); err != nil {
		return agentSetup{}, err
	}
	if !create {
		a.recordAgentAccount(agentID, defaultName, desc.Binary, "", "", false)
		fmt.Fprintln(a.Out, theme.Dim.Render(fmt.Sprintf(
			"Keeping same-user. You can isolate later with `jentic run %s`.", agentID)))
		return agentSetup{agentID: agentID, agentUser: defaultName}, nil
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

	// Editable, prefilled dialog: account name, home, and the two port toggles.
	fields := agentUserFields{
		name:         defaultName,
		homeDir:      localagent.DefaultHomeDir(defaultName),
		portConfig:   len(configSrcs) > 0,
		portProvider: len(providerSrcs) > 0,
	}
	if err := a.promptAgentUserFields(&fields, configSrcs, provider.Name, providerSrcs); err != nil {
		return agentSetup{}, err
	}

	if err := a.createAgentAccount(ctx, operator, fields, desc); err != nil {
		return agentSetup{}, err
	}

	// The agent's own jentic config lives in its home — this is where the platform
	// identity is written and owned (see agentSetup / ConfigDir), so the operator's
	// config need only reference it.
	configDir := localagent.AgentConfigDir(fields.homeDir)
	a.recordAgentAccount(agentID, fields.name, desc.Binary, fields.homeDir, configDir, true)
	a.printAgentRunInstructions(agentID, fields.homeDir)
	return agentSetup{
		created:   true,
		agentID:   agentID,
		agentUser: fields.name,
		homeDir:   fields.homeDir,
		configDir: configDir,
	}, nil
}

// createAgentAccount runs the privileged account-creation recipe (idempotently),
// locks the operator's own home, and seeds config/provider per the field toggles.
func (a *App) createAgentAccount(ctx context.Context, operator string, fields agentUserFields, desc localagent.Descriptor) error {
	if localagent.UserExists(ctx, fields.name) {
		fmt.Fprintln(a.Out, theme.Dim.Render(fmt.Sprintf("Account %q already exists — reusing it.", fields.name)))
	} else {
		fmt.Fprintln(a.Out, theme.Infof("Creating agent account %q (home %s) ...", fields.name, fields.homeDir))
		for _, step := range localagent.CreateAccountCmds(operator, fields.name, fields.homeDir) {
			c := step.Cmd
			c.Stdout, c.Stderr = a.Out, a.Err
			if err := c.Run(); err != nil {
				return fmt.Errorf("%s: %w", step.What, err)
			}
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
		reclaim.Stdout, reclaim.Stderr = a.Out, a.Err
		if err := reclaim.Run(); err != nil {
			fmt.Fprintln(a.Out, theme.Dim.Render(
				"  (some protected system files in the home couldn't be re-owned to the agent — that's expected; continuing)"))
		}
	}

	// Lock the operator's own home — the machine-independent isolation guarantee.
	// It is unprivileged (the operator owns it) and idempotent, so run it always.
	if home := localagent.OperatorHome(); home != "" {
		lock := localagent.LockOperatorHomeCmd(home)
		lock.Stdout, lock.Stderr = a.Out, a.Err
		if err := lock.Run(); err != nil {
			return fmt.Errorf("lock the operator's home (chmod 700): %w", err)
		}
	}

	// Seed config/provider per the operator's toggles — the same porting logic
	// `jentic run` uses. The field bools drive the decision directly, so there is
	// no second prompt; the warnings still print after each copy.
	prefs := seedPrefs{forceSeed: fields.portConfig, interactive: false}
	if err := a.ensureAgentConfig(ctx, prefs, fields.name, desc); err != nil {
		return err
	}
	provPrefs := seedPrefs{forceSeed: fields.portProvider, interactive: false}
	return a.ensureProviderConfig(ctx, provPrefs, fields.name)
}

// recordAgentAccount persists the local-agent entry, including the AccountCreated
// boolean the rest of the CLI keys off. A fresh entry is stamped with CreatedAt;
// an existing one keeps its original stamp.
func (a *App) recordAgentAccount(agentID, userName, binary, homeDir, configDir string, created bool) {
	cfg, err := config.Load(a.Paths)
	if err != nil {
		fmt.Fprintln(a.Out, theme.Warnf("could not record the agent account: %v", err))
		return
	}
	entry, existed := cfg.LocalAgent(agentID)
	entry.User = userName
	entry.Binary = binary
	entry.AccountCreated = created
	if homeDir != "" {
		entry.HomeDir = homeDir
	}
	if configDir != "" {
		entry.ConfigDir = configDir
	}
	if !existed || entry.CreatedAt == "" {
		entry.CreatedAt = time.Now().UTC().Format(time.RFC3339)
	}
	cfg.SetLocalAgent(agentID, entry)
	if err := cfg.Save(a.Paths); err != nil {
		fmt.Fprintln(a.Out, theme.Warnf("could not save the agent account: %v", err))
	}
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
