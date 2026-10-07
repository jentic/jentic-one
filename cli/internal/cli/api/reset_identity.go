package api

import (
	"fmt"
	"os"
	"path/filepath"

	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

// identityWipe is the resolved plan for wiping the operator's own jentic
// identity state: the XDG trees and (if still present) the legacy V1 tree.
// Directories are recorded only when they exist, so the plan is truthful.
type identityWipe struct {
	// configDir is the config tree (~/.config/jentic): config.yaml + keys/.
	configDir string
	// stateDir is the state tree (~/.local/state/jentic): tokens + API keys.
	stateDir string
	// legacyRoot is the V1 ~/.jentic profile tree (profiles/ + MIGRATED marker).
	// Only the identity material is wiped — the rest of ~/.jentic is jenticctl's
	// install root and reset must not touch it.
	legacyProfilesDir string
	legacyMarker      string
}

func (w identityWipe) any() bool {
	return w.configDir != "" || w.stateDir != "" || w.legacyProfilesDir != ""
}

// surveyIdentityWipe resolves which identity trees exist on this machine. It
// never errors: an unresolvable dir simply isn't wiped (and was never readable
// by the CLI either).
func surveyIdentityWipe(paths config.Paths) identityWipe {
	var w identityWipe
	if dir, err := sdkconfig.ConfigDir(); err == nil {
		if _, serr := os.Stat(dir); serr == nil {
			w.configDir = dir
		}
	}
	if dir, err := sdkconfig.StateDir(); err == nil {
		if _, serr := os.Stat(dir); serr == nil {
			w.stateDir = dir
		}
	}
	if dir := paths.ProfilesDir(); dir != "" {
		if _, serr := os.Stat(dir); serr == nil {
			w.legacyProfilesDir = dir
		}
	}
	if marker := filepath.Join(paths.Dir(), "MIGRATED"); marker != "" {
		if _, serr := os.Stat(marker); serr == nil {
			w.legacyMarker = marker
		}
	}
	return w
}

// execIdentityWipe deletes the surveyed identity trees. It runs entirely as the
// operator (reset is never launched under sudo), so it can only ever touch the
// invoking account's own dirs — never another user's. The plan was already shown
// and confirmed by the caller; this is the execution tail only. Local deletion is
// the whole job — we deliberately do NOT try to revoke tokens server-side (the
// tokens are typically expired, so revocation just prints 401 noise; and deleting
// the local key/tokens already severs this machine's access).
func (a *app) execIdentityWipe(w identityWipe) error {
	for _, dir := range []string{w.configDir, w.stateDir, w.legacyProfilesDir} {
		if dir == "" {
			continue
		}
		if err := os.RemoveAll(dir); err != nil {
			return fmt.Errorf("removing %s: %w", dir, err)
		}
		fmt.Fprintln(a.Out, theme.Infof("• removed %s", dir))
	}
	// Drop the MIGRATED marker with the legacy profiles: a marker without a
	// store is stale, and a future V1 tree (downgrade) should gate again.
	if w.legacyMarker != "" {
		if err := os.Remove(w.legacyMarker); err != nil && !os.IsNotExist(err) {
			return fmt.Errorf("removing %s: %w", w.legacyMarker, err)
		}
	}
	fmt.Fprintln(a.Out, theme.Successf("Your jentic identity state was reset."))
	return nil
}

// printIdentityWipePlan shows exactly which identity trees will be deleted, as
// part of the full-reset preview.
func (a *app) printIdentityWipePlan(w identityWipe) {
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Error.Render("⚠  DANGER ZONE — a full reset will PERMANENTLY remove YOUR OWN jentic "+
		"identity state from this account. This cannot be undone."))
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Warn.Render("  Identity state to delete (contexts, environments, identities, keys, tokens):"))
	for _, line := range []struct{ label, dir string }{
		{"config ", w.configDir},
		{"state  ", w.stateDir},
		{"legacy ", w.legacyProfilesDir},
	} {
		if line.dir == "" {
			continue
		}
		fmt.Fprintln(a.Out, theme.Dim.Render("    - "+line.label+" "+line.dir))
	}
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Success.Render("  NOT touched:"))
	fmt.Fprintln(a.Out, theme.Dim.Render("    - another user's config — this only affects the account you ran reset from"))
	fmt.Fprintln(a.Out, theme.Dim.Render("    - the jentic-one install itself (~/.jentic install root, data, logs)"))
	fmt.Fprintln(a.Out)
}
