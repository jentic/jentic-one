package ctlcmd

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"os/user"
	"strconv"
	"strings"
	"time"

	"github.com/jentic/jentic-one/cli/client/auth"
	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/install"
	"github.com/jentic/jentic-one/cli/internal/localagent"
	"github.com/jentic/jentic-one/cli/internal/proc"
	"github.com/jentic/jentic-one/cli/internal/serverinfo"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
	"gopkg.in/yaml.v3"
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

func newDoctorCmd(app *app) *cobra.Command {
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
	opts.Bind(cmd)
	cmd.Flags().BoolVar(&opts.json, "json", false, "emit the check results as JSON")
	return cmd
}

// doctor accumulates checks while probing each subsystem.
type doctor struct {
	app     *app
	ctx     context.Context
	baseURL string
	checks  []check
}

func (d *doctor) add(section, name string, status checkStatus, detail, hint string) {
	d.checks = append(d.checks, check{section: section, name: name, status: status, detail: detail, hint: hint})
}

func (a *app) doctorE(ctx context.Context, opts *doctorOptions) error {
	d := &doctor{app: a, ctx: ctx}

	// config.Load returns (nil, err) on a parse/read failure; the environment
	// check surfaces that as a fail and we fall back to defaults so the rest of
	// the report still runs.
	cfg, cfgErr := config.Load(a.Paths)
	d.checkEnvironment(cfg, cfgErr)
	if cfg == nil {
		cfg = &config.FileConfig{}
	}
	d.baseURL = cfg.ResolvedBaseURLOr(opts.BaseURL)

	manifest, manifestFound, _ := config.LoadManifest(a.Paths)
	d.checkInstall(manifest, manifestFound)
	d.checkTooling(manifest, manifestFound)
	d.checkConfigValidity()
	d.checkServer()
	d.checkAgent()
	d.checkLocalAgent(cfg)

	if opts.json || !cmdcore.StdoutIsTerminal() {
		// Aligned with the jentic-side JSONOrPretty default (UX-5): machine
		// output when piped, unless the operator explicitly asked for the
		// pretty report by running on a terminal without --json.
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
// is simply down. The ctx lets doctor's overall run be canceled (Ctrl-C).
var doctorDockerProbe = func(ctx context.Context) (string, bool) {
	return install.DockerDaemonResponsiveQuick(ctx, 2*time.Second)
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
		if detail, healthy := doctorDockerProbe(d.ctx); !healthy {
			// A missing binary points at install docs; a present-but-down
			// daemon points at starting it (#954).
			hint := install.DockerDaemonRecoveryHint() + ", then `jenticctl start`"
			name := "docker daemon"
			if install.DockerNotInstalled(detail) {
				hint = install.DockerNotInstalledHint()
				name = "docker"
			}
			d.add(section, name, statusWarn, detail, hint)
			return
		}
		out, err := install.ComposePs(composePath)
		if err != nil {
			d.add(section, "deploy", statusWarn, "docker compose ps failed: "+err.Error(),
				install.DockerDaemonRecoveryHint())
			return
		}
		d.add(section, "deploy", statusPass, "docker compose ("+composeSummary(out)+")", "")
		d.checkExposure(section, composePath, out)
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

// checkExposure warns when the Docker install publishes ports more widely
// than the operator likely intended (#992). Two layers:
//
//   - the generated compose file: an unqualified mapping ("8000:8000") is
//     published by Docker on ALL interfaces — and Docker's own iptables rules
//     bypass UFW — so a compose file generated before the #992 fix silently
//     exposes the app, broker, and database regardless of the wizard's
//     loopback choice. This layer works even when the stack is down.
//   - the live `docker compose ps` output: running containers may predate a
//     regenerated compose file, so a "0.0.0.0:" publish is flagged even when
//     the file itself is clean.
//
// Warnings, not failures: doctor is documented as safe for CI wiring, and the
// operator may genuinely want an all-interfaces publish (0.0.0.0 answered in
// the wizard also lands here — the hint tells them how to change it if it was
// not intentional).
func (d *doctor) checkExposure(section, composePath, psOut string) {
	data, err := os.ReadFile(composePath) //nolint:gosec // composePath is CLI-managed under JENTIC_HOME.
	if err != nil {
		d.add(section, "exposure", statusWarn, "could not read compose file: "+err.Error(), "")
		return
	}
	unqualified, err := install.UnqualifiedPublishes(data)
	if err != nil {
		d.add(section, "exposure", statusWarn, err.Error(), "")
		return
	}
	regenHint := "re-run `jenticctl install` to regenerate the compose file with your bind host, then `jenticctl start`"
	if len(unqualified) > 0 {
		d.add(section, "exposure", statusWarn,
			"ports published on all interfaces (Docker bypasses UFW): "+strings.Join(unqualified, ", "),
			regenHint)
		return
	}
	if strings.Contains(psOut, "0.0.0.0:") || strings.Contains(psOut, "[::]:") {
		d.add(section, "exposure", statusWarn,
			"running containers publish on all interfaces (stack predates the current compose file)",
			"restart the stack: `jenticctl stop && jenticctl start`")
		return
	}
	d.add(section, "exposure", statusPass, "published ports are bound to specific interfaces", "")
}

// checkAgent reports the ACTIVE V2 context's identity and token state, read-only
// from the XDG store. Like status, it never mints or refreshes a token: the /me
// probe (deep credential check) is `jentic doctor`'s job — this operator-side
// row only reflects what is on disk.
func (d *doctor) checkAgent() {
	const section = "Agent"
	st, err := sdkconfig.LoadState("")
	if err != nil {
		d.add(section, "context", statusWarn, "no active context: "+err.Error(),
			"run `jentic register` (or `jentic migrate` on an upgraded machine)")
		return
	}
	if st.InjectedBearerToken != "" {
		d.add(section, "context", statusPass, "file-less session ($JENTIC_BEARER_TOKEN)", "")
		return
	}

	ref := auth.IdentityRef{Identity: st.IdentityName, Environment: st.EnvironmentName}
	pair := fmt.Sprintf("identity %q in environment %q", st.IdentityName, st.EnvironmentName)

	if key, kerr := auth.ReadAPIKey(ref); kerr == nil && key != "" {
		d.add(section, "context", statusPass, pair+" (api-key)", "")
		return
	}

	tokens, _ := auth.ReadTokens(ref)
	state, _ := tokenStatus(tokens)
	status := statusPass
	hint := ""
	if tokens == nil || tokens.AccessToken == "" || time.Now().After(tokens.ExpiresAt) {
		status = statusWarn
		hint = "run any `jentic` command to mint a fresh token, or `jentic register` if unregistered"
	}
	d.add(section, "context", status, pair+", token "+state, hint)
}

// checkLocalAgent is the client-side sibling of jenticctl's local-agent probe
// (5.1 §3c, plan.md Phase 5 item 6). jenticctl is deliberately absent from agent
// hosts, so `jentic doctor` is the only self-check that can run there. It reports
// per-session confinement prerequisites (the SAME AgentUserPrereqs probe `jentic
// run`'s launch gate uses, so the two can never disagree) and, when this operator
// has provisioned a dedicated agent account, warns if the agent would run under
// the operator's own uid — the boundary the whole isolation model exists to
// establish. It is read-only and, like the rest of doctor, keeps warnings at a
// zero exit so it stays CI-safe.
func (d *doctor) checkLocalAgent(cfg *config.FileConfig) {
	const section = "Local agent"

	for _, p := range localagent.AgentUserPrereqs() {
		if p.OK {
			d.add(section, p.Name, statusPass, "available", "")
			continue
		}
		d.add(section, p.Name, statusWarn, p.Reason, p.Hint)
	}

	acct, hasAcct := cfg.AgentAccount()
	if !hasAcct || !acct.AccountCreated {
		return
	}
	// A provisioned account whose OS user cannot be resolved, or which resolves
	// to the operator's own uid, means an agent launched here would share the
	// operator's disk view — the isolation is nominal, not real.
	if agentUser, err := user.Lookup(acct.User); err != nil {
		d.add(section, "account", statusWarn, fmt.Sprintf("account %q not found on this host: %v", acct.User, err),
			"run `jentic bootstrap` on the agent host, or `jentic reset` to clear the stale record")
	} else if agentUser.Uid == strconv.Itoa(os.Getuid()) {
		d.add(section, "account uid", statusWarn,
			fmt.Sprintf("agent account %q shares this operator's uid (%s)", acct.User, agentUser.Uid),
			"the agent would run unconfined against your files; re-provision a dedicated account with `jentic bootstrap`")
	} else {
		d.add(section, "account", statusPass, fmt.Sprintf("%s (uid %s)", acct.User, agentUser.Uid), "")
	}
}

// checkConfigValidity validates the persisted install config (jentic-one.yaml)
// against the shape jentic-one needs to boot (impl/6.4 "Configuration Validity",
// wired to the Phase 6 config-schema work). It is a light structural check, not a
// full schema validation: the authoritative validator is the backend's Pydantic
// model at boot, and doctor must stay read-only and dependency-light. It reports:
//   - databases.control must name either a Postgres host or a SQLite path — the
//     single most common "install half-written" failure the impl guide calls out;
//   - the file must parse as YAML at all.
//
// It is skipped (no row) when no install config exists yet — that gap is already
// reported by the Install/Server checks, and a fresh machine shouldn't fail here.
func (d *doctor) checkConfigValidity() {
	const section = "Configuration"
	path := d.app.Paths.InstallConfigPath()
	raw, err := os.ReadFile(path) //nolint:gosec // path is the CLI-managed install config under JENTIC_HOME, not user input
	if err != nil {
		return // not installed yet; other checks cover that
	}
	var doc struct {
		Databases struct {
			Control struct {
				Host string `yaml:"host"`
				Path string `yaml:"path"`
			} `yaml:"control"`
		} `yaml:"databases"`
	}
	if err := yaml.Unmarshal(raw, &doc); err != nil {
		d.add(section, "config file", statusFail, fmt.Sprintf("%s is not valid YAML: %v", path, err),
			"re-run `jenticctl install` to regenerate it, or fix the syntax by hand")
		return
	}
	if doc.Databases.Control.Host == "" && doc.Databases.Control.Path == "" {
		d.add(section, "databases.control", statusFail,
			"neither a Postgres host nor a SQLite path is set",
			"re-run `jenticctl install` (or set databases.control.host / databases.control.path)")
		return
	}
	backend := "sqlite (" + doc.Databases.Control.Path + ")"
	if doc.Databases.Control.Host != "" {
		backend = "postgres (" + doc.Databases.Control.Host + ")"
	}
	d.add(section, "databases.control", statusPass, backend, "")
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
