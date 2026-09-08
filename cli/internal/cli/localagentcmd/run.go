package localagentcmd

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
	"time"

	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/localagent"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

// cancelGracePeriod is how long a launched agent session is given to exit on the
// SIGTERM sent when the run context is cancelled, before the exec package escalates
// to SIGKILL. It is generous because the SIGTERM has to relay down the launch chain
// (sudo → shell → confinement wrapper → agent) and the agent may flush transcript
// state on the way out.
const cancelGracePeriod = 5 * time.Second

// wireGracefulCancel makes ctx-cancellation of a launched session TERMINATE the
// child rather than SIGKILL it. exec.CommandContext's default cancel sends SIGKILL
// to the DIRECT child — for a confined launch that child is `sudo`, which cannot
// relay an uncatchable SIGKILL, so the agent process tree underneath it would be
// orphaned (reparented to init and left running) on a `jentic run` cancel. Sending
// SIGTERM instead lets sudo (and the shells below it) forward the signal so the
// whole tree unwinds; WaitDelay is the backstop that still forces a SIGKILL if the
// tree hasn't exited within the grace period. Interactive Ctrl-C is unaffected: it
// is delivered by the tty to the entire foreground process group by the kernel, so
// this only governs the programmatic-cancel path (a SIGINT/SIGTERM to the jentic
// process, which cancels ctx).
func wireGracefulCancel(cmd *exec.Cmd) {
	cmd.Cancel = func() error {
		// Best-effort: if the process has already gone, the platform terminate
		// returns an error we deliberately ignore — WaitDelay/Wait handle the
		// terminal state. On Unix this sends a catchable SIGTERM so sudo/shells
		// can relay it down the launch chain; on Windows there is no relayable
		// termination signal, so it falls back to Kill (OPS-20).
		return terminateForCancel(cmd.Process)
	}
	cmd.WaitDelay = cancelGracePeriod
}

type runOptions struct {
	home         bool
	allowDir     bool
	noAllowDir   bool
	yes          bool
	agentUser    string
	listGrants   bool
	grant        string
	revoke       string
	seedConfig   bool
	noSeedConfig bool
}

// NewRunCmd builds the `run` command that launches a coding agent inside the
// per-session sandbox. Shared by both trees via cmdcore.
func NewRunCmd(app *cmdcore.App) *cobra.Command {
	a := &Cmd{App: app}
	opts := &runOptions{}
	cmd := &cobra.Command{
		Use:   "run <agent> [path] [-- <agent-args>...]",
		Short: "Launch a coding agent as its own isolated Unix user",
		Long: "run launches a coding agent under a dedicated, unprivileged OS account\n" +
			"distinct from the operator's login user, so a compromised or prompt-injected\n" +
			"agent cannot read the operator's keys, browser session, or the jentic-one\n" +
			"credential store. It provisions the agent's binary for that account if\n" +
			"missing, resolves filesystem access to the working directory (granting a\n" +
			"scoped ACL only when the operator confirms), and starts the session as\n" +
			"the agent's own Unix user in a fresh login shell, so the operator's\n" +
			"environment and secrets are not inherited by the agent.\n\n" +
			"Runnable agents: " + strings.Join(localagent.Known(), ", ") + ".\n" +
			"(\"generic\" is a skill-only operator — it has no binary and cannot be run;\n" +
			"use `jentic skill` to write its onboarding docs instead.)\n\n" +
			"Arguments for the agent binary are forwarded verbatim, in either form:\n" +
			"  jentic run claude -- --model opus -p \"hi\"   (agent, then -- <agent-args>)\n" +
			"  jentic run -- claude --model opus -p \"hi\"   (-- then the whole agent command)\n" +
			"both run `claude --model opus -p \"hi\"` as the agent user. In the leading-`--`\n" +
			"form the first token after `--` is the agent id and there is no path argument\n" +
			"(the working directory defaults to the current one); use the trailing form to\n" +
			"pass a path. Without any `--`, run takes at most the agent id and a path.\n\n" +
			"The agent account, its home, and the directories it has been granted are\n" +
			"recorded in the CLI's agent state (~/.config/jentic/agent-account.yaml).\n" +
			"See the security analysis in\n" +
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
			return a.runE(cmd, opts, args)
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

func (a *Cmd) runE(cmd *cobra.Command, opts *runOptions, args []string) error {
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
		if localagent.IsSkillOnly(agentID) {
			return fmt.Errorf("%q is a skill-only operator: `jentic run` launches coding agents, "+
				"but %q has no runnable binary (use `jentic skill` to write its onboarding docs). "+
				"Runnable agents: %s", agentID, agentID, strings.Join(localagent.Known(), ", "))
		}
		return fmt.Errorf("unknown agent %q; known agents: %s", agentID, strings.Join(localagent.Known(), ", "))
	}

	st, err := config.LoadAgentState(a.Paths)
	if err != nil {
		return err
	}

	// 1. Resolve the shared agent account (recorded state, --agent-user, or the
	// default). The binary is always the descriptor's — <agent> selects the
	// binary, never the account (there is one account for every agent).
	acct, hasAcct := st.AgentAccount()
	agentUser := resolveAgentUser(opts.agentUser, acct)
	binary := desc.Binary

	// Without a provisioned, enabled agent account there is no Unix user to
	// isolate into. The grant/account management shortcuts have nothing to act on,
	// and a launch simply runs the agent binary directly as the operator — the CLI
	// behaves exactly as it does for someone who never enabled isolation. The
	// --agent-user override is the one way to still target an account explicitly.
	if !st.HasAgentUser() && opts.agentUser == "" {
		if opts.listGrants || opts.grant != "" || opts.revoke != "" {
			return accountMissingErr("no agent account is set up, so there are no directory grants to manage")
		}
		return a.runSameUser(ctx, st, desc, opts, posArgs, agentArgs)
	}

	// Every step below threads agentUser into a privileged command (sudo -u, the
	// ACL grants, the confined launch). Validate it here — before the first one is
	// built — so a malformed --agent-user or a hand-edited config account name is
	// rejected up front rather than reaching a shell as an injection vector.
	if err := localagent.ValidateAgentUser(agentUser); err != nil {
		return err
	}

	// Management shortcuts: list/revoke operate on the recorded grants.
	if opts.listGrants {
		return a.runListGrants(agentID, agentUser, acct, hasAcct)
	}
	if opts.revoke != "" {
		return a.runRevoke(ctx, st, agentUser, opts.revoke)
	}

	if !localagent.UserExists(ctx, agentUser) {
		return accountMissingErr(fmt.Sprintf("agent account %q does not exist — create it first with "+
			"`jentic setup` or `jenticctl wizard` (see "+
			"docs/security/local-agent/local-agent-isolation.md), then re-run", agentUser))
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
		return a.runGrantDir(ctx, cmd, st, opts, agentID, agentUser, opts.grant)
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
	dir, err := a.resolveWorkingDir(ctx, cmd, st, opts, agentID, agentUser, posArgs)
	if err != nil {
		if errors.Is(err, errCancelled) {
			fmt.Fprintln(a.Out, theme.Dim.Render("Cancelled."))
			return nil
		}
		return err
	}

	// 3a. Hand the operator's ACTIVE context to the agent account: a minimal
	// config + the env-scoped credentials, freshly exported into the agent
	// home's own XDG store, so the `jentic` the agent runs inside the session
	// resolves the same environment/identity — in agent mode — without flags.
	if err := a.exportContextToAgent(ctx, acct); err != nil {
		return err
	}

	// 4. Launch (confined — see launchAgent for the error-closed contract).
	return a.launchAgent(ctx, acct, agentUser, binary, dir, agentArgs)
}

// runSameUser launches the agent binary directly as the operator, with no Unix
// user, no confinement, and no ACL grants. This is the path for an operator who
// never enabled agent-user isolation (HasAgentUser is false): `jentic run` is
// then just a convenient launcher that resolves the binary. It injects nothing
// into the environment — because it shares the operator's home, the agent's own
// `jentic` calls pick up the operator's active context from the XDG store. The
// working directory is the path argument if given, else the current directory
// (there is nothing to grant — the agent already runs with the operator's own
// filesystem access).
func (a *Cmd) runSameUser(ctx context.Context, st *config.AgentState, desc localagent.Descriptor, opts *runOptions, posArgs, agentArgs []string) error {
	binary, err := exec.LookPath(desc.Binary)
	if err != nil {
		return binaryMissingErr(fmt.Sprintf("%s is not installed or not on your PATH; install it, then re-run "+
			"(or run `jentic setup` to set up an isolated agent user)", desc.Binary))
	}

	a.warnSameUserOnce(st)

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
	// Same-user shares the operator's home, so the agent's `jentic` calls pick
	// up the operator's own active context from the XDG store — nothing to inject.
	wireGracefulCancel(c)
	if err := c.Run(); err != nil {
		var exit interface{ ExitCode() int }
		if errors.As(err, &exit) && exit.ExitCode() >= 0 {
			return &cmdcore.ExitCodeError{Code: exit.ExitCode()}
		}
		return fmt.Errorf("launch %s: %w", desc.Binary, err)
	}
	return nil
}

// warnSameUserOnce shows the one-time security notice that this launch runs the
// agent SAME-USER: no dedicated Unix account and no process confinement, so the
// agent has the operator's own filesystem access (its keys, browser session, and
// the jentic-one credential store are all reachable). The isolated path errors
// closed when it can't confine; this unconfined path is only reached when the
// operator never enabled isolation, so it must not fail silently-permissive — the
// operator should know the boundary isn't there and how to get it. The notice is
// shown once (persisted via SameUserNoticeSeen) so it informs without nagging every
// launch. Persisting is best-effort: a save failure just means it may show again.
func (a *Cmd) warnSameUserOnce(st *config.AgentState) {
	if st.SameUserNoticeSeen {
		return
	}
	fmt.Fprintln(a.Out, theme.Warnf(
		"Running the agent as YOU — no dedicated account, no confinement."))
	fmt.Fprintln(a.Out, theme.Dim.Render(
		"  It can read everything you can: your keys, browser session, and the jentic-one"))
	fmt.Fprintln(a.Out, theme.Dim.Render(
		"  credential store. Run `jentic setup` to isolate it behind its own Unix user."))
	fmt.Fprintln(a.Out, theme.Dim.Render("  (Shown once.)"))

	if _, err := config.MutateAgentState(a.Paths, func(s *config.AgentState) error {
		s.SameUserNoticeSeen = true
		return nil
	}); err != nil {
		return // best-effort: the notice may show again next launch
	}
	st.SameUserNoticeSeen = true
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
// ── step 3: working directory + access ───────────────────────────────────────

func (a *Cmd) resolveWorkingDir(ctx context.Context, cmd *cobra.Command, st *config.AgentState, opts *runOptions, agentID, agentUser string, args []string) (string, error) {
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
	if _, err := filepath.Abs(dir); err != nil {
		return "", err
	}
	// Canonicalize (absolute + cleaned + symlinks resolved) so the ban check, the
	// access probe, the ACL grant, and the recorded path all name the same on-disk
	// directory — a symlink can't be classified as safe here yet have its real
	// (banned) target stamped by the recursive grant.
	abs := localagent.Canonicalize(dir)

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

	if err := a.grantDir(ctx, st, agentUser, abs); err != nil {
		return "", err
	}
	fmt.Fprintln(a.Out, theme.Dim.Render("  Granted (persists across sessions; `jentic run "+agentID+" --list-grants` to review)."))
	fmt.Fprintln(a.Out, theme.Dim.Render("  This is OS-level access only — the agent still runs its own workspace-trust prompt."))
	return abs, nil
}

// ── step 4: launch ───────────────────────────────────────────────────────────

// launchIsolated is the programmatic entry to the confined launch used by flows
// that already know the agent user (setup's "start a session now?" offer).
// It reloads the recorded account (for grants + home), validates the user,
// exports the active context into the agent's own store, and launches confined
// — the same steps 3a/4 that a `jentic run` performs after its prompts.
func (a *Cmd) launchIsolated(ctx context.Context, agentUser, binary, dir string, agentArgs []string) error {
	if err := localagent.ValidateAgentUser(agentUser); err != nil {
		return err
	}
	st, err := config.LoadAgentState(a.Paths)
	if err != nil {
		return err
	}
	acct, _ := st.AgentAccount()
	if acct.User == "" {
		acct.User = agentUser
	}
	if err := a.exportContextToAgent(ctx, acct); err != nil {
		return err
	}
	return a.launchAgent(ctx, acct, agentUser, binary, dir, agentArgs)
}

// launchAgent starts the confined agent session. Confinement is REQUIRED: it
// closes the sibling-traversal leak that the coarse ACL grant leaves open, and it
// is what keeps the agent out of the operator's home. When this machine can't confine
// the process (no sandbox-exec on macOS; no bwrap / unprivileged userns on Linux)
// we ERROR CLOSED — refuse the launch rather than silently drop to an unconfined
// session — and point the operator at an alternative isolation route.
func (a *Cmd) launchAgent(ctx context.Context, acct config.AgentAccount, agentUser, binary, dir string, agentArgs []string) error {
	if missing := localagent.MissingPrereqs(); len(missing) > 0 {
		var b strings.Builder
		b.WriteString("confined agent sessions aren't available on this machine:\n")
		for _, p := range missing {
			fmt.Fprintf(&b, "  • %s\n", p.Reason)
			if p.Hint != "" {
				fmt.Fprintf(&b, "    %s\n", p.Hint)
			}
		}
		b.WriteString("  jentic run won't start an unconfined session, because that would expose the operator's\n")
		b.WriteString("  home beyond the directories granted. Install the prerequisites above and re-run, or run\n")
		b.WriteString("  this agent in isolation another way (e.g. inside Docker). See ")
		b.WriteString("docs/security/local-agent/sandbox-exec-plan.md")
		return confinementUnavailableErr(b.String())
	}

	grantedDirs := acct.GrantedDirs
	agentHome := acct.HomeDir
	if agentHome == "" {
		// Fall back to the conventional default so the sandbox re-allows the
		// agent's own home even if config predates HomeDir being recorded.
		agentHome = localagent.DefaultHomeDir(agentUser)
	}

	// M1 (review round-3 #6): re-validate the operator-editable inputs
	// IMMEDIATELY before the privileged launch, mirroring grantDir /
	// exportContextToAgent. These reach the SBPL profile / bwrap argv; the sinks
	// are independently defended (control-char strip, per-token shell-quoting),
	// but the `local-agent-isolation` rule requires "validated at the source AND
	// re-checked immediately before every privileged command" — grantedDirs were
	// only trusted from recorded config, and agentHome is unvalidated when
	// exportContextToAgent early-returned (no active context / file-less mode).
	// Fail closed on the first bad entry rather than relying on sink-only defence.
	if err := localagent.ValidateHomeDir(agentHome); err != nil {
		return confinementUnavailableErr(fmt.Sprintf("recorded agent home is invalid: %v", err))
	}
	for _, gd := range grantedDirs {
		if err := localagent.ValidateGrantPath(gd); err != nil {
			return confinementUnavailableErr(fmt.Sprintf("recorded granted directory %q is invalid: %v", gd, err))
		}
	}

	where := dir
	if where == "" {
		where = "the agent's home"
	}
	fmt.Fprintln(a.Out, theme.Infof("Launching %s as %s in %s (confined) ...", binary, agentUser, where))
	cmd := localagent.ConfineLaunchCmd(ctx, agentUser, binary, dir, agentHome, grantedDirs, agentArgs)
	cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, os.Stdout, os.Stderr
	// On a programmatic cancel (SIGINT/SIGTERM to jentic), terminate the confined
	// session gracefully instead of SIGKILLing sudo and orphaning the agent tree.
	wireGracefulCancel(cmd)
	if err := cmd.Run(); err != nil {
		var exit interface{ ExitCode() int }
		if errors.As(err, &exit) && exit.ExitCode() >= 0 {
			return &cmdcore.ExitCodeError{Code: exit.ExitCode()}
		}
		return fmt.Errorf("launch %s: %w", binary, err)
	}
	return nil
}

// ── management: --list-grants / --revoke ─────────────────────────────────────

func (a *Cmd) runListGrants(agentID, agentUser string, acct config.AgentAccount, hasAcct bool) error {
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
	a.PrintRevokeHint()
	return nil
}

// runGrantDir grants the agent access to dir and exits without launching — the
// standalone counterpart to --revoke. It reuses the in-launch access flow: if
// the agent already reaches dir it is a no-op; otherwise it applies the same
// danger-confirmation and scoped-ACL grant (grantDir) as `jentic run <agent>
// <path>` would, and records it.
func (a *Cmd) runGrantDir(ctx context.Context, cmd *cobra.Command, st *config.AgentState, opts *runOptions, agentID, agentUser, dir string) error {
	if _, err := filepath.Abs(dir); err != nil {
		return err
	}
	abs := localagent.Canonicalize(dir)

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
	if err := a.grantDir(ctx, st, agentUser, abs); err != nil {
		return err
	}
	fmt.Fprintln(a.Out, theme.Successf("Granted (persists across sessions; `jentic run %s --list-grants` to review).", agentID))
	return nil
}

func (a *Cmd) runRevoke(_ context.Context, st *config.AgentState, agentUser, dir string) error {
	if _, err := filepath.Abs(dir); err != nil {
		return err
	}
	// Canonicalize to match the form grants are recorded in, so revoking by the
	// symlink or a relative path still finds and drops the recorded (resolved)
	// entry rather than leaving it orphaned.
	abs := localagent.Canonicalize(dir)

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
	updated, err := config.MutateAgentState(a.Paths, func(s *config.AgentState) error {
		s.RemoveGrantedDir(abs)
		return nil
	})
	if err != nil {
		return err
	}
	*st = *updated
	fmt.Fprintln(a.Out, theme.Successf("Revoked."))
	return nil
}
