package localagentcmd

import (
	"context"
	"errors"
	"fmt"
	"os/exec"
	"strings"

	"github.com/charmbracelet/huh"
	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/cli/prompt"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/localagent"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

// run_grants.go holds the directory-grant policy concern extracted from run.go
// (ARCH-24): applying the "traverse-walk + rwx-leaf" ACL model, running the ACL
// commands with benign-race classification, and the interactive/flag decisions
// about whether a directory may be granted at all. run.go keeps command wiring
// and the launch; binary provisioning lives in run_provision.go.

// errCancelled signals a user-initiated cancel that runE turns into a clean exit.
var errCancelled = errors.New("cancelled")

// grantDir applies the "traverse-walk + rwx-leaf" ACL model so the agent uid can
// read/write abs. For a path under the home it (1) opens execute-only traverse on
// each ancestor the agent can't already pass through, then (2) grants the rwx leaf.
// For a path outside the home the leaf grant alone suffices. These grants only ever
// OPEN access (the sandbox is intersection-only, so a DAC grant is still required);
// the sibling-traversal leak they leave open is closed per session by the
// process-confinement layer (see localagent/confine.go), not by an ACL deny sweep.
// All grants are scoped to the agent user and never touch the operator's own
// permissions.
func (a *Cmd) grantDir(ctx context.Context, cfg *config.FileConfig, agentUser, abs string) error {
	// SEC-22: belt-and-suspenders — validate the resolved grant path at the sink
	// before ANY privileged ACL command is built, mirroring how export/reset
	// guard HomeDir via ValidateHomeDir. Injection is already prevented downstream
	// (Canonicalize + Classify + shellQuote/sbplPath), but a single explicit
	// boundary check (absolute, no control chars, non-empty) rejects a malformed
	// path up front on every path into grantDir (in-launch resolve AND --grant).
	if err := localagent.ValidateGrantPath(abs); err != nil {
		return err
	}

	// Canonicalize home to match abs (already canonical from the callers): the
	// under-home test and ancestor walk must reason about the same resolved tree.
	home := localagent.Canonicalize(localagent.OperatorHome())

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
	// Positively confirm the leaf ACE actually landed rather than trusting the exit
	// code + stderr classification alone: runGrant treats a benign mid-scan race as
	// success, and a subtly malformed ACE spec could exit zero without granting. Read
	// the ACL back and require the agent's entry to be present before recording —
	// otherwise we'd persist a grant that doesn't exist on disk, and a later launch
	// would silently fail to reach the directory. This closes the gap between "the
	// grant command returned" and "the agent can actually read/write here".
	if !localagent.AgentACLPresent(ctx, agentUser, abs) {
		return fmt.Errorf("grant directory access: the access-control entry for %s did not "+
			"appear on %s after granting — not recording it", agentUser, abs)
	}
	// Record the grant under the config lock, reloading first, so a concurrent
	// `jentic run` granting a different directory can't drop this one (each would
	// otherwise load, append its own dir, and the last Save would win). Mutate
	// returns the committed config; adopt it so the in-memory cfg stays current.
	updated, err := config.Mutate(a.Paths, func(c *config.FileConfig) error {
		c.AddGrantedDir(abs)
		return nil
	})
	if err != nil {
		return err
	}
	*cfg = *updated
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
func (a *Cmd) runGrant(c *exec.Cmd, what string) error {
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
func (a *Cmd) decideDirGrant(cmd *cobra.Command, opts *runOptions, agentUser, dir string, verdict localagent.DangerVerdict) (bool, error) {
	// A banned path can never be granted, by any flag or prompt.
	if verdict.Banned() {
		if opts.allowDir {
			return false, fmt.Errorf("refusing to grant a protected directory (%s); "+
				"this path cannot be handed to the agent — pick a directory outside it", verdict.Reason)
		}
		if !cmdcore.WantsInteractive(cmd, opts.yes) {
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
	if !cmdcore.WantsInteractive(cmd, opts.yes) {
		return false, nil
	}
	return a.confirmPlainGrant(agentUser, dir)
}

func (a *Cmd) confirmPlainGrant(agentUser, dir string) (bool, error) {
	fmt.Fprintln(a.Out, theme.Warnf("Agent %s has no access to %s.", agentUser, dir))
	// Focus "Allow" by default: this is an ordinary (non-banned) workspace the
	// operator explicitly asked to open, so granting is the expected choice. huh
	// focuses the option whose value matches the bound field's current value.
	choice := "allow"
	err := prompt.NewForm(huh.NewGroup(
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
func (a *Cmd) confirmBannedPath(agentUser, dir string, verdict localagent.DangerVerdict) (bool, error) {
	fmt.Fprintln(a.Out, theme.Error.Render("⚠  "+dir))
	fmt.Fprintln(a.Out, theme.Warnf("   %s can't be granted access here: %s.", agentUser, verdict.Reason))
	fmt.Fprintln(a.Out, theme.Dim.Render("   This directory is a protected boundary and cannot be handed to the agent."))

	var choice string
	err := prompt.NewForm(huh.NewGroup(
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

// PrintRevokeHint prints a small footer, under any directory-access tree, telling
// the operator how to take a grant away again. It mirrors the "Granted (…)" line
// the grant flow prints, so revocation is always one command away from wherever
// access is shown. Grants are account-scoped (one set for every agent binary), so
// the hint is generic over which `<agent>` binary the operator names.
func (a *Cmd) PrintRevokeHint() {
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Dim.Render("To take a directory away: `jentic run <agent> --revoke <dir>` "+
		"(`--list-grants` to review)."))
}
