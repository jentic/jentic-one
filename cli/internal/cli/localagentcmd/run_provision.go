package localagentcmd

import (
	"context"
	"errors"
	"fmt"
	"os"

	"github.com/charmbracelet/huh"
	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/cli/prompt"
	"github.com/jentic/jentic-one/cli/internal/localagent"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

// run_provision.go holds the "make the agent runnable" concern extracted from
// run.go (ARCH-24): switching to the agent user, ensuring the agent binary is
// present + on PATH, and provisioning it (copy the operator's binary or install
// a fresh one) when it is missing. run.go keeps command wiring, working-dir
// resolution, and the launch itself; grant policy lives in run_grants.go.

// ── step 1: switch to the agent user ─────────────────────────────────────────

func (a *Cmd) ensureCanRunAsAgent(ctx context.Context, agentUser string) error {
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

func (a *Cmd) ensureAgentBinary(ctx context.Context, cmd *cobra.Command, opts *runOptions, agentUser string, desc localagent.Descriptor) error {
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
func (a *Cmd) ensureLocalBinOnPath(agentUser string) error {
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
func (a *Cmd) ensureSharedBinsOnPath(agentUser string) error {
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

func (a *Cmd) provisionBinary(ctx context.Context, cmd *cobra.Command, opts *runOptions, agentUser string, desc localagent.Descriptor) error {
	fmt.Fprintln(a.Out, theme.Warnf("Agent %q is not installed for user %s.", desc.ID, agentUser))

	opBin := ""
	if desc.SingleBinary {
		opBin = localagent.OperatorBinaryPath(ctx, desc.Binary)
	}

	choice := "copy"
	if opBin == "" {
		choice = "install"
	}
	if cmdcore.WantsInteractive(cmd, opts.yes) {
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
		agentHome, err := localagent.LookupHomeDir(agentUser)
		if err != nil {
			return err
		}
		cp := localagent.CopyBinaryCmd(agentUser, agentHome, opBin, desc.Binary)
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
		return binaryMissingErr("agent binary not installed")
	}
	return nil
}

func (a *Cmd) pickProvisionRoute(desc localagent.Descriptor, agentUser, opBin string) (string, error) {
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
	err := prompt.NewForm(huh.NewGroup(
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
