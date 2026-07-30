package cmd

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/charmbracelet/huh"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/install"
	"github.com/jentic/jentic-one/cli/internal/proc"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

type uninstallOptions struct {
	yes      bool
	purge    bool
	keepData bool
}

// localStopTimeout bounds how long uninstall waits for a local background
// app/broker process to exit on SIGTERM before escalating to SIGKILL.
const localStopTimeout = 10 * time.Second

func newUninstallCmd(app *App) *cobra.Command {
	opts := &uninstallOptions{}
	cmd := &cobra.Command{
		Use:   "uninstall",
		Short: "Remove everything under ~/.jentic (config files are backed up to *-old)",
		Long: "uninstall deletes the contents of ~/.jentic (venv, source, data, logs,\n" +
			"profiles, CA, etc.). Config files are preserved: each is renamed to a\n" +
			"'-old' backup so you can restore your settings later. The ~/.jentic\n" +
			"directory itself is kept.\n\n" +
			"For a Docker install the database lives in a named volume (SQLite data or\n" +
			"the managed Postgres data dir), not under ~/.jentic. uninstall tears the\n" +
			"stack down but PRESERVES that volume by default, so a later `install`\n" +
			"reattaches your data. The credential-encryption key for that data lives\n" +
			"in the jentic-one-old.yaml backup — install auto-reuses it so encrypted\n" +
			"credentials stay readable; deleting the backup by hand strands them.\n" +
			"Use --purge to also delete the data volume (this destroys the database),\n" +
			"or --keep-data to force preservation. With no flag and an interactive\n" +
			"terminal, uninstall asks before deleting it.\n\n" +
			"On a local (source) install it stops the background app/broker processes\n" +
			"first so they aren't orphaned when their files are removed.",
		Args: cobra.NoArgs,
		RunE: func(_ *cobra.Command, _ []string) error {
			return app.uninstallE(opts)
		},
	}
	cmd.Flags().BoolVar(&opts.yes, "yes", false, "skip the confirmation prompts")
	cmd.Flags().BoolVar(&opts.purge, "purge", false,
		"also remove the Docker stack's data volume (destroys the database)")
	cmd.Flags().BoolVar(&opts.keepData, "keep-data", false,
		"preserve the Docker stack's data volume (the default; reinstall reattaches it "+
			"and reuses jentic-one-old.yaml's encryption key)")
	return cmd
}

// preservedConfigs maps a config filename to the backup name it's moved to.
var preservedConfigs = map[string]string{
	config.InstallConfigName: config.BackupName(config.InstallConfigName), // jentic-one.yaml -> jentic-one-old.yaml
	config.ConfigName:        config.BackupName(config.ConfigName),        // config.yaml -> config-old.yaml
}

func (a *App) uninstallE(opts *uninstallOptions) error {
	if opts.purge && opts.keepData {
		return errors.New("--purge and --keep-data are mutually exclusive")
	}

	dir := a.Paths.Dir()

	entries, err := os.ReadDir(dir)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			fmt.Fprintln(a.Out, theme.Dimf("Nothing to do: %s does not exist.", dir))
			return nil
		}
		return fmt.Errorf("read %s: %w", dir, err)
	}
	if len(entries) == 0 {
		// An empty ~/.jentic means nothing was installed (no compose file, pid
		// files, or data to clean up). Treat it like a missing directory rather
		// than running the teardown/backup phases against nothing.
		fmt.Fprintln(a.Out, theme.Dimf("Nothing to do: %s is empty.", dir))
		return nil
	}

	if !opts.yes {
		ok, err := confirmUninstall(dir)
		if err != nil {
			return err
		}
		if !ok {
			fmt.Fprintln(a.Out, theme.Dim.Render("uninstall cancelled"))
			return nil
		}
	}

	// Docker teardown runs before the filesystem removal below, since
	// `docker compose -f <composePath> down` needs the compose file to exist
	// (Phase 2 deletes it). A generated compose file marks a Docker install.
	composePath := a.Paths.ComposePath()
	if proc.FileExists(composePath) {
		if err := a.uninstallDocker(opts, composePath); err != nil {
			return err
		}
	} else if opts.purge || opts.keepData {
		// --purge/--keep-data only govern the Docker named volume; a local
		// (non-Docker) install keeps its database under ~/.jentic/data, which
		// the Phase 2 wipe below deletes unconditionally. Warn so --keep-data
		// isn't a silent footgun that destroys the data it names. Mirrors the
		// "--volumes has no effect" warning in stop.go.
		fmt.Fprintln(a.Out, theme.Warnf("--purge/--keep-data only apply to a Docker install; "+
			"this is a local install, so the database under %s will be deleted.", a.Paths.DataDir()))
	}

	// Stop any local background app/broker before deleting their files. The
	// Docker stack (and its data volume) was already handled above by
	// uninstallDocker; this covers the local (source) install path where the
	// app/broker run as background processes recorded in pid files. Deleting
	// venv/src/logs out from under live processes orphans them, so signal them
	// first. Guarded on the pid file so this is a true no-op (no output) on a
	// pure-Docker install, and best-effort/non-fatal so a stuck process can't
	// block the cleanup.
	if proc.FileExists(a.Paths.AppPIDPath()) {
		if err := a.stopProcess(a.Paths.AppPIDPath(), "app", localStopTimeout); err != nil {
			fmt.Fprintln(a.Out, theme.Warnf("could not stop app (continuing): %v", err))
		}
	}
	if proc.FileExists(a.Paths.BrokerPIDPath()) {
		if err := a.stopProcess(a.Paths.BrokerPIDPath(), "broker", localStopTimeout); err != nil {
			fmt.Fprintln(a.Out, theme.Warnf("could not stop broker (continuing): %v", err))
		}
	}

	// Phase 1: back up config files (overwriting any prior backup).
	backups := map[string]bool{}
	for name, backup := range preservedConfigs {
		src := filepath.Join(dir, name)
		if _, statErr := os.Stat(src); statErr != nil {
			continue
		}
		dst := filepath.Join(dir, backup)
		if err := os.Rename(src, dst); err != nil {
			return fmt.Errorf("back up %s: %w", name, err)
		}
		backups[backup] = true
		fmt.Fprintln(a.Out, theme.Dimf("backed up %s -> %s", name, backup))
	}

	// Phase 2: remove everything else from the snapshot, skipping the freshly
	// created backups (their filenames may collide with prior backups in the
	// snapshot) and the original config names (already renamed away).
	for _, e := range entries {
		name := e.Name()
		if backups[name] || preservedConfigs[name] != "" {
			continue
		}
		if err := os.RemoveAll(filepath.Join(dir, name)); err != nil {
			return fmt.Errorf("remove %s: %w", name, err)
		}
	}

	fmt.Fprintln(a.Out, theme.Successf("Removed jentic-one state under %s (config backed up).", dir))
	return nil
}

// uninstallDocker tears the Docker stack down before the filesystem teardown.
// It always stops/removes the containers so no stale container stays bound to
// the old data volume. The volume itself is preserved unless purging is
// requested (via --purge or an interactive confirmation) — a reinstall then
// reattaches the old database.
//
// Unlike stopDocker, the docker steps are best-effort: if docker is missing or
// the daemon is down, it warns and returns nil so the filesystem cleanup still
// proceeds (a user uninstalling may have already removed Docker).
func (a *App) uninstallDocker(opts *uninstallOptions, composePath string) error {
	if _, err := exec.LookPath("docker"); err != nil {
		fmt.Fprintln(a.Out, theme.Warnf("docker not found on PATH; skipping Docker teardown. "+
			"Any stack containers or the data volume may still exist."))
		return nil //nolint:nilerr // best-effort: docker absent must not abort the filesystem teardown.
	}

	// Decide whether to remove the data volume. Flags win; otherwise prompt on
	// an interactive run, and default to keep-data for unattended (--yes) runs.
	purge := opts.purge
	if !opts.purge && !opts.keepData && !opts.yes {
		ok, err := confirmUninstallVolumes(a.dataVolumeLabel())
		if err != nil {
			return err
		}
		purge = ok
	}

	if purge {
		fmt.Fprintln(a.Out, theme.Infof("Stopping Docker stack and removing the data volume ..."))
		if err := install.ComposeDownVolumes(a.Out, composePath); err != nil {
			vols := strings.Join(a.uninstallVolumeHint(), " ")
			fmt.Fprintln(a.Out, theme.Warnf("docker compose down -v failed (continuing): %v", err))
			fmt.Fprintln(a.Out, theme.Dimf("the database volume may survive; once the daemon is up remove it with: docker volume rm %s", vols))
			return nil
		}
		// `docker compose down -v` only removes volumes declared in the compose
		// file under the pinned project. A volume created under a different
		// project name (a pre-pinning install, a regenerated compose file, etc.)
		// survives that otherwise-successful teardown, so remove the known
		// project-prefixed volume(s) by name too. Best-effort: a failure here is
		// non-fatal and points the operator at the manual removal.
		hints := a.uninstallVolumeHint()
		removed, rmErr := install.RemoveDataVolumes(a.Out, hints)
		if rmErr != nil {
			fmt.Fprintln(a.Out, theme.Warnf("could not remove the data volume by name (continuing): %v", rmErr))
			fmt.Fprintln(a.Out, theme.Dimf("remove it by hand with: docker volume rm %s", strings.Join(hints, " ")))
			return nil
		}
		if len(removed) > 0 {
			fmt.Fprintln(a.Out, theme.Successf("Stopped Docker stack and removed its data volume (%s).", strings.Join(removed, ", ")))
		} else {
			fmt.Fprintln(a.Out, theme.Successf("Stopped Docker stack; no data volume found to remove (already gone)."))
		}
		return nil
	}

	fmt.Fprintln(a.Out, theme.Infof("Stopping Docker stack (preserving the data volume) ..."))
	if err := install.ComposeDown(a.Out, composePath); err != nil {
		fmt.Fprintln(a.Out, theme.Warnf("docker compose down failed (continuing): %v", err))
		return nil
	}
	fmt.Fprintln(a.Out, theme.Successf("Stopped Docker stack; data volume preserved (use --purge to delete it)."))
	return nil
}

// dataVolumeLabel describes the database backend for volume-removal messaging,
// read from the manifest. A missing/unreadable manifest yields a generic label.
func (a *App) dataVolumeLabel() string {
	m, found, err := config.LoadManifest(a.Paths)
	if err != nil || !found {
		return "the database"
	}
	switch m.DB {
	case "postgres":
		return "the Postgres database"
	case "sqlite":
		return "the SQLite database"
	default:
		return "the database"
	}
}

// uninstallVolumeHint returns the docker volume name(s) to suggest for manual
// removal when `docker compose down -v` could not run. It reads the install
// manifest to name the precise volume (SQLite's jentic_jentic-data vs Postgres'
// jentic_db-data). When the manifest is missing or unreadable — e.g. an install
// from before the manifest existed — the mode is unknown, so it lists BOTH
// candidates rather than guessing and pointing the operator at the wrong one.
func (a *App) uninstallVolumeHint() []string {
	m, found, err := config.LoadManifest(a.Paths)
	if err != nil || !found || m == nil {
		return install.AllDataVolumeNames()
	}
	return install.DataVolumeNames(strings.EqualFold(m.DB, "postgres"))
}

func confirmUninstall(dir string) (bool, error) {
	confirm := false
	if err := install.RunConfirm(
		huh.NewConfirm().
			Title(fmt.Sprintf("Delete everything under %s?", dir)).
			Description("Config files are backed up to *-old. This also stops the stack; " +
				"the Docker data volume is preserved unless you choose to delete it.").
			Affirmative("Yes, uninstall").
			Negative("Cancel").
			Value(&confirm),
	); err != nil {
		if errors.Is(err, huh.ErrUserAborted) {
			return false, nil
		}
		return false, err
	}
	return confirm, nil
}

// confirmUninstallVolumes prompts before removing the Docker data volume, which
// permanently deletes the database. Declining preserves the volume so a later
// reinstall reattaches the old data.
//
// Aborting the prompt (Ctrl+C) is treated as "keep the data": the user already
// confirmed the overall uninstall at the first prompt, so the safe default here
// is the non-destructive one — preserve the volume and let the teardown finish.
func confirmUninstallVolumes(dbLabel string) (bool, error) {
	confirm := false
	if err := install.RunConfirm(
		huh.NewConfirm().
			Title(fmt.Sprintf("Also delete the Docker data volume? This permanently deletes %s.", dbLabel)).
			Description("Keep it to let a later `install` reattach your data. " +
				"The backup jentic-one-old.yaml holds the encryption key install " +
				"needs to keep the reattached credentials readable — don't delete it.").
			Affirmative("Yes, delete the data").
			Negative("No, keep the data").
			Value(&confirm),
	); err != nil {
		if errors.Is(err, huh.ErrUserAborted) {
			return false, nil
		}
		return false, err
	}
	return confirm, nil
}
