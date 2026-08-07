package cmdcore

import (
	"context"
	"fmt"
	"strings"

	"github.com/charmbracelet/huh"
	"github.com/jentic/jentic-one/cli/internal/cli/prompt"
	"github.com/jentic/jentic-one/cli/internal/localagent"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

// seedPrefs is the resolved decision inputs for config/provider seeding, decoupled
// from any one command's flag struct so the same seeding logic can be driven by
// `jentic run` and by the shared agent-user setup flow (wizard + bootstrap).
//
//   - forceSeed short-circuits to "copy" (the --seed-config escape hatch).
//   - forceNoSeed short-circuits to "skip" (the --no-seed-config escape hatch).
//   - interactive says whether prompting is possible (a real TTY, not --yes/CI).
//     When false the safe default is NOT to copy, since these files can carry
//     provider credentials.
type seedPrefs struct {
	forceSeed   bool
	forceNoSeed bool
	interactive bool
}

// seedPrefs projects run's flags onto the shared seedPrefs, mirroring how
// bootstrap projects its flags onto skillOptions. wantsInteractive already folds
// in --yes and the TTY check.
func (o *runOptions) seedPrefs(cmd *cobra.Command) seedPrefs {
	return seedPrefs{
		forceSeed:   o.seedConfig,
		forceNoSeed: o.noSeedConfig,
		interactive: WantsInteractive(cmd, o.yes),
	}
}

// ── config seeding ───────────────────────────────────────────────────────────

// ensureAgentConfig offers to copy the operator's agent configuration (e.g.
// ~/.claude, ~/.claude.json) into the agent's home, so the agent inherits the
// operator's settings. It only acts when the operator has such config, the
// agent doesn't already have its own, and the operator opts in — a compromised
// agent must not be able to trick a re-run into overwriting its state, and the
// operator must consciously accept that these files can carry provider secrets.
func (a *App) ensureAgentConfig(ctx context.Context, prefs seedPrefs, agentUser string, desc localagent.Descriptor) error {
	if prefs.forceNoSeed || len(desc.ConfigPaths) == 0 {
		return nil
	}
	home := localagent.OperatorHome()
	srcs := localagent.ExistingConfigPaths(home, desc)
	srcs = a.safeSeedSources(home, srcs)
	if len(srcs) == 0 {
		return nil // operator has nothing to seed
	}
	if localagent.AgentHasConfig(ctx, agentUser, desc) {
		return nil // agent already has its own config — don't clobber it
	}

	if !a.decideSeedConfig(prefs, srcs) {
		return nil
	}

	fmt.Fprintln(a.Out, theme.Infof("Seeding %s's %s config into %s ...", desc.ID, desc.Binary, agentUser))
	agentHome, err := localagent.LookupHomeDir(agentUser)
	if err != nil {
		return err
	}
	c := localagent.CopyConfigCmd(agentUser, agentHome, home, srcs)
	c.Stdout, c.Stderr = a.Out, a.Err
	if err := c.Run(); err != nil {
		return fmt.Errorf("seed agent config: %w", err)
	}
	if err := a.scrubSeededSecrets(agentHome, desc); err != nil {
		return err
	}
	fmt.Fprintln(a.Out, theme.Dim.Render("  These are the operator's settings; the agent still authenticates as itself on first launch."))
	a.printProviderSecretWarning()
	return nil
}

// scrubSeededSecrets deletes the operator's discrete credential files (e.g.
// Codex's auth.json, Hermes's .env) that the config copy just placed in the
// agent's home, so the agent inherits the operator's SETTINGS but not their raw
// keys — it authenticates as itself. Claude has no such file (its key is embedded
// in the config the agent needs), so this is a no-op there.
func (a *App) scrubSeededSecrets(agentHome string, desc localagent.Descriptor) error {
	scrub := localagent.ScrubSecretsCmd(localagent.ExpandedSecretPaths(agentHome, desc))
	if scrub == nil {
		return nil
	}
	scrub.Stdout, scrub.Stderr = a.Out, a.Err
	if err := scrub.Run(); err != nil {
		return fmt.Errorf("scrub seeded %s secrets: %w", desc.ID, err)
	}
	fmt.Fprintln(a.Out, theme.Dim.Render("  Removed the operator's saved API credentials from the seeded config; the agent authenticates as itself."))
	return nil
}

// safeSeedSources keeps only the seed sources whose real path resolves under the
// operator's home and warns about any it drops. A source that is (or contains a
// top-level) symlink escaping the home — e.g. a `~/.aws` linked to /etc, or a
// GOOGLE_APPLICATION_CREDENTIALS pointing at an arbitrary absolute path — is
// skipped rather than copied, so the root-run copy never pulls a file from
// outside the operator's own home into the agent's.
func (a *App) safeSeedSources(operatorHome string, srcs []string) []string {
	safe, skipped := localagent.SafeSeedSources(operatorHome, srcs)
	for _, s := range skipped {
		fmt.Fprintln(a.Out, theme.Warnf("Skipping %s: it resolves outside your home directory and won't be copied to the agent.", s))
	}
	return safe
}

// printProviderSecretWarning is the shared caveat shown after seeding either the
// agent config or the provider config: until the operator fronts the provider
// with an LLM proxy, the provider credentials live in the agent's environment.
func (a *App) printProviderSecretWarning() {
	fmt.Fprintln(a.Out, theme.Dim.Render("  Note: until you front your provider with an LLM proxy, its credentials live in the"))
	fmt.Fprintln(a.Out, theme.Dim.Render("  agent's environment. A proxy (e.g. LiteLLM — https://docs.litellm.ai/) keeps the keys"))
	fmt.Fprintln(a.Out, theme.Dim.Render("  out of the agent account."))
}

// ── provider config seeding ──────────────────────────────────────────────────

// ensureProviderConfig detects which LLM provider the operator's Claude Code
// setup authenticates against (from ~/.claude/settings.json) and, when that
// provider keeps its config in the operator's home (e.g. ~/.aws for Bedrock),
// offers to copy that config into the agent's home so the agent can reach the
// same provider. Only the config is copied — Claude Code performs any SSO login
// programmatically, so cached tokens are deliberately left behind. As with the
// agent config it acts only when the operator has such config, the agent doesn't
// already have it, and the operator opts in.
func (a *App) ensureProviderConfig(ctx context.Context, prefs seedPrefs, agentUser string) error {
	if prefs.forceNoSeed {
		return nil
	}
	home := localagent.OperatorHome()
	pc := localagent.DetectProvider(home)
	if len(pc.ConfigPaths) == 0 {
		return nil // Anthropic default (or unknown) — nothing separate to seed
	}
	srcs := localagent.ProviderConfigPaths(home, pc)
	srcs = a.safeSeedSources(home, srcs)
	if len(srcs) == 0 {
		return nil // provider selected but its config isn't on disk
	}
	if localagent.AgentHasPaths(ctx, agentUser, srcs) {
		return nil // agent already has this provider config
	}

	if !a.decideSeedProviderConfig(prefs, pc, srcs) {
		return nil
	}

	fmt.Fprintln(a.Out, theme.Infof("Seeding %s provider config into %s ...", pc.Name, agentUser))
	agentHome, err := localagent.LookupHomeDir(agentUser)
	if err != nil {
		return err
	}
	c := localagent.CopyConfigCmd(agentUser, agentHome, home, srcs)
	c.Stdout, c.Stderr = a.Out, a.Err
	if err := c.Run(); err != nil {
		return fmt.Errorf("seed provider config: %w", err)
	}
	fmt.Fprintln(a.Out, theme.Dim.Render("  Copied config only; the agent performs any provider login itself on first launch."))
	a.printProviderSecretWarning()
	return nil
}

// decideSeedProviderConfig returns whether to copy the provider config. Like the
// agent-config decision, the safe default (--yes, non-interactive) is NOT to
// copy, since provider config can carry long-lived credentials.
func (a *App) decideSeedProviderConfig(prefs seedPrefs, pc localagent.ProviderConfig, srcs []string) bool {
	if prefs.forceSeed {
		return true
	}
	if !prefs.interactive {
		return false
	}
	fmt.Fprintln(a.Out, theme.Warnf("Your Claude Code uses the %s provider; found its config: %s", pc.Name, strings.Join(srcs, ", ")))
	confirm := false
	err := prompt.RunConfirm(
		huh.NewConfirm().
			Title(fmt.Sprintf("Copy your %s provider config into the agent's home?", pc.Name)).
			Description("Lets the agent authenticate to the same provider. May include long-lived credentials.").
			Value(&confirm),
	)
	if err != nil {
		return false
	}
	return confirm
}

// decideSeedConfig returns whether to copy the operator's config, honouring the
// flags and otherwise prompting. The safe default (--yes, non-interactive) is
// NOT to copy, since the files can contain provider secrets.
func (a *App) decideSeedConfig(prefs seedPrefs, srcs []string) bool {
	if prefs.forceSeed {
		return true
	}
	if !prefs.interactive {
		return false
	}
	fmt.Fprintln(a.Out, theme.Warnf("Found the operator's agent config: %s", strings.Join(srcs, ", ")))
	confirm := false
	err := prompt.RunConfirm(
		huh.NewConfirm().
			Title("Copy the operator's config into the agent's home?").
			Description("Gives the agent your settings. May include provider API keys stored locally.").
			Value(&confirm),
	)
	if err != nil {
		return false
	}
	return confirm
}
