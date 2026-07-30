package cmd

import (
	"fmt"
	"io"
	"os"
	"path/filepath"

	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/localagent"
	"github.com/jentic/jentic-one/cli/internal/profile"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

// identityTarget describes WHERE `register`/`bootstrap` write a newly provisioned
// profile and whether that location belongs to the shared agent account. When an
// agent account exists, identities land in the shared agent home
// (<config_dir>/profiles): they are chowned to the agent (so the agent can read
// its own 0600 key/tokens when it runs as itself) and CHECKED OUT there (the agent
// home's own default_profile), never touching the operator's own default. Without
// an account they land in the operator's ~/.jentic exactly as before.
type identityTarget struct {
	// paths is the profile store root the identity is written under.
	paths config.Paths
	// agentUser is the OS account that must own the files (empty when operator-owned).
	agentUser string
	// configDir is the agent's ~/.jentic that is handed over after writing (empty
	// when operator-owned).
	configDir string
	// agentOwned reports whether this target is the shared agent account's home.
	agentOwned bool
}

// resolveIdentityTarget decides where a new identity is provisioned. The gate is
// the account existing at all (AccountCreated + a recorded config_dir) — NOT
// merely "created in this run" — so a `register`/`bootstrap` invoked after the
// account was set up in an earlier run still lands the identity in the shared
// home. Enabled tracks AccountCreated, so an existing account is always active.
func (a *App) resolveIdentityTarget(cfg *config.FileConfig) identityTarget {
	if acct, ok := cfg.AgentAccount(); ok && acct.AccountCreated && acct.ConfigDir != "" {
		return identityTarget{
			paths:      config.Paths{Root: acct.ConfigDir},
			agentUser:  acct.User,
			configDir:  acct.ConfigDir,
			agentOwned: true,
		}
	}
	return identityTarget{paths: a.Paths}
}

// checkOutProfile makes the just-provisioned profile the active one. For an
// agent-owned target it is CHECKED OUT — written as the agent home's own
// default_profile so `jentic run` injects it as JENTIC_PROFILE — and this happens
// regardless of activateOperator, because that flag governs only the operator's
// separate default. For an operator-owned target the profile becomes the
// operator's default_profile only when activateOperator is set (register never
// activates; bootstrap does unless --no-activate).
func (a *App) checkOutProfile(target identityTarget, profileName string, activateOperator bool) error {
	if target.agentOwned {
		if err := config.SetDefaultProfile(target.paths, profileName); err != nil {
			return fmt.Errorf("check out agent profile %q: %w", profileName, err)
		}
		fmt.Fprintln(a.Out, theme.Successf("Checked out agent profile %q (used by `jentic run`).", profileName))
		return nil
	}
	if activateOperator {
		if err := config.SetDefaultProfile(target.paths, profileName); err != nil {
			return fmt.Errorf("set default profile: %w", err)
		}
		fmt.Fprintln(a.Out, theme.Successf("Active profile set to %q", profileName))
	}
	return nil
}

// handOffToAgent hands the agent's own config dir to the agent uid after the
// operator has written an identity into it. Files the operator created in the
// agent home are operator-owned, but the agent's 0600 key and tokens must be
// readable when it runs as itself, so the whole config dir is chowned. Best-effort:
// a chown failure is reported, not fatal (the identity is already provisioned).
func (a *App) handOffToAgent(target identityTarget) {
	if !target.agentOwned || target.configDir == "" {
		return
	}
	chown := localagent.ChownToAgentCmd(target.agentUser, target.configDir)
	chown.Stdout, chown.Stderr = a.Out, a.Err
	if err := chown.Run(); err != nil {
		fmt.Fprintln(a.Out, theme.Warnf("could not hand the agent config to %s: %v", target.agentUser, err))
		return
	}
	fmt.Fprintln(a.Out, theme.Dim.Render("Agent identity written to its own config: "+target.configDir))
}

// translateOperatorProfile moves a pre-existing operator-owned profile into the
// shared agent home when an agent account exists but the profile has not yet been
// written there. This is the non-agent → agent transition: an identity registered
// before the account existed (in the operator's ~/.jentic) is copied into
// <config_dir>/profiles/<name> and its operator-side original removed, so the
// switch to the isolated account carries the existing key/tokens/registration over
// rather than re-registering from scratch (which would mint a new agent_id needing
// fresh approval). The subsequent handOffToAgent chowns the copied files to the
// agent uid. Returns whether a translation happened.
func (a *App) translateOperatorProfile(target identityTarget, name string) (bool, error) {
	if !target.agentOwned {
		return false, nil
	}
	// Already present in the agent home? Nothing to translate — the agent store is
	// authoritative and we must not clobber it with an operator copy.
	agentNames, err := profile.List(target.paths)
	if err != nil {
		return false, err
	}
	for _, n := range agentNames {
		if n == name {
			return false, nil
		}
	}
	// Present operator-side to move?
	opNames, err := profile.List(a.Paths)
	if err != nil {
		return false, err
	}
	found := false
	for _, n := range opNames {
		if n == name {
			found = true
			break
		}
	}
	if !found {
		return false, nil
	}

	src := filepath.Join(a.Paths.ProfilesDir(), name)
	dst := filepath.Join(target.paths.ProfilesDir(), name)
	if err := copyTree(src, dst); err != nil {
		return false, fmt.Errorf("translate profile %q into the agent home: %w", name, err)
	}
	// Remove the operator's original: the profile is being handed over to the
	// isolated account, not duplicated across both stores.
	if op, oerr := profile.Open(a.Paths, name); oerr == nil {
		if derr := op.Delete(); derr != nil {
			fmt.Fprintln(a.Out, theme.Warnf("kept the operator copy of %q (could not remove it: %v)", name, derr))
		}
	}
	fmt.Fprintln(a.Out, theme.Dim.Render(fmt.Sprintf("Moved existing profile %q into the agent account's home.", name)))
	return true, nil
}

// copyTree recursively copies the file tree at src to dst, preserving each entry's
// permission bits (profiles hold 0600 secrets under a 0700 dir). It creates dst and
// any parents. Symlinks are not expected in a profile dir and are skipped.
func copyTree(src, dst string) error {
	info, err := os.Stat(src)
	if err != nil {
		return err
	}
	if info.IsDir() {
		if err := os.MkdirAll(dst, info.Mode().Perm()); err != nil {
			return err
		}
		entries, err := os.ReadDir(src)
		if err != nil {
			return err
		}
		for _, e := range entries {
			if e.Type()&os.ModeSymlink != 0 {
				continue
			}
			if err := copyTree(filepath.Join(src, e.Name()), filepath.Join(dst, e.Name())); err != nil {
				return err
			}
		}
		return nil
	}
	return copyFile(src, dst, info.Mode().Perm())
}

// copyFile copies a single regular file, creating dst with perm.
func copyFile(src, dst string, perm os.FileMode) error {
	in, err := os.Open(src) //nolint:gosec // src is a resolved path under the operator's own ~/.jentic/profiles.
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := os.OpenFile(dst, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, perm) //nolint:gosec // dst is under the agent home the operator has ACL write to.
	if err != nil {
		return err
	}
	if _, err := io.Copy(out, in); err != nil {
		_ = out.Close()
		return err
	}
	return out.Close()
}
