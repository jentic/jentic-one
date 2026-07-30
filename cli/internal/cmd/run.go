package cmd

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"os/user"
	"path/filepath"
	"strings"

	"github.com/charmbracelet/huh"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/install"
	"github.com/jentic/jentic-one/cli/internal/localagent"
	"github.com/jentic/jentic-one/cli/internal/profile"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

type runOptions struct {
	home         bool
	allowDir     bool
	noAllowDir   bool
	yes          bool
	agentUser    string
	profile      string
	listGrants   bool
	grant        string
	revoke       string
	seedConfig   bool
	noSeedConfig bool
}

func newRunCmd(app *App) *cobra.Command {
	opts := &runOptions{}
	cmd := &cobra.Command{
		Use:   "run <agent> [path] [-- <agent-args>...]",
		Short: "Launch a coding agent as its own isolated Unix user",
		Long: "run launches a coding agent (claude, ...) under a dedicated, unprivileged\n" +
			"OS account distinct from the operator's login user, so a compromised or\n" +
			"prompt-injected agent cannot read the operator's keys, browser session, or\n" +
			"the jentic-one credential store. It provisions the agent's binary for that\n" +
			"account if missing, resolves filesystem access to the working directory\n" +
			"(granting a scoped ACL only when the operator confirms), and starts the\n" +
			"session in a login shell so no operator environment leaks.\n\n" +
			"Arguments for the agent binary are forwarded verbatim, in either form:\n" +
			"  jentic run claude -- --model opus -p \"hi\"   (agent, then -- <agent-args>)\n" +
			"  jentic run -- claude --model opus -p \"hi\"   (-- then the whole agent command)\n" +
			"both run `claude --model opus -p \"hi\"` as the agent user. In the leading-`--`\n" +
			"form the first token after `--` is the agent id and there is no path argument\n" +
			"(the working directory defaults to the current one); use the trailing form to\n" +
			"pass a path. Without any `--`, run takes at most the agent id and a path.\n\n" +
			"The agent account, its home, and the directories it has been granted are\n" +
			"recorded in ~/.jentic/config.yaml. See the security analysis in\n" +
			"docs/security/local-agent/local-agent-isolation.md for the full rationale.",
		// Only the positional args that are jentic's own (agent id, optional path)
		// count against the limit; everything forwarded to the agent does not.
		// cmd.ArgsLenAtDash() is the arg count before `--` (-1 when absent): a
		// LEADING `--` (dash == 0) puts the agent id in the passthrough, so 0
		// jentic positionals; a trailing `--` (dash > 0) keeps agent+path before it.
		Args: func(cmd *cobra.Command, args []string) error {
			n := len(args)
			if dash := cmd.ArgsLenAtDash(); dash >= 0 {
				n = dash
			}
			if n > 2 {
				return fmt.Errorf("accepts at most 2 args before `--` (agent and path), received %d", n)
			}
			return nil
		},
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
	cmd.Flags().StringVar(&opts.profile, "profile", "",
		"launch with this agent profile checked out for the session (overrides the checked-out default)")
	cmd.Flags().BoolVar(&opts.listGrants, "list-grants", false,
		"list the directories the agent has been granted, then exit")
	cmd.Flags().StringVar(&opts.grant, "grant", "",
		"grant the agent access to the given directory, then exit (without launching)")
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

	// Split jentic's own positional args (agent id, optional path) from the agent
	// passthrough — the argv forwarded verbatim to the agent binary. Two `--` forms
	// are supported (ArgsLenAtDash() is the arg count before `--`, -1 when absent):
	//
	//   jentic run claude -- --flag x   (trailing): dash=1 → agent/path before it,
	//                                    forwarded args after.
	//   jentic run -- claude --flag x   (leading):  dash=0 → the whole agent command
	//                                    is forwarded, so its FIRST token is the
	//                                    agent id and the remainder is the argv. The
	//                                    leading form takes no path (cwd is used).
	//
	// The leading form's value is that everything after `--` skips jentic's flag
	// parsing, so an agent flag like --resumeSessionId never collides with a jentic
	// flag or needs escaping.
	posArgs, agentArgs := args, []string(nil)
	if dash := cmd.ArgsLenAtDash(); dash == 0 {
		// Leading `--`: pull the agent id out of the forwarded tokens.
		if len(args) == 0 {
			return fmt.Errorf("missing agent identifier after `--`; known agents: %s", strings.Join(localagent.Known(), ", "))
		}
		posArgs, agentArgs = args[:1], args[1:]
	} else if dash > 0 {
		posArgs, agentArgs = args[:dash], args[dash:]
	}

	if len(posArgs) == 0 {
		return fmt.Errorf("missing agent identifier; known agents: %s", strings.Join(localagent.Known(), ", "))
	}
	agentID := posArgs[0]
	desc, ok := localagent.Lookup(agentID)
	if !ok {
		return fmt.Errorf("unknown agent %q; known agents: %s", agentID, strings.Join(localagent.Known(), ", "))
	}

	cfg, err := config.Load(a.Paths)
	if err != nil {
		return err
	}

	// 1. Resolve the shared agent account (config record, --agent-user, or the
	// default). The binary is always the descriptor's — <agent> selects the
	// binary, never the account (there is one account for every agent).
	acct, hasAcct := cfg.AgentAccount()
	agentUser := resolveAgentUser(opts.agentUser, acct)
	binary := desc.Binary

	// Without a provisioned, enabled agent account there is no Unix user to
	// isolate into. The grant/account management shortcuts have nothing to act on,
	// and a launch simply runs the agent binary directly as the operator — the CLI
	// behaves exactly as it does for someone who never enabled isolation. The
	// --agent-user override is the one way to still target an account explicitly.
	if !cfg.HasAgentUser() && opts.agentUser == "" {
		if opts.listGrants || opts.grant != "" || opts.revoke != "" {
			return errors.New("no agent account is set up, so there are no directory grants to manage — " +
				"run `jentic bootstrap` to create the isolated agent user first")
		}
		return a.runSameUser(ctx, cfg, desc, opts, posArgs, agentArgs)
	}

	// Management shortcuts: list/revoke operate on the recorded grants.
	if opts.listGrants {
		return a.runListGrants(agentID, agentUser, acct, hasAcct)
	}
	if opts.revoke != "" {
		return a.runRevoke(ctx, cfg, agentUser, opts.revoke)
	}

	if !localagent.UserExists(ctx, agentUser) {
		return fmt.Errorf("agent account %q does not exist — create it first with "+
			"`jentic bootstrap` or `jenticctl wizard` (see "+
			"docs/security/local-agent/local-agent-isolation.md), then re-run", agentUser)
	}

	// Confirm we can actually become the agent user before anything else. Every
	// later step (binary probe, ACL grants, launch) runs through `sudo -u <agent>`,
	// and a failed sudo authentication exits non-zero exactly like a missing binary
	// — so without this preflight a declined password prompt is misreported as
	// "agent not installed" and the operator is offered a pointless reinstall. Fail
	// here with the real reason instead. This is also the single place the password
	// prompt appears (subsequent sudo calls reuse the cached credential).
	if err := a.ensureCanRunAsAgent(ctx, agentUser); err != nil {
		return err
	}

	// Management shortcut: grant a directory and exit (mirrors --revoke). It
	// applies the same scoped ACL and danger-confirmation as an in-launch grant.
	if opts.grant != "" {
		return a.runGrantDir(ctx, cmd, cfg, opts, agentID, agentUser, opts.grant)
	}

	// 2. Ensure the agent's binary is installed for that user.
	if err := a.ensureAgentBinary(ctx, cmd, opts, agentUser, desc); err != nil {
		return err
	}

	// 2a. Share the operator's world-readable CLI tool dirs (e.g. Homebrew's
	// /opt/homebrew/bin) on the agent's PATH so agent sessions can use tools the
	// operator has installed there. Home-local dirs (~/.local/bin, ~/.cargo/bin)
	// are intentionally excluded — see SharedBinPaths.
	if err := a.ensureSharedBinsOnPath(agentUser); err != nil {
		return err
	}

	// 2b. Offer to seed the operator's agent config into the agent's home (once).
	prefs := opts.seedPrefs(cmd)
	if err := a.ensureAgentConfig(ctx, prefs, agentUser, desc); err != nil {
		return err
	}

	// 2c. Offer to seed the operator's LLM-provider config (e.g. ~/.aws for
	// Bedrock) so the agent can authenticate to the same provider.
	if err := a.ensureProviderConfig(ctx, prefs, agentUser); err != nil {
		return err
	}

	// 3. Resolve the working directory and its access.
	dir, err := a.resolveWorkingDir(ctx, cmd, cfg, opts, agentID, agentUser, posArgs)
	if err != nil {
		if errors.Is(err, errCancelled) {
			fmt.Fprintln(a.Out, theme.Dim.Render("Cancelled."))
			return nil
		}
		return err
	}

	// 3a. Resolve the profile to check out for this session: --profile if given,
	// else the agent account's own checked-out default. It is injected as
	// JENTIC_PROFILE so the launched agent (and any `jentic` it runs) acts on the
	// right profile without the operator passing a flag inside the session.
	sessionProfile, err := a.resolveSessionProfile(opts.profile, acct)
	if err != nil {
		return err
	}

	// 4. Launch (confined — see launchAgent for the error-closed contract).
	return a.launchAgent(ctx, acct, agentUser, binary, dir, sessionProfile, agentArgs)
}

// resolveSessionProfile picks the profile injected as JENTIC_PROFILE into the
// confined session: the --profile override if given (validated against the agent
// home's profile store), else the account's own checked-out profile (the agent
// home's default_profile, which register/bootstrap set on check-out). Returns ""
// when nothing is checked out and no override was given, so the agent falls back
// to its own default.
func (a *App) resolveSessionProfile(flag string, acct config.AgentAccount) (string, error) {
	if acct.ConfigDir == "" {
		return flag, nil
	}
	agentPaths := config.Paths{Root: acct.ConfigDir}
	if flag != "" {
		names, err := profile.List(agentPaths)
		if err != nil {
			return "", err
		}
		for _, n := range names {
			if n == flag {
				return flag, nil
			}
		}
		return "", fmt.Errorf("profile %q is not registered for the agent account; "+
			"run `jentic profile list` to see the agent's profiles", flag)
	}
	agentCfg, err := config.Load(agentPaths)
	if err != nil {
		return "", err
	}
	return agentCfg.DefaultProfile, nil
}

// runSameUser launches the agent binary directly as the operator, with no Unix
// user, no confinement, and no ACL grants. This is the path for an operator who
// never enabled agent-user isolation (HasAgentUser is false): `jentic run` is
// then just a convenient launcher that resolves the binary and injects the
// operator's active profile as JENTIC_PROFILE. The working directory is the path
// argument if given, else the current directory (there is nothing to grant — the
// agent already runs with the operator's own filesystem access).
func (a *App) runSameUser(ctx context.Context, cfg *config.FileConfig, desc localagent.Descriptor, opts *runOptions, posArgs, agentArgs []string) error {
	binary, err := exec.LookPath(desc.Binary)
	if err != nil {
		return fmt.Errorf("%s is not installed or not on your PATH; install it, then re-run "+
			"(or run `jentic bootstrap` to set up an isolated agent user)", desc.Binary)
	}

	dir := ""
	if !opts.home && len(posArgs) > 1 {
		abs, aerr := filepath.Abs(posArgs[1])
		if aerr != nil {
			return aerr
		}
		dir = filepath.Clean(abs)
	}

	c := exec.CommandContext(ctx, binary, agentArgs...) //nolint:gosec // binary is resolved from the agent descriptor registry; agentArgs are the operator's own pass-through args for their coding agent.
	c.Dir = dir
	c.Stdin, c.Stdout, c.Stderr = os.Stdin, os.Stdout, os.Stderr
	// The operator's own active profile (flag < JENTIC_PROFILE < config default)
	// carries into the session so the agent's `jentic` calls act on it.
	c.Env = append(os.Environ(), config.ProfileEnv+"="+cfg.ResolvedProfileName(opts.profile))
	if err := c.Run(); err != nil {
		var exit interface{ ExitCode() int }
		if errors.As(err, &exit) && exit.ExitCode() >= 0 {
			return &exitCodeError{code: exit.ExitCode()}
		}
		return fmt.Errorf("launch %s: %w", desc.Binary, err)
	}
	return nil
}

// resolveAgentUser applies the precedence: --agent-user flag, then the recorded
// account, then the <operator>-local-agent default.
func resolveAgentUser(flag string, acct config.AgentAccount) string {
	if flag != "" {
		return flag
	}
	if acct.User != "" {
		return acct.User
	}
	operator := "user"
	if u, err := user.Current(); err == nil && u.Username != "" {
		operator = u.Username
	}
	return localagent.DefaultUserName(operator)
}

// ensureCanRunAsAgent confirms the operator can switch to the agent user before
// any step that relies on it. The check runs `sudo -u <agent> … true` with the
// terminal wired up, so a passwordless rule passes silently and, without one, the
// operator sees the sudo password prompt here — the single place it appears. A
// non-zero exit means we could NOT become the agent (declined/wrong password, no
// sudoers rights), which every later `sudo -u <agent>` would hit too; we surface
// that as the real reason rather than letting the binary probe misread it as a
// missing install.
func (a *App) ensureCanRunAsAgent(ctx context.Context, agentUser string) error {
	c := localagent.CanRunAsAgentCmd(ctx, agentUser)
	c.Stdin, c.Stdout, c.Stderr = os.Stdin, a.Out, a.Err
	if err := c.Run(); err != nil {
		return fmt.Errorf("couldn't switch to the agent user %q — the launch needs to run as that "+
			"account (every step uses `sudo -u %s`).\n"+
			"  If you were asked for your password and cancelled, re-run and enter it. To skip the\n"+
			"  prompt each time, enable passwordless launch during `jentic bootstrap` (or re-run it)",
			agentUser, agentUser)
	}
	return nil
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

// ensureSharedBinsOnPath appends the operator's world-readable CLI tool dirs to
// the agent's login PATH (idempotent). It is best-effort convenience, not a
// security boundary: on failure it warns and continues rather than blocking the
// launch, and it no-ops when there is nothing safe to share.
func (a *App) ensureSharedBinsOnPath(agentUser string) error {
	dirs := localagent.SharedBinPaths(localagent.OperatorHome())
	c := localagent.EnsureSharedBinsOnPathCmd(agentUser, dirs)
	if c == nil {
		return nil
	}
	c.Stdout, c.Stderr = a.Out, a.Err
	if err := c.Run(); err != nil {
		fmt.Fprintln(a.Out, theme.Warnf("could not add operator CLI tool dirs to the agent's PATH: %v", err))
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

// ── step 3: working directory + access ───────────────────────────────────────

func (a *App) resolveWorkingDir(ctx context.Context, cmd *cobra.Command, cfg *config.FileConfig, opts *runOptions, agentID, agentUser string, args []string) (string, error) {
	if opts.home {
		return "", nil // login shell starts in the agent's home
	}

	var dir string
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
	verdict := localagent.Classify(abs, localagent.OperatorHome())
	grant, err := a.decideDirGrant(cmd, opts, agentUser, abs, verdict)
	if err != nil {
		return "", err
	}
	if !grant {
		fmt.Fprintln(a.Out, theme.Dim.Render("Opening in the agent's home instead."))
		return "", nil
	}

	if err := a.grantDir(ctx, cfg, agentUser, abs); err != nil {
		return "", err
	}
	fmt.Fprintln(a.Out, theme.Dim.Render("  Granted (persists across sessions; `jentic run "+agentID+" --list-grants` to review)."))
	fmt.Fprintln(a.Out, theme.Dim.Render("  This is OS-level access only — the agent still runs its own workspace-trust prompt."))
	return abs, nil
}

// grantDir applies the "traverse-walk + rwx-leaf" ACL model so the agent uid can
// read/write abs. For a path under the home it (1) opens execute-only traverse on
// each ancestor the agent can't already pass through, then (2) grants the rwx leaf.
// For a path outside the home the leaf grant alone suffices. These grants only ever
// OPEN access (the sandbox is intersection-only, so a DAC grant is still required);
// the sibling-traversal leak they leave open is closed per session by the
// process-confinement layer (see localagent/confine.go), not by an ACL deny sweep.
// All grants are scoped to the agent user and never touch the operator's own
// permissions.
func (a *App) grantDir(ctx context.Context, cfg *config.FileConfig, agentUser, abs string) error {
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
	if cfg.AddGrantedDir(abs) {
		if err := cfg.Save(a.Paths); err != nil {
			return err
		}
	}
	return nil
}

// runGrant runs one ACL command, wiring output and wrapping any failure.
//
// A recursive stamp over a large, live tree can race the filesystem: an entry the
// walk saw can be gone by the time chmod reaches it, and chmod exits non-zero after
// printing "No such file or directory" for that entry even though every surviving
// file was stamped. Those per-entry misses are benign, so stderr is captured and
// classified: if the only failures are missing entries the grant is reported as a
// success (with a count), and any other error still fails.
func (a *App) runGrant(c *exec.Cmd, what string) error {
	c.Stdout = a.Out
	var stderr strings.Builder
	c.Stderr = &stderr
	err := c.Run()
	out := stderr.String()
	if err == nil {
		fmt.Fprint(a.Err, out)
		return nil
	}
	if missing, benign := classifyGrantStderr(out); benign {
		fmt.Fprintln(a.Err, theme.Infof(
			"%s: skipped %d entr%s that disappeared during the scan (harmless).",
			what, missing, plural(missing, "y", "ies")))
		return nil
	}
	fmt.Fprint(a.Err, out)
	return fmt.Errorf("%s: %w", what, err)
}

// classifyGrantStderr reports how many entries chmod could not find and whether
// every non-blank stderr line was one of those benign "No such file or directory"
// misses. Any other diagnostic makes the failure real.
func classifyGrantStderr(out string) (missing int, benign bool) {
	sawLine := false
	for _, line := range strings.Split(out, "\n") {
		if strings.TrimSpace(line) == "" {
			continue
		}
		sawLine = true
		if strings.HasSuffix(strings.TrimRight(line, "\r"), "No such file or directory") {
			missing++
			continue
		}
		return missing, false
	}
	return missing, sawLine
}

// plural returns one or other depending on n.
func plural(n int, one, other string) string {
	if n == 1 {
		return one
	}
	return other
}

// decideDirGrant returns whether to grant the agent access to dir, honouring the
// flags and the path's ban class. A banned path (the operator's or another
// user's home, or any sensitive/system subtree) is NEVER grantable — there is no
// "grant anyway" escape hatch; the operator may only open in the agent's home or
// cancel. Only an ordinary, unbanned path can be granted.
func (a *App) decideDirGrant(cmd *cobra.Command, opts *runOptions, agentUser, dir string, verdict localagent.DangerVerdict) (bool, error) {
	// A banned path can never be granted, by any flag or prompt.
	if verdict.Banned() {
		if opts.allowDir {
			return false, fmt.Errorf("refusing to grant a protected directory (%s); "+
				"this path cannot be handed to the agent — pick a directory outside it", verdict.Reason)
		}
		if !wantsInteractive(cmd, opts.yes) {
			// Non-interactive: fall back to the agent's home (no grant).
			return false, nil
		}
		return a.confirmBannedPath(agentUser, dir, verdict)
	}

	// Ordinary path: flags may pre-answer.
	if opts.noAllowDir {
		return false, nil
	}
	if opts.allowDir {
		return true, nil
	}
	if opts.yes {
		return false, nil // safe default: open in home
	}
	if !wantsInteractive(cmd, opts.yes) {
		return false, nil
	}
	return a.confirmPlainGrant(agentUser, dir)
}

func (a *App) confirmPlainGrant(agentUser, dir string) (bool, error) {
	fmt.Fprintln(a.Out, theme.Warnf("Agent %s has no access to %s.", agentUser, dir))
	// Focus "Allow" by default: this is an ordinary (non-banned) workspace the
	// operator explicitly asked to open, so granting is the expected choice. huh
	// focuses the option whose value matches the bound field's current value.
	choice := "allow"
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

// confirmBannedPath handles a protected path: it explains why the directory
// cannot be granted and offers only to open in the agent's home or cancel. There
// is deliberately no "grant anyway" option — a banned path is a non-negotiable
// boundary, so this returns (false, ...) in every non-error case.
func (a *App) confirmBannedPath(agentUser, dir string, verdict localagent.DangerVerdict) (bool, error) {
	fmt.Fprintln(a.Out, theme.Error.Render("⚠  "+strings.ToUpper(dir)))
	fmt.Fprintln(a.Out, theme.Warnf("   %s can't be granted access here: %s.", agentUser, verdict.Reason))
	fmt.Fprintln(a.Out, theme.Dim.Render("   This directory is a protected boundary and cannot be handed to the agent."))

	var choice string
	err := install.NewForm(huh.NewGroup(
		huh.NewSelect[string]().
			Title("This directory cannot be granted.").
			Options(
				huh.NewOption("Open in the agent's home instead", "home"),
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
	if choice == "cancel" {
		return false, errCancelled
	}
	return false, nil
}

// errCancelled signals a user-initiated cancel that runE turns into a clean exit.
var errCancelled = errors.New("cancelled")

// ── step 4: launch ───────────────────────────────────────────────────────────

// launchAgent starts the confined agent session. Confinement is REQUIRED: it
// closes the sibling-traversal leak that the coarse ACL grant leaves open, and it
// is what replaces the old `chmod 700 ~` guarantee. When this machine can't confine
// the process (no sandbox-exec on macOS; no bwrap / unprivileged userns on Linux)
// we ERROR CLOSED — refuse the launch rather than silently drop to an unconfined
// session — and point the operator at an alternative isolation route.
func (a *App) launchAgent(ctx context.Context, acct config.AgentAccount, agentUser, binary, dir, sessionProfile string, agentArgs []string) error {
	if ok, reason := localagent.ConfinementAvailable(); !ok {
		return fmt.Errorf("fully locked-down agent sessions aren't available on this machine (%s).\n"+
			"  jentic run won't start an unconfined session, because that would expose the operator's\n"+
			"  home beyond the directories granted. To run this agent in isolation instead, consider\n"+
			"  containerising it (e.g. run it inside Docker). See "+
			"docs/security/local-agent/sandbox-exec-plan.md", reason)
	}

	grantedDirs := acct.GrantedDirs
	agentHome := acct.HomeDir
	if agentHome == "" {
		// Fall back to the conventional default so the sandbox re-allows the
		// agent's own home even if config predates HomeDir being recorded.
		agentHome = localagent.DefaultHomeDir(agentUser)
	}

	where := dir
	if where == "" {
		where = "the agent's home"
	}
	fmt.Fprintln(a.Out, theme.Infof("Launching %s as %s in %s (confined) ...", binary, agentUser, where))
	cmd := localagent.ConfineLaunchCmd(ctx, agentUser, binary, dir, agentHome, sessionProfile, grantedDirs, agentArgs)
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

func (a *App) runListGrants(agentID, agentUser string, acct config.AgentAccount, hasAcct bool) error {
	fmt.Fprintln(a.Out, theme.Heading.Render("Directory grants"))
	fmt.Fprintln(a.Out, "  "+theme.Field("agent", agentID))
	fmt.Fprintln(a.Out, "  "+theme.Field("user", agentUser))
	if !hasAcct || len(acct.GrantedDirs) == 0 {
		fmt.Fprintln(a.Out, "  "+theme.Dim.Render("no directories granted"))
		return nil
	}
	for _, d := range acct.GrantedDirs {
		danger := localagent.DangerReason(d, localagent.OperatorHome())
		line := "  " + theme.Field("dir", d)
		if danger != "" {
			line += " " + theme.Warnf("(⚠ %s)", danger)
		}
		fmt.Fprintln(a.Out, line)
	}
	a.printRevokeHint()
	return nil
}

// runGrantDir grants the agent access to dir and exits without launching — the
// standalone counterpart to --revoke. It reuses the in-launch access flow: if
// the agent already reaches dir it is a no-op; otherwise it applies the same
// danger-confirmation and scoped-ACL grant (grantDir) as `jentic run <agent>
// <path>` would, and records it.
func (a *App) runGrantDir(ctx context.Context, cmd *cobra.Command, cfg *config.FileConfig, opts *runOptions, agentID, agentUser, dir string) error {
	abs, err := filepath.Abs(dir)
	if err != nil {
		return err
	}
	abs = filepath.Clean(abs)

	if localagent.DirAccess(ctx, agentUser, abs) {
		fmt.Fprintln(a.Out, theme.Dim.Render(agentUser+" already has access to "+abs+"."))
		return nil
	}

	verdict := localagent.Classify(abs, localagent.OperatorHome())
	grant, err := a.decideDirGrant(cmd, opts, agentUser, abs, verdict)
	if err != nil {
		if errors.Is(err, errCancelled) {
			fmt.Fprintln(a.Out, theme.Dim.Render("Cancelled."))
			return nil
		}
		return err
	}
	if !grant {
		fmt.Fprintln(a.Out, theme.Dim.Render("Not granted."))
		return nil
	}
	if err := a.grantDir(ctx, cfg, agentUser, abs); err != nil {
		return err
	}
	fmt.Fprintln(a.Out, theme.Successf("Granted (persists across sessions; `jentic run %s --list-grants` to review).", agentID))
	return nil
}

func (a *App) runRevoke(_ context.Context, cfg *config.FileConfig, agentUser, dir string) error {
	abs, err := filepath.Abs(dir)
	if err != nil {
		return err
	}
	abs = filepath.Clean(abs)

	fmt.Fprintln(a.Out, theme.Infof("Revoking %s access to %s ...", agentUser, abs))
	r := localagent.LeafRevokeCmd(agentUser, abs)
	// The recursive `chmod -a` emits an expected per-file error on entries that
	// don't carry the exact ACE; we summarise it below, so swallow the raw stderr.
	r.Stdout, r.Stderr = a.Out, io.Discard
	if err := r.Run(); err != nil {
		// The revoke recurses over the subtree; macOS `chmod -a` exits non-zero on
		// entries that don't carry the exact ACE (inherited-only children, files
		// with no ACL). That is benign — the ACE is removed everywhere it existed —
		// so warn and still drop the recorded grant rather than abort.
		fmt.Fprintln(a.Out, theme.Dim.Render(
			"  (some entries had no matching grant to remove — that's expected; continuing)"))
	}
	if cfg.RemoveGrantedDir(abs) {
		if err := cfg.Save(a.Paths); err != nil {
			return err
		}
	}
	fmt.Fprintln(a.Out, theme.Successf("Revoked."))
	return nil
}
