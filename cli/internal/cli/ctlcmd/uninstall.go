package ctlcmd

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/charmbracelet/huh"
	"github.com/jentic/jentic-one/cli/internal/cli/localagentcmd"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/install"
	"github.com/jentic/jentic-one/cli/internal/proc"
	"github.com/jentic/jentic-one/cli/internal/skillgen"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

type uninstallOptions struct {
	yes                 bool
	purge               bool
	keepData            bool
	removeSkills        bool
	removeProjectSkills bool
	dryRun              bool
}

// localStopTimeout bounds how long uninstall waits for a local background
// app/broker process to exit on SIGTERM before escalating to SIGKILL.
const localStopTimeout = 10 * time.Second

func newUninstallCmd(app *app) *cobra.Command {
	opts := &uninstallOptions{}
	cmd := &cobra.Command{
		Use:   "uninstall",
		Short: "Remove everything under ~/.jentic (config files are backed up to *-old)",
		Long: "uninstall deletes the contents of ~/.jentic (venv, source, data, logs,\n" +
			"CA, etc.). Config files are preserved: each is renamed to a\n" +
			"'-old' backup so you can restore your settings later. Your agent\n" +
			"identity (~/.jentic/profiles) is ALSO preserved — reinstalling keeps you\n" +
			"authenticated; run `jentic reset` to remove it. The ~/.jentic\n" +
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
			"first so they aren't orphaned when their files are removed.\n\n" +
			"Agent skills installed by `jentic skill init` live outside ~/.jentic\n" +
			"(e.g. ~/.claude/skills/jentic, AGENTS.md managed blocks). By default\n" +
			"uninstall leaves them in place and lists any that remain, with the\n" +
			"command to remove them (`jentic skill remove --all`).\n" +
			"--remove-skills (implied by --purge) also removes the user-scope\n" +
			"installs under your home directory. Project-scope installs (./AGENTS.md\n" +
			"blocks, ./.claude|.cursor|.hermes/skills/jentic) are found relative to\n" +
			"the current directory and are only removed with --remove-project-skills.\n" +
			"Uninstall lists the exact files first and asks before removing them\n" +
			"unless --yes is given. Manually edited skills, and files at a skill path\n" +
			"that Jentic did not write, are kept and reported. --dry-run shows what\n" +
			"would be removed and changes nothing. If a skill cannot be removed,\n" +
			"uninstall still finishes the teardown, reports the failure, and exits\n" +
			"non-zero.",
		Example: "  jenticctl uninstall\n" +
			"  jenticctl uninstall --dry-run --purge\n" +
			"  jenticctl uninstall --yes --keep-data\n" +
			"  jenticctl uninstall --yes --remove-skills\n" +
			"  jenticctl uninstall --yes --remove-project-skills\n" +
			"  jenticctl uninstall --yes --purge",
		Args: cobra.NoArgs,
		RunE: func(_ *cobra.Command, _ []string) error {
			return app.uninstallE(opts)
		},
	}
	cmd.Flags().BoolVarP(&opts.yes, "yes", "y", false, "skip the confirmation prompts")
	cmd.Flags().BoolVar(&opts.purge, "purge", false,
		"also remove the Docker stack's data volume (destroys the database) and the user-scope "+
			"Jentic agent skills (never touches the current directory)")
	cmd.Flags().BoolVar(&opts.keepData, "keep-data", false,
		"preserve the Docker stack's data volume (the default; reinstall reattaches it "+
			"and reuses jentic-one-old.yaml's encryption key)")
	cmd.Flags().BoolVar(&opts.removeSkills, "remove-skills", false,
		"also remove the user-scope Jentic agent skills under your home directory (manually edited ones are kept)")
	cmd.Flags().BoolVar(&opts.removeProjectSkills, "remove-project-skills", false,
		"also remove project-scope Jentic agent skills under the current directory "+
			"(./AGENTS.md blocks, ./.claude|.cursor|.hermes/skills/jentic); implies --remove-skills")
	cmd.Flags().BoolVar(&opts.dryRun, "dry-run", false,
		"list what uninstall would remove, including agent skills, and change nothing")
	return cmd
}

// preservedConfigs maps a config filename to the backup name it's moved to.
var preservedConfigs = map[string]string{
	config.InstallConfigName: config.BackupName(config.InstallConfigName), // jentic-one.yaml -> jentic-one-old.yaml
	config.ConfigName:        config.BackupName(config.ConfigName),        // config.yaml -> config-old.yaml
}

// preservedNames are entries under ~/.jentic that uninstall must NOT delete: the
// operator's own agent identity (profiles/ — Ed25519 keys, tokens, per-agent
// meta) is NOT an install artifact, and tearing it down here would silently
// revoke the operator's ability to re-authenticate after a reinstall. That
// teardown belongs to `jentic reset` (CLI-V2 impl/5.1). The config backups
// (config-old.yaml) already carry the local-agent account record. Kept as a
// set so Phase 2's wipe skips these by name.
var preservedNames = map[string]bool{
	"profiles": true, // per-agent identity (agent.key, tokens.json, profile.yaml)
}

func (a *app) uninstallE(opts *uninstallOptions) error {
	if opts.purge && opts.keepData {
		return errors.New("--purge and --keep-data are mutually exclusive")
	}

	dir := a.Paths.Dir()
	// One read-only probe of the installed agent skills drives the preview,
	// the confirmation, the removal, and the leftover report.
	plan := a.planSkillCleanup(opts)

	entries, err := os.ReadDir(dir)
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return fmt.Errorf("read %s: %w", dir, err)
	}
	if err != nil || len(entries) == 0 {
		// A missing or empty ~/.jentic means nothing was installed (no compose
		// file, pid files, or data to clean up), so there is no teardown. Skills
		// can outlive it (e.g. a hand-deleted ~/.jentic), so the explicit skill
		// flags still apply.
		state := "does not exist"
		if err == nil {
			state = "is empty"
		}
		fmt.Fprintln(a.Out, theme.Dimf("Nothing to do: %s %s.", dir, state))
		return a.skillsOnlyCleanup(dir, opts, plan)
	}

	if opts.dryRun {
		fmt.Fprintln(a.Out, theme.Infof("Would remove the jentic-one state under %s (config files backed up to *-old; "+
			"the Docker data volume is kept unless --purge).", dir))
		a.previewSkillCleanup(plan, true)
		a.reportRemainingSkills(plan, nil, true)
		fmt.Fprintln(a.Out, theme.Dim.Render("Dry run — nothing was changed."))
		return nil
	}

	a.previewSkillCleanup(plan, false)
	if !opts.yes {
		ok, err := confirmUninstall(dir, len(plan.remove))
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
		// --purge/--keep-data primarily govern the Docker named volume. On a
		// local (non-Docker) install the database lives under ~/.jentic/data,
		// which the Phase 2 wipe deletes. --keep-data preserves it (see the
		// preserved set below); --purge has no extra data effect on a local
		// install (the wipe already removes everything), so say so rather than
		// leave the operator guessing what it did.
		if opts.purge {
			fmt.Fprintln(a.Out, theme.Warnf("--purge has no extra data effect on a local install; the database under %s "+
				"is removed by the normal teardown (pass --keep-data to preserve it).", a.Paths.DataDir()))
		}
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
	// snapshot), the original config names (already renamed away), and the
	// preserved identity entries (profiles/ — see preservedNames; `jentic reset`
	// owns that teardown, not uninstall).
	preserved := map[string]bool{}
	for name := range preservedNames {
		preserved[name] = true
	}
	// Resolve the real profiles dir name from Paths so a rename of the constant
	// can't silently un-preserve it.
	preserved[filepath.Base(a.Paths.ProfilesDir())] = true
	// --keep-data on a LOCAL install preserves the SQLite data dir, not just
	// warns: its basename joins the name-based skip set so the Phase 2 wipe
	// leaves it. (On a Docker install the data is a named volume handled by
	// uninstallDocker, so this local-only branch never applies there.)
	keptData := false
	if opts.keepData {
		preserved[filepath.Base(a.Paths.DataDir())] = true
	}
	skippedIdentity := false
	for _, e := range entries {
		name := e.Name()
		if backups[name] || preservedConfigs[name] != "" {
			continue
		}
		if preserved[name] {
			skippedIdentity = true
			if opts.keepData && name == filepath.Base(a.Paths.DataDir()) {
				keptData = true
			}
			continue
		}
		if err := os.RemoveAll(filepath.Join(dir, name)); err != nil {
			return fmt.Errorf("remove %s: %w", name, err)
		}
	}
	if keptData {
		fmt.Fprintln(a.Out, theme.Dimf("kept the database under %s (--keep-data)", a.Paths.DataDir()))
	}

	fmt.Fprintln(a.Out, theme.Successf("Removed jentic-one state under %s (config backed up).", dir))
	if skippedIdentity {
		fmt.Fprintln(a.Out, theme.Dimf("Kept your agent identity under %s; run `jentic reset` to remove it.", a.Paths.ProfilesDir()))
	}

	// Skills live in operator config OUTSIDE ~/.jentic (e.g. ~/.claude/skills,
	// AGENTS.md), so the teardown above never touches them. They are removed
	// only after it succeeded: a failed backup or wipe returns early with every
	// skill still in place, so a re-run sees the same plan instead of a
	// half-uninstalled machine with its skills already gone.
	return a.finishSkillCleanup(dir, plan)
}

// skillCleanup is the slice of the local-agent skill engine uninstall needs:
// a read-only probe and a guarded removal over skillgen.Remove, the engine
// `jentic skill remove` uses. Tests swap it out.
type skillCleanup interface {
	SkillCleanupTargets() ([]localagentcmd.SkillCleanupTarget, error)
	RemoveSkillCleanupTargets(targets []localagentcmd.SkillCleanupTarget, dryRun bool) ([]localagentcmd.SkillCleanupOutcome, error)
}

// newSkillCleanup builds the skill engine uninstall uses. A package variable so
// tests can inject a failing implementation.
var newSkillCleanup = func(a *app) skillCleanup { return localagentcmd.New(a.App) }

// skillPlan is uninstall's view of the installed agent skills: everything the
// probe found, and the subset this run removes.
type skillPlan struct {
	requested      bool // a skill-removal flag was given
	includeProject bool // --remove-project-skills: project scope is in play
	installed      []localagentcmd.SkillCleanupTarget
	remove         []localagentcmd.SkillCleanupTarget // removable, in a requested scope
	detectErr      error
}

// planSkillCleanup probes the installed skills once and selects the removal
// set. --purge and --remove-skills cover user scope only; project-scope
// installs resolve against the current directory, which may be a team repo
// with the skill committed on purpose, so they need --remove-project-skills.
func (a *app) planSkillCleanup(opts *uninstallOptions) skillPlan {
	p := skillPlan{
		requested:      opts.purge || opts.removeSkills || opts.removeProjectSkills,
		includeProject: opts.removeProjectSkills,
	}
	p.installed, p.detectErr = newSkillCleanup(a).SkillCleanupTargets()
	if !p.requested {
		return p
	}
	for _, t := range p.installed {
		if t.Removable() && p.inScope(t) {
			p.remove = append(p.remove, t)
		}
	}
	return p
}

func (p skillPlan) inScope(t localagentcmd.SkillCleanupTarget) bool {
	return t.Scope == skillgen.ScopeUser || p.includeProject
}

// previewSkillCleanup lists the exact skill files this run removes, before
// anything is confirmed or changed.
func (a *app) previewSkillCleanup(p skillPlan, dryRun bool) {
	if !p.requested || p.detectErr != nil {
		return
	}
	if len(p.remove) == 0 {
		fmt.Fprintln(a.Out, theme.Dim.Render("No removable Jentic skill installs found."))
		return
	}
	verb := "Uninstall will remove"
	if dryRun {
		verb = "Would remove"
	}
	fmt.Fprintln(a.Out, theme.Infof("%s these Jentic skill installs:", verb))
	for _, t := range p.remove {
		fmt.Fprintln(a.Out, theme.Dim.Render(skillLine(t)))
	}
}

// skillsOnlyCleanup handles the agent skills when there is no ~/.jentic
// install to tear down. Removal still previews and, without --yes, asks first:
// it edits files outside ~/.jentic, so it never runs unconfirmed.
func (a *app) skillsOnlyCleanup(dir string, opts *uninstallOptions, p skillPlan) error {
	if opts.dryRun {
		a.previewSkillCleanup(p, true)
		a.reportRemainingSkills(p, nil, true)
		fmt.Fprintln(a.Out, theme.Dim.Render("Dry run — nothing was changed."))
		return nil
	}
	a.previewSkillCleanup(p, false)
	if len(p.remove) > 0 && !opts.yes {
		ok, err := askSkillRemoval(len(p.remove))
		if err != nil {
			return err
		}
		if !ok {
			fmt.Fprintln(a.Out, theme.Dim.Render("skill removal cancelled"))
			p.remove = nil
			p.requested = false
		}
	}
	return a.finishSkillCleanup(dir, p)
}

// finishSkillCleanup removes the planned skills and reports what remains. A
// detection or removal failure never undoes the teardown that already ran,
// but when removal was asked for it is returned so uninstall exits non-zero
// rather than claiming a clean machine.
func (a *app) finishSkillCleanup(dir string, p skillPlan) error {
	const removeCmd = "jentic skill remove --all"
	if p.detectErr != nil {
		fmt.Fprintln(a.Out, theme.Warnf("could not detect installed skills: %v", p.detectErr))
		fmt.Fprintln(a.Out, theme.Dimf("Installed skill blocks live outside %s; remove them with: %s", dir, removeCmd))
		if p.requested {
			return fmt.Errorf("uninstall finished, but the agent skills were not removed: detect installed skills: %w", p.detectErr)
		}
		return nil
	}
	var outcomes []localagentcmd.SkillCleanupOutcome
	var removeErr error
	if len(p.remove) > 0 {
		outcomes, removeErr = newSkillCleanup(a).RemoveSkillCleanupTargets(p.remove, false)
		removed := 0
		for _, o := range outcomes {
			switch {
			case o.Err != nil:
				fmt.Fprintln(a.Out, theme.Warnf("could not remove %s at %s: %v", o.Target.Label(), o.Target.Display, o.Err))
			case o.Removed:
				removed++
			}
		}
		if removed > 0 {
			fmt.Fprintln(a.Out, theme.Successf("Removed %d Jentic skill install(s).", removed))
		}
	}
	a.reportRemainingSkills(p, outcomes, false)
	if removeErr != nil {
		return fmt.Errorf("uninstall finished, but some agent skills could not be removed (run `%s`): %w", removeCmd, removeErr)
	}
	return nil
}

// reportRemainingSkills lists the Jentic skills still installed (or, on a dry
// run, that would remain) with the command that removes them. It never
// mutates and works off the single up-front probe plus this run's outcomes.
// Hints are tailored per reason so nothing is suggested twice and --force is
// only ever offered for a Jentic write the operator edited — never for a file
// Jentic cannot prove it wrote.
func (a *app) reportRemainingSkills(p skillPlan, outcomes []localagentcmd.SkillCleanupOutcome, dryRun bool) {
	if p.detectErr != nil {
		return
	}
	gone := map[string]bool{}
	// A removable target the engine kept without an error was edited after
	// the probe; it needs the targeted --force line, since `--all` would
	// refuse it again.
	editedSinceProbe := map[string]bool{}
	for _, o := range outcomes {
		key := o.Target.Skill + "\x00" + o.Target.Path
		switch {
		case o.Removed:
			gone[key] = true
		case o.Kept && o.Err == nil && o.Target.Removable():
			editedSinceProbe[key] = true
		}
	}
	if dryRun {
		for _, t := range p.remove {
			gone[t.Skill+"\x00"+t.Path] = true
		}
	}
	var remaining []localagentcmd.SkillCleanupTarget
	for _, t := range p.installed {
		key := t.Skill + "\x00" + t.Path
		if gone[key] {
			continue
		}
		if editedSinceProbe[key] {
			t.UserEdits = true
		}
		remaining = append(remaining, t)
	}
	if len(remaining) == 0 {
		return
	}

	heading := "Jentic skills are still installed for your agents (they point at the removed stack):"
	if dryRun {
		heading = "These Jentic skills would stay installed:"
	}
	fmt.Fprintln(a.Out, theme.Warnf("%s", heading))
	var managed, outOfScope, edited, unattributed bool
	for _, t := range remaining {
		line := skillLine(t)
		switch {
		case t.Unattributed:
			unattributed = true
			line += " (not written by Jentic)"
		case t.UserEdits:
			edited = true
			line += " (manually edited)"
		default:
			managed = true
			if p.requested && !p.inScope(t) {
				outOfScope = true
			}
		}
		fmt.Fprintln(a.Out, theme.Dim.Render(line))
	}
	if outOfScope {
		fmt.Fprintln(a.Out, theme.Dim.Render("Project-scope installs under the current directory are kept unless you pass --remove-project-skills."))
	}
	if managed {
		fmt.Fprintln(a.Out, theme.Dimf("Remove them with: %s (from the directory holding any project-scope install)", "jentic skill remove --all"))
	}
	if edited {
		fmt.Fprintln(a.Out, theme.Dim.Render("Manually edited skills are kept. To delete one anyway:"))
		for _, t := range remaining {
			if t.UserEdits {
				fmt.Fprintln(a.Out, theme.Dimf("  jentic skill remove --operator %s --skill %s --scope %s --force", t.Operators[0], t.Skill, t.Scope))
			}
		}
	}
	if unattributed {
		fmt.Fprintln(a.Out, theme.Dim.Render("Files marked (not written by Jentic) carry no Jentic provenance; review them and delete by hand if unwanted."))
	}
}

// skillLine formats one skill target for a listing.
func skillLine(t localagentcmd.SkillCleanupTarget) string {
	return fmt.Sprintf("  %s  %s (%s scope)", t.Label(), t.Display, t.Scope)
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
func (a *app) uninstallDocker(opts *uninstallOptions, composePath string) error {
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
func (a *app) dataVolumeLabel() string {
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
func (a *app) uninstallVolumeHint() []string {
	m, found, err := config.LoadManifest(a.Paths)
	if err != nil || !found || m == nil {
		return install.AllDataVolumeNames()
	}
	return install.DataVolumeNames(strings.EqualFold(m.DB, "postgres"))
}

func confirmUninstall(dir string, skills int) (bool, error) {
	desc := "Config files are backed up to *-old. This also stops the stack; " +
		"the Docker data volume is preserved unless you choose to delete it."
	if skills > 0 {
		desc += fmt.Sprintf(" It also removes the %d Jentic skill install(s) listed above, outside %s.", skills, dir)
	}
	confirm := false
	if err := install.RunConfirm(
		huh.NewConfirm().
			Title(fmt.Sprintf("Delete everything under %s?", dir)).
			Description(desc).
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

// askSkillRemoval is the skills-only confirmation; a package variable so tests
// can observe the prompt without a terminal.
var askSkillRemoval = confirmSkillRemoval

// confirmSkillRemoval asks before removing skills when there is no ~/.jentic
// install to tear down, so the skills-only path never edits files outside
// ~/.jentic unconfirmed. Aborting the prompt keeps the skills.
func confirmSkillRemoval(n int) (bool, error) {
	confirm := false
	if err := install.RunConfirm(
		huh.NewConfirm().
			Title(fmt.Sprintf("Remove these %d Jentic skill install(s)?", n)).
			Description("Manually edited skills are kept either way.").
			Affirmative("Yes, remove them").
			Negative("Keep them").
			Value(&confirm),
	); err != nil {
		if errors.Is(err, huh.ErrUserAborted) {
			return false, nil
		}
		return false, err
	}
	return confirm, nil
}
