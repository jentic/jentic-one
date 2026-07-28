package cmd

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"os/user"
	"path/filepath"
	"strings"

	"github.com/charmbracelet/huh"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/install"
	"github.com/jentic/jentic-one/cli/internal/localagent"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

type runOptions struct {
	home       bool
	allowDir   bool
	noAllowDir bool
	yes          bool
	agentUser    string
	listGrants   bool
	revoke       string
	seedConfig   bool
	noSeedConfig bool
}

func newRunCmd(app *App) *cobra.Command {
	opts := &runOptions{}
	cmd := &cobra.Command{
		Use:   "run <agent> [path]",
		Short: "Launch a coding agent as its own isolated Unix user",
		Long: "run launches a coding agent (claude, ...) under a dedicated, unprivileged\n" +
			"OS account distinct from the operator's login user, so a compromised or\n" +
			"prompt-injected agent cannot read the operator's keys, browser session, or\n" +
			"the jentic-one credential store. It provisions the agent's binary for that\n" +
			"account if missing, resolves filesystem access to the working directory\n" +
			"(granting a scoped ACL only when the operator confirms), and starts the\n" +
			"session in a login shell so no operator environment leaks.\n\n" +
			"The agent account, its home, and the directories it has been granted are\n" +
			"recorded in ~/.jentic/config.yaml. See the security analysis under\n" +
			"docs/security/analysis/agent-as-unix-user/ for the full rationale.",
		Args: cobra.MaximumNArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			return app.runE(cmd, opts, args)
		},
	}
	cmd.Flags().BoolVar(&opts.home, "home", false,
		"open the session in the agent's own home, skipping the current directory")
	cmd.Flags().BoolVar(&opts.allowDir, "allow-dir", false,
		"grant the agent access to the working directory without prompting")
	cmd.Flags().BoolVar(&opts.noAllowDir, "no-allow-dir", false,
		"never grant directory access; open in the agent's home instead")
	cmd.Flags().BoolVarP(&opts.yes, "yes", "y", false,
		"assume the safe default for every prompt (never grants a flagged-dangerous dir)")
	cmd.Flags().StringVar(&opts.agentUser, "agent-user", "",
		"override the derived <operator>-local-agent account")
	cmd.Flags().BoolVar(&opts.listGrants, "list-grants", false,
		"list the directories the agent has been granted, then exit")
	cmd.Flags().StringVar(&opts.revoke, "revoke", "",
		"revoke the agent's access to the given directory, then exit")
	cmd.Flags().BoolVar(&opts.seedConfig, "seed-config", false,
		"copy the operator's agent config into the agent's home without prompting")
	cmd.Flags().BoolVar(&opts.noSeedConfig, "no-seed-config", false,
		"never copy the operator's agent config into the agent's home")
	return cmd
}

func (a *App) runE(cmd *cobra.Command, opts *runOptions, args []string) error {
	ctx := cmd.Context()
	if len(args) == 0 {
		return fmt.Errorf("missing agent identifier; known agents: %s", strings.Join(localagent.Known(), ", "))
	}
	agentID := args[0]
	desc, ok := localagent.Lookup(agentID)
	if !ok {
		return fmt.Errorf("unknown agent %q; known agents: %s", agentID, strings.Join(localagent.Known(), ", "))
	}

	cfg, err := config.Load(a.Paths)
	if err != nil {
		return err
	}

	// 1. Resolve the agent user (config entry, --agent-user, or the default).
	entry, hasEntry := cfg.LocalAgent(agentID)
	agentUser := resolveAgentUser(opts.agentUser, entry)
	binary := desc.Binary
	if hasEntry && entry.Binary != "" {
		binary = entry.Binary
	}

	// Management shortcuts: list/revoke operate on the recorded grants.
	if opts.listGrants {
		return a.runListGrants(agentID, agentUser, entry, hasEntry)
	}
	if opts.revoke != "" {
		return a.runRevoke(ctx, cfg, agentID, agentUser, opts.revoke)
	}

	if !localagent.UserExists(ctx, agentUser) {
		return fmt.Errorf("agent account %q does not exist — create it first (see "+
			"docs/security/analysis/agent-as-unix-user/05-agent-as-own-unix-user.md), "+
			"then re-run", agentUser)
	}

	// Record/refresh the entry so subsequent runs never re-derive the account.
	if !hasEntry {
		cfg.SetLocalAgent(agentID, config.LocalAgent{User: agentUser, Binary: binary})
		if saveErr := cfg.Save(a.Paths); saveErr != nil {
			return saveErr
		}
	}

	// 2. Ensure the agent's binary is installed for that user.
	if err := a.ensureAgentBinary(ctx, cmd, opts, agentUser, desc); err != nil {
		return err
	}

	// 2b. Offer to seed the operator's agent config into the agent's home (once).
	if err := a.ensureAgentConfig(ctx, cmd, opts, agentUser, desc); err != nil {
		return err
	}

	// 3. Resolve the working directory and its access.
	dir, err := a.resolveWorkingDir(ctx, cmd, cfg, opts, agentID, agentUser, args)
	if err != nil {
		if errors.Is(err, errCancelled) {
			fmt.Fprintln(a.Out, theme.Dim.Render("Cancelled."))
			return nil
		}
		return err
	}

	// 4. Launch.
	return a.launchAgent(ctx, agentUser, binary, dir)
}

// resolveAgentUser applies the precedence: --agent-user flag, then the recorded
// config entry, then the <operator>-local-agent default.
func resolveAgentUser(flag string, entry config.LocalAgent) string {
	if flag != "" {
		return flag
	}
	if entry.User != "" {
		return entry.User
	}
	operator := "user"
	if u, err := user.Current(); err == nil && u.Username != "" {
		operator = u.Username
	}
	return localagent.DefaultUserName(operator)
}

// ── step 2: binary provisioning ──────────────────────────────────────────────

func (a *App) ensureAgentBinary(ctx context.Context, cmd *cobra.Command, opts *runOptions, agentUser string, desc localagent.Descriptor) error {
	switch localagent.ProbeBinary(ctx, agentUser, desc) {
	case localagent.BinaryOnPath:
		return nil
	case localagent.BinaryFoundOffPath:
		// Installed at a known location but not resolvable by the login shell —
		// put ~/.local/bin on the agent's PATH and carry on, rather than erroring.
		fmt.Fprintln(a.Out, theme.Infof("%s is installed for %s but not on its PATH — adding ~/.local/bin ...", desc.Binary, agentUser))
		return a.ensureLocalBinOnPath(agentUser)
	case localagent.BinaryMissing:
		return a.provisionBinary(ctx, cmd, opts, agentUser, desc)
	}
	return nil
}

// ensureLocalBinOnPath appends ~/.local/bin to the agent's login PATH so the
// launch can exec a binary that lives there (copy and install both land there).
func (a *App) ensureLocalBinOnPath(agentUser string) error {
	c := localagent.EnsureLocalBinOnPathCmd(agentUser)
	c.Stdout, c.Stderr = a.Out, a.Err
	if err := c.Run(); err != nil {
		return fmt.Errorf("add ~/.local/bin to the agent's PATH: %w", err)
	}
	return nil
}

func (a *App) provisionBinary(ctx context.Context, cmd *cobra.Command, opts *runOptions, agentUser string, desc localagent.Descriptor) error {
	fmt.Fprintln(a.Out, theme.Warnf("Agent %q is not installed for user %s.", desc.ID, agentUser))

	opBin := ""
	if desc.SingleBinary {
		opBin = localagent.OperatorBinaryPath(ctx, desc.Binary)
	}

	choice := "copy"
	if opBin == "" {
		choice = "install"
	}
	if wantsInteractive(cmd, opts.yes) {
		c, err := a.pickProvisionRoute(desc, agentUser, opBin)
		if err != nil {
			return err
		}
		choice = c
	}

	switch choice {
	case "copy":
		if opBin == "" {
			return fmt.Errorf("no operator copy of %s found to copy; re-run and choose install", desc.Binary)
		}
		fmt.Fprintln(a.Out, theme.Infof("Copying %s → %s ...", opBin, agentUser))
		cp := localagent.CopyBinaryCmd(agentUser, opBin, desc.Binary)
		cp.Stdout, cp.Stderr = a.Out, a.Err
		if err := cp.Run(); err != nil {
			return fmt.Errorf("copy binary: %w", err)
		}
		fmt.Fprintln(a.Out, theme.Dim.Render("  The copy carries the binary, not credentials — the agent still logs in as itself on first run."))
		// The copy lands in ~/.local/bin, which a fresh account may not have on
		// its login PATH — make sure it does so the launch can find it.
		if err := a.ensureLocalBinOnPath(agentUser); err != nil {
			return err
		}
	case "install":
		fmt.Fprintln(a.Out, theme.Infof("Installing %s as %s ...", desc.Binary, agentUser))
		inst := localagent.InstallBinaryCmd(agentUser, desc.Install)
		inst.Stdin, inst.Stdout, inst.Stderr = os.Stdin, a.Out, a.Err
		if err := inst.Run(); err != nil {
			return fmt.Errorf("install binary: %w", err)
		}
		if err := a.ensureLocalBinOnPath(agentUser); err != nil {
			return err
		}
	case "skip":
		fmt.Fprintln(a.Out, theme.Dim.Render("Skipped. Install it for the agent yourself, then re-run."))
		return errors.New("agent binary not installed")
	}
	return nil
}

func (a *App) pickProvisionRoute(desc localagent.Descriptor, agentUser, opBin string) (string, error) {
	var choice string
	opts := []huh.Option[string]{}
	if opBin != "" {
		opts = append(opts, huh.NewOption(fmt.Sprintf("Copy the operator's binary (%s)", opBin), "copy"))
		choice = "copy"
	}
	opts = append(opts,
		huh.NewOption("Install a fresh copy as the agent", "install"),
		huh.NewOption("Skip — I'll set it up myself", "skip"),
	)
	if opBin == "" {
		choice = "install"
	}
	err := install.NewForm(huh.NewGroup(
		huh.NewSelect[string]().
			Title(fmt.Sprintf("Provision %q for %s?", desc.Binary, agentUser)).
			Options(opts...).
			Value(&choice),
	)).Run()
	if err != nil {
		if errors.Is(err, huh.ErrUserAborted) {
			return "skip", nil
		}
		return "", err
	}
	return choice, nil
}

// ── step 2b: config seeding ──────────────────────────────────────────────────

// ensureAgentConfig offers to copy the operator's agent configuration (e.g.
// ~/.claude, ~/.claude.json) into the agent's home, so the agent inherits the
// operator's settings. It only acts when the operator has such config, the
// agent doesn't already have its own, and the operator opts in — a compromised
// agent must not be able to trick a re-run into overwriting its state, and the
// operator must consciously accept that these files can carry provider secrets.
func (a *App) ensureAgentConfig(ctx context.Context, cmd *cobra.Command, opts *runOptions, agentUser string, desc localagent.Descriptor) error {
	if opts.noSeedConfig || len(desc.ConfigPaths) == 0 {
		return nil
	}
	home := localagent.OperatorHome()
	srcs := localagent.ExistingConfigPaths(home, desc)
	if len(srcs) == 0 {
		return nil // operator has nothing to seed
	}
	if localagent.AgentHasConfig(ctx, agentUser, desc) {
		return nil // agent already has its own config — don't clobber it
	}

	if !a.decideSeedConfig(cmd, opts, srcs) {
		return nil
	}

	fmt.Fprintln(a.Out, theme.Infof("Seeding %s's %s config into %s ...", desc.ID, desc.Binary, agentUser))
	c := localagent.CopyConfigCmd(agentUser, home, srcs)
	c.Stdout, c.Stderr = a.Out, a.Err
	if err := c.Run(); err != nil {
		return fmt.Errorf("seed agent config: %w", err)
	}
	fmt.Fprintln(a.Out, theme.Dim.Render("  These are the operator's settings; the agent still authenticates as itself on first launch."))
	fmt.Fprintln(a.Out, theme.Dim.Render("  Note: provider config may carry API keys — longer-term these move behind jentic-one's broker."))
	return nil
}

// decideSeedConfig returns whether to copy the operator's config, honouring the
// flags and otherwise prompting. The safe default (--yes, non-interactive) is
// NOT to copy, since the files can contain provider secrets.
func (a *App) decideSeedConfig(cmd *cobra.Command, opts *runOptions, srcs []string) bool {
	if opts.seedConfig {
		return true
	}
	if opts.yes || !wantsInteractive(cmd, opts.yes) {
		return false
	}
	fmt.Fprintln(a.Out, theme.Warnf("Found the operator's agent config: %s", strings.Join(srcs, ", ")))
	confirm := false
	err := install.RunConfirm(
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

// ── step 3: working directory + access ───────────────────────────────────────

func (a *App) resolveWorkingDir(ctx context.Context, cmd *cobra.Command, cfg *config.FileConfig, opts *runOptions, agentID, agentUser string, args []string) (string, error) {
	if opts.home {
		return "", nil // login shell starts in the agent's home
	}

	dir := ""
	if len(args) > 1 {
		dir = args[1]
	} else {
		cwd, err := os.Getwd()
		if err != nil {
			return "", err
		}
		dir = cwd
	}
	abs, err := filepath.Abs(dir)
	if err != nil {
		return "", err
	}
	abs = filepath.Clean(abs)

	// Already accessible (agent's home, an earlier grant, a shared workspace)?
	if localagent.DirAccess(ctx, agentUser, abs) {
		return abs, nil
	}

	// Not accessible — decide whether to grant, open-in-home, or cancel.
	danger := localagent.DangerReason(abs, localagent.OperatorHome())
	grant, err := a.decideDirGrant(cmd, opts, agentUser, abs, danger)
	if err != nil {
		return "", err
	}
	if !grant {
		fmt.Fprintln(a.Out, theme.Dim.Render("Opening in the agent's home instead."))
		return "", nil
	}

	if err := a.grantDir(ctx, cfg, agentID, agentUser, abs); err != nil {
		return "", err
	}
	fmt.Fprintln(a.Out, theme.Dim.Render("  Granted (persists across sessions; `jentic run "+agentID+" --list-grants` to review)."))
	fmt.Fprintln(a.Out, theme.Dim.Render("  This is OS-level access only — the agent still runs its own workspace-trust prompt."))
	return abs, nil
}

// grantDir applies the "700 home + traverse-walk + rwx-leaf" model so the agent
// can read/write abs without gaining access to the rest of the operator's home.
// For a path under the home it (1) opens execute-only traverse on each ancestor
// the agent can't already pass through, then (2) grants the rwx leaf. For a path
// outside the home the leaf grant alone suffices. The default-deny is the home's
// existing 0700 mode, not an ACL we add — see localagent's model comment for why
// we deliberately avoid a home-wide deny sweep. All grants are scoped to the
// agent user and never touch the operator's own permissions.
func (a *App) grantDir(ctx context.Context, cfg *config.FileConfig, agentID, agentUser, abs string) error {
	home := localagent.OperatorHome()

	if home != "" && localagent.IsUnderHome(home, abs) {
		// Layer 1: open traverse on the ancestors the agent can't yet pass through.
		for _, anc := range localagent.AncestorsNeedingTraverse(ctx, agentUser, home, abs) {
			if err := a.runGrant(localagent.TraverseGrantCmd(agentUser, anc), "grant traverse on "+anc); err != nil {
				return err
			}
		}
	}

	// Layer 2: the rwx leaf.
	fmt.Fprintln(a.Out, theme.Infof("Granting %s read/write to %s ...", agentUser, abs))
	if err := a.runGrant(localagent.LeafGrantCmd(agentUser, abs), "grant directory access"); err != nil {
		return err
	}
	if cfg.AddGrantedDir(agentID, abs) {
		if err := cfg.Save(a.Paths); err != nil {
			return err
		}
	}
	return nil
}

// runGrant runs one ACL command, wiring output and wrapping any failure.
func (a *App) runGrant(c *exec.Cmd, what string) error {
	c.Stdout, c.Stderr = a.Out, a.Err
	if err := c.Run(); err != nil {
		return fmt.Errorf("%s: %w", what, err)
	}
	return nil
}

// decideDirGrant returns whether to grant the agent access to dir, honouring the
// flags and, for a dangerous path, requiring a typed confirmation.
func (a *App) decideDirGrant(cmd *cobra.Command, opts *runOptions, agentUser, dir, danger string) (bool, error) {
	// Flags pre-answer, but --yes/--allow-dir must never punch a dangerous hole.
	if opts.noAllowDir {
		return false, nil
	}
	if opts.allowDir {
		if danger != "" {
			return false, fmt.Errorf("refusing to grant a flagged-dangerous directory non-interactively (%s); "+
				"re-run interactively to confirm, or pick a neutral path", danger)
		}
		return true, nil
	}
	if opts.yes {
		return false, nil // safe default: open in home
	}
	if !wantsInteractive(cmd, opts.yes) {
		return false, nil
	}

	if danger != "" {
		return a.confirmDangerousGrant(agentUser, dir, danger)
	}
	return a.confirmPlainGrant(agentUser, dir)
}

func (a *App) confirmPlainGrant(agentUser, dir string) (bool, error) {
	fmt.Fprintln(a.Out, theme.Warnf("Agent %s has no access to %s.", agentUser, dir))
	var choice string
	err := install.NewForm(huh.NewGroup(
		huh.NewSelect[string]().
			Title("How should the session reach this directory?").
			Options(
				huh.NewOption("Open in the agent's home instead", "home"),
				huh.NewOption("Allow the agent read/write here (adds an inherited ACL)", "allow"),
				huh.NewOption("Cancel", "cancel"),
			).
			Value(&choice),
	)).Run()
	if err != nil {
		if errors.Is(err, huh.ErrUserAborted) {
			return false, errCancelled
		}
		return false, err
	}
	switch choice {
	case "allow":
		return true, nil
	case "cancel":
		return false, errCancelled
	default:
		return false, nil
	}
}

func (a *App) confirmDangerousGrant(agentUser, dir, danger string) (bool, error) {
	fmt.Fprintln(a.Out, theme.Error.Render("⚠  "+strings.ToUpper(dir)))
	fmt.Fprintln(a.Out, theme.Warnf("   Granting %s here is dangerous: %s.", agentUser, danger))
	fmt.Fprintln(a.Out, theme.Dim.Render("   This re-opens the credential boundary this setup exists to protect."))

	var choice string
	err := install.NewForm(huh.NewGroup(
		huh.NewSelect[string]().
			Title("This directory should not normally be granted.").
			Options(
				huh.NewOption("Open in the agent's home instead", "home"),
				huh.NewOption("I understand the risk — grant anyway", "allow"),
				huh.NewOption("Cancel", "cancel"),
			).
			Value(&choice),
	)).Run()
	if err != nil {
		if errors.Is(err, huh.ErrUserAborted) {
			return false, errCancelled
		}
		return false, err
	}
	if choice != "allow" {
		if choice == "cancel" {
			return false, errCancelled
		}
		return false, nil
	}

	// Require typing the word to confirm — a keypress is not enough here.
	var typed string
	if err := install.NewForm(huh.NewGroup(
		install.Input().
			Title("Type 'grant' to confirm this dangerous grant").
			Value(&typed),
	)).Run(); err != nil {
		if errors.Is(err, huh.ErrUserAborted) {
			return false, errCancelled
		}
		return false, err
	}
	if strings.TrimSpace(typed) != "grant" {
		fmt.Fprintln(a.Out, theme.Dim.Render("Not confirmed — opening in the agent's home instead."))
		return false, nil
	}
	return true, nil
}

// errCancelled signals a user-initiated cancel that runE turns into a clean exit.
var errCancelled = errors.New("cancelled")

// ── step 4: launch ───────────────────────────────────────────────────────────

func (a *App) launchAgent(ctx context.Context, agentUser, binary, dir string) error {
	where := dir
	if where == "" {
		where = "the agent's home"
	}
	fmt.Fprintln(a.Out, theme.Infof("Launching %s as %s in %s ...", binary, agentUser, where))
	cmd := localagent.LaunchCmd(ctx, agentUser, binary, dir)
	cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, os.Stdout, os.Stderr
	if err := cmd.Run(); err != nil {
		var exit interface{ ExitCode() int }
		if errors.As(err, &exit) && exit.ExitCode() >= 0 {
			return &exitCodeError{code: exit.ExitCode()}
		}
		return fmt.Errorf("launch %s: %w", binary, err)
	}
	return nil
}

// ── management: --list-grants / --revoke ─────────────────────────────────────

func (a *App) runListGrants(agentID, agentUser string, entry config.LocalAgent, hasEntry bool) error {
	fmt.Fprintln(a.Out, theme.Heading.Render("Directory grants"))
	fmt.Fprintln(a.Out, "  "+theme.Field("agent", agentID))
	fmt.Fprintln(a.Out, "  "+theme.Field("user", agentUser))
	if !hasEntry || len(entry.GrantedDirs) == 0 {
		fmt.Fprintln(a.Out, "  "+theme.Dim.Render("no directories granted"))
		return nil
	}
	for _, d := range entry.GrantedDirs {
		danger := localagent.DangerReason(d, localagent.OperatorHome())
		line := "  " + theme.Field("dir", d)
		if danger != "" {
			line += " " + theme.Warnf("(⚠ %s)", danger)
		}
		fmt.Fprintln(a.Out, line)
	}
	return nil
}

func (a *App) runRevoke(ctx context.Context, cfg *config.FileConfig, agentID, agentUser, dir string) error {
	abs, err := filepath.Abs(dir)
	if err != nil {
		return err
	}
	abs = filepath.Clean(abs)

	fmt.Fprintln(a.Out, theme.Infof("Revoking %s access to %s ...", agentUser, abs))
	r := localagent.LeafRevokeCmd(agentUser, abs)
	r.Stdout, r.Stderr = a.Out, a.Err
	if err := r.Run(); err != nil {
		return fmt.Errorf("revoke directory access: %w", err)
	}
	if cfg.RemoveGrantedDir(agentID, abs) {
		if err := cfg.Save(a.Paths); err != nil {
			return err
		}
	}
	fmt.Fprintln(a.Out, theme.Successf("Revoked."))
	return nil
}
