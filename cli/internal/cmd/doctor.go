package cmd

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"

	"github.com/jentic/jentic-one/cli/internal/agentauth"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/install"
	"github.com/jentic/jentic-one/cli/internal/proc"
	"github.com/jentic/jentic-one/cli/internal/serverinfo"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

// checkStatus is the outcome of a single doctor probe.
type checkStatus int

const (
	statusPass checkStatus = iota
	statusWarn
	statusFail
)

func (s checkStatus) String() string {
	switch s {
	case statusPass:
		return "pass"
	case statusWarn:
		return "warn"
	default:
		return "fail"
	}
}

// check is one diagnostic result. detail is the human value shown after the
// name; hint is an optional remediation line printed under non-passing rows.
type check struct {
	section string
	name    string
	status  checkStatus
	detail  string
	hint    string
}

type doctorOptions struct {
	identityOptions
	json bool
}

func newDoctorCmd(app *App) *cobra.Command {
	opts := &doctorOptions{}
	cmd := &cobra.Command{
		Use:   "doctor",
		Short: "Diagnose the local jentic environment",
		Long: "doctor runs an exhaustive set of health checks across the local jentic\n" +
			"setup — filesystem and config, the recorded install, required tooling, the\n" +
			"control-plane server, and the agent profile — and prints a pass/warn/fail\n" +
			"report with remediation hints.\n\n" +
			"It is read-only and never mints tokens. It exits non-zero when any check\n" +
			"fails (warnings keep a zero exit), so it is safe to wire into CI.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.doctorE(cmd.Context(), opts)
		},
	}
	opts.bind(cmd)
	cmd.Flags().BoolVar(&opts.json, "json", false, "emit the check results as JSON")
	return cmd
}

// doctor accumulates checks while probing each subsystem.
type doctor struct {
	app     *App
	ctx     context.Context
	profile string
	baseURL string
	checks  []check
}

func (d *doctor) add(section, name string, status checkStatus, detail, hint string) {
	d.checks = append(d.checks, check{section: section, name: name, status: status, detail: detail, hint: hint})
}

func (a *App) doctorE(ctx context.Context, opts *doctorOptions) error {
	d := &doctor{app: a, ctx: ctx}

	// config.Load returns (nil, err) on a parse/read failure; the environment
	// check surfaces that as a fail and we fall back to defaults so the rest of
	// the report still runs.
	cfg, cfgErr := config.Load(a.Paths)
	d.checkEnvironment(cfg, cfgErr)
	if cfg == nil {
		cfg = &config.FileConfig{}
	}
	d.profile = cfg.ResolvedProfileName(opts.profile)
	d.baseURL = cfg.ResolvedBaseURLOr(opts.baseURL)

	manifest, manifestFound, _ := config.LoadManifest(a.Paths)
	d.checkInstall(manifest, manifestFound)
	d.checkTooling(manifest, manifestFound)
	d.checkServer()
	d.checkAgent()

	if opts.json {
		return d.renderJSON()
	}
	return d.render()
}

// checkEnvironment verifies the state directory and config.yaml.
func (d *doctor) checkEnvironment(cfg *config.FileConfig, cfgErr error) {
	const section = "Environment"
	root := d.app.Paths.Dir()
	info, statErr := os.Stat(root)
	switch {
	case statErr != nil && os.IsNotExist(statErr):
		d.add(section, "home", statusFail, root+" does not exist", "run `jenticctl install` to set up ~/.jentic")
	case statErr != nil:
		d.add(section, "home", statusFail, fmt.Sprintf("cannot stat %s: %v", root, statErr), "")
	default:
		d.add(section, "home", statusPass, root, "")
		if perm := info.Mode().Perm(); perm&0o077 != 0 {
			d.add(section, "home perms", statusWarn, fmt.Sprintf("%s is %#o (group/other access)", root, perm), "chmod 700 "+root)
		}
	}

	switch {
	case cfgErr != nil:
		d.add(section, "config", statusFail, cfgErr.Error(), "fix or remove "+d.app.Paths.ConfigPath())
	case cfg != nil && cfg.Loaded:
		d.add(section, "config", statusPass, d.app.Paths.ConfigPath(), "")
	default:
		d.add(section, "config", statusPass, "using defaults (no config.yaml)", "")
	}
}

// checkInstall reports what the install manifest recorded.
func (d *doctor) checkInstall(m *config.Manifest, found bool) {
	const section = "Install"
	if !found {
		d.add(section, "manifest", statusWarn, "no install manifest", "run `jenticctl install`")
		return
	}
	d.add(section, "manifest", statusPass, fmt.Sprintf("mode %s, db %s", valueOr(m.Mode, "unknown"), valueOr(m.DB, "-")), "")
}

// checkTooling verifies the external tools the recorded install mode needs.
func (d *doctor) checkTooling(m *config.Manifest, found bool) {
	const section = "Tooling"
	mode := config.ModeLocal
	if found && m.Mode != "" {
		mode = m.Mode
	}

	if mode == config.ModeDocker {
		d.checkTool(section, "docker", "https://docs.docker.com/get-docker/")
		return
	}

	d.checkTool(section, "uv", "https://docs.astral.sh/uv/")
	d.checkTool(section, "git", "https://git-scm.com/downloads")
	venv := d.app.Paths.VenvPath()
	if fi, err := os.Stat(venv); err == nil && fi.IsDir() {
		d.add(section, "venv", statusPass, venv, "")
	} else if found {
		d.add(section, "venv", statusWarn, "venv not found at "+venv, "run `jenticctl install`")
	}
}

func (d *doctor) checkTool(section, name, url string) {
	path, err := exec.LookPath(name)
	if err != nil {
		d.add(section, name, statusFail, "not found on PATH", "install "+name+": "+url)
		return
	}
	detail := path
	if v := toolVersionLine(name); v != "" {
		detail = v
	}
	d.add(section, name, statusPass, detail, "")
}

// checkServer probes the control-plane health route and the local deploy.
func (d *doctor) checkServer() {
	const section = "Server"
	info := serverinfo.Probe(d.baseURL, 2*time.Second)
	if info.Running {
		d.add(section, "control", statusPass, d.baseURL+" ("+valueOr(info.Version, "running")+")", "")
	} else {
		d.add(section, "control", statusWarn, d.baseURL+" offline", "run `jenticctl start`")
	}
	d.checkDeploy(section)
}

// doctorDockerProbe is the seam doctor's deploy check runs through so tests can
// simulate a stopped daemon without a real Docker. It returns a short reason
// (empty when healthy) and whether the daemon answered. It is a fast,
// single-round-trip probe (distinct from install's ~30s polling probe): doctor
// is read-only and must not block for the full cold-start window when the daemon
// is simply down.
var doctorDockerProbe = func() (string, bool) {
	return install.DockerDaemonResponsiveQuick(2 * time.Second)
}

func (d *doctor) checkDeploy(section string) {
	composePath := d.app.Paths.ComposePath()
	if proc.FileExists(composePath) {
		// A compose install needs a live daemon. Probe it directly so a stopped
		// daemon is reported explicitly rather than inferred from a cryptic
		// `docker compose ps` error (#783). Keep it a warning, not a fail: doctor
		// is documented as safe to wire into CI ("warnings keep a zero exit"),
		// and the sibling `control`/`compose ps` checks also warn on a
		// not-running dependency — a down daemon shouldn't flip the exit code.
		if detail, healthy := doctorDockerProbe(); !healthy {
			d.add(section, "docker daemon", statusWarn, detail,
				install.DockerDaemonRecoveryHint()+", then `jenticctl start`")
			return
		}
		out, err := install.ComposePs(composePath)
		if err != nil {
			d.add(section, "deploy", statusWarn, "docker compose ps failed: "+err.Error(),
				install.DockerDaemonRecoveryHint())
			return
		}
		d.add(section, "deploy", statusPass, "docker compose ("+composeSummary(out)+")", "")
		return
	}

	pid, alive, err := proc.LivePID(d.app.Paths.AppPIDPath())
	if err != nil || pid == 0 {
		return // no local process tracked; nothing to assert
	}
	if alive {
		d.add(section, "deploy", statusPass, fmt.Sprintf("process running (pid %d)", pid), "")
	} else {
		d.add(section, "deploy", statusWarn, "stale pid file (process not running)", "run `jenticctl start`")
	}
}

// checkAgent reports the profile's registration and token state. Like status,
// it never mints or refreshes a token: the /me probe runs only with an
// already-valid cached token.
func (d *doctor) checkAgent() {
	const section = "Agent"
	sess, err := agentauth.Open(d.app.Paths, d.profile, d.baseURL)
	if err != nil {
		d.add(section, "profile", statusWarn, fmt.Sprintf("profile %q unavailable: %v", d.profile, err), "run `jentic register`")
		return
	}
	if sess.Meta.AgentID == "" {
		d.add(section, "profile", statusWarn, fmt.Sprintf("%q not registered", d.profile), "run `jentic register`")
		return
	}

	tokens, _ := sess.Profile.LoadTokens()
	state, _ := tokenStatus(tokens)
	status := statusPass
	hint := ""
	if tokens == nil || tokens.AccessToken == "" || tokens.Expired(0) {
		status = statusWarn
		hint = "run `jentic register` to refresh tokens"
	}
	d.add(section, "profile "+d.profile, status, "token "+state, hint)

	if tokens != nil && !tokens.Expired(0) {
		if me, meErr := sess.Client.Me(d.ctx, tokens.AccessToken); meErr == nil {
			d.add(section, "identity", statusPass, identityLabel(me), "")
		} else {
			d.add(section, "identity", statusWarn, "identity check failed: "+meErr.Error(), "")
		}
	}
}

// render prints the grouped report and returns a non-nil error when any check
// failed, so the CLI exits non-zero.
func (d *doctor) render() error {
	var b strings.Builder
	section := ""
	for _, c := range d.checks {
		if c.section != section {
			if section != "" {
				b.WriteString("\n")
			}
			b.WriteString(theme.Heading.Render(c.section) + "\n")
			section = c.section
		}
		b.WriteString(dotFor(c.status) + " " + theme.Field(c.name, c.detail) + "\n")
		if c.hint != "" && c.status != statusPass {
			b.WriteString("  " + theme.Dim.Render("↳ "+c.hint) + "\n")
		}
	}
	b.WriteString("\n" + d.summary() + "\n")
	fmt.Fprint(d.app.Out, b.String())

	if f := d.failed(); f > 0 {
		return fmt.Errorf("doctor: %d check(s) failed", f)
	}
	return nil
}

func (d *doctor) renderJSON() error {
	type jsonCheck struct {
		Section string `json:"section"`
		Name    string `json:"name"`
		Status  string `json:"status"`
		Detail  string `json:"detail,omitempty"`
		Hint    string `json:"hint,omitempty"`
	}
	var out struct {
		Checks  []jsonCheck `json:"checks"`
		Summary struct {
			Passed   int `json:"passed"`
			Warnings int `json:"warnings"`
			Failed   int `json:"failed"`
		} `json:"summary"`
	}
	for _, c := range d.checks {
		out.Checks = append(out.Checks, jsonCheck{
			Section: c.section,
			Name:    c.name,
			Status:  c.status.String(),
			Detail:  c.detail,
			Hint:    c.hint,
		})
	}
	out.Summary.Passed, out.Summary.Warnings, out.Summary.Failed = d.counts()

	enc := json.NewEncoder(d.app.Out)
	enc.SetIndent("", "  ")
	if err := enc.Encode(out); err != nil {
		return err
	}
	if out.Summary.Failed > 0 {
		return fmt.Errorf("doctor: %d check(s) failed", out.Summary.Failed)
	}
	return nil
}

func (d *doctor) counts() (pass, warn, fail int) {
	for _, c := range d.checks {
		switch c.status {
		case statusPass:
			pass++
		case statusWarn:
			warn++
		default:
			fail++
		}
	}
	return pass, warn, fail
}

func (d *doctor) failed() int {
	_, _, f := d.counts()
	return f
}

func (d *doctor) summary() string {
	p, w, f := d.counts()
	parts := []string{theme.Successf("%d passed", p)}
	if w > 0 {
		parts = append(parts, theme.Warnf("%d warnings", w))
	}
	if f > 0 {
		parts = append(parts, theme.Error.Render(fmt.Sprintf("%d failed", f)))
	}
	return strings.Join(parts, theme.Dim.Render(" · "))
}

func dotFor(s checkStatus) string {
	switch s {
	case statusPass:
		return dotOK()
	case statusWarn:
		return dotWarn()
	default:
		return dotFail()
	}
}

// composeSummary reduces `docker compose ps` table output to a count of listed
// services (header row excluded).
func composeSummary(psOutput string) string {
	lines := strings.Split(strings.TrimSpace(psOutput), "\n")
	n := 0
	for i, ln := range lines {
		if i == 0 || strings.TrimSpace(ln) == "" {
			continue
		}
		n++
	}
	return fmt.Sprintf("%d services", n)
}

// toolVersionLine returns the first line of `<name> --version`, best-effort.
func toolVersionLine(name string) string {
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, name, "--version").Output()
	if err != nil {
		return ""
	}
	line := strings.TrimSpace(string(out))
	if i := strings.IndexByte(line, '\n'); i >= 0 {
		line = line[:i]
	}
	return line
}
