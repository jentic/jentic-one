package ctlcmd

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"runtime"
	"strings"
	"time"

	"github.com/charmbracelet/huh"
	"github.com/charmbracelet/x/term"
	"github.com/jentic/jentic-one/cli/internal/cli/localagentcmd"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/install"
	"github.com/jentic/jentic-one/cli/internal/serverinfo"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

type wizardOptions struct {
	baseURL      string
	timeout      time.Duration
	yes          bool
	noOpen       bool
	noWait       bool
	skipOperator bool
	operator     string
}

// healthSetupPaths are the places the admin /health endpoint that carries the
// setup_required signal can live. Combined mode keeps the surface prefix
// (/admin/health) — the root /health in combined mode is a generic liveness
// probe WITHOUT setup_required — while a standalone admin app drops the prefix
// (/health). Probe /admin/health first so combined mode (the default) resolves
// the real signal; only a response that actually contains setup_required is
// trusted (see setupRequired).
var healthSetupPaths = []string{"/admin/health", "/health"}

// Wizard probe timeouts, named so the polling cadence is visible in one place
// rather than scattered as literals.
const (
	// wizardStackProbeTimeout bounds the "is the stack up at all" liveness probe.
	wizardStackProbeTimeout = 2 * time.Second
	// setupProbeTimeout bounds each individual /health setup_required probe.
	setupProbeTimeout = 3 * time.Second
)

// adminConfirmDelay is how long to wait before re-reading setup_required to
// confirm a "no setup needed" result is stable (not a startup-race blip).
// Package-level so tests can shrink it to keep cases near-instant.
var adminConfirmDelay = 1500 * time.Millisecond

// wizardStackReadyTimeout bounds how long step 0 waits for a freshly started
// stack to start answering its health probe. `jenticctl install` flows straight
// into the wizard right after `compose up -d`, which returns once containers are
// started — not once the app has finished booting (DB init + migrations + app
// startup can take tens of seconds on a cold/empty database, e.g. right after a
// `--purge`). A single short probe races that cold start and wrongly reports the
// stack as down, so step 0 polls up to this deadline before giving up. See #697.
// Package-level so tests can shrink it.
var wizardStackReadyTimeout = 90 * time.Second

func newWizardCmd(app *app) *cobra.Command {
	opts := &wizardOptions{}

	cmd := &cobra.Command{
		Use:   "wizard",
		Short: "Guided first-run: create your admin account, connect an operator, and start using Jentic",
		Long: "wizard is the hand-held onboarding that runs after `jenticctl install`. It\n" +
			"waits while you create your first admin account in the browser (so you get\n" +
			"to know the product through the UI), then offers to connect your AI operator\n" +
			"(Claude, Cursor, …): it registers an agent, points you at the console to\n" +
			"approve it, waits for the approval, and finally suggests example prompts you\n" +
			"can try.\n\n" +
			"Everything it does you can also do by hand (open the UI, `jenticctl setup`,\n" +
			"`jentic bootstrap`); the wizard just sequences it into one continuous flow.\n" +
			"Re-run it any time — each step is idempotent.",
		Example: "  jenticctl wizard\n" +
			"  jenticctl wizard --operator claude --yes\n" +
			"  jenticctl wizard --no-open   # don't auto-launch the browser",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.wizardE(cmd.Context(), opts)
		},
	}

	cmd.Flags().StringVar(&opts.baseURL, "base-url", "", "Jentic control-plane base URL (default: config / http://127.0.0.1:8000)")
	cmd.Flags().DurationVar(&opts.timeout, "timeout", 15*time.Minute, "how long to wait for account creation and agent approval at each step")
	cmd.Flags().BoolVarP(&opts.yes, "yes", "y", false, "skip confirmation prompts; connect the detected operator automatically")
	cmd.Flags().BoolVar(&opts.noOpen, "no-open", false, "do not auto-open the browser to the setup page")
	cmd.Flags().BoolVar(&opts.noWait, "no-wait", false, "do not wait for the admin account; assume it already exists")
	cmd.Flags().BoolVar(&opts.skipOperator, "skip-operator", false, "stop after the account exists; do not connect an operator")
	cmd.Flags().StringVar(&opts.operator, "operator", "", "operator to connect (claude, cursor, …); default: auto-detect")

	return cmd
}

func (a *app) wizardE(ctx context.Context, opts *wizardOptions) error {
	baseURL := a.resolveWizardBaseURL(opts.baseURL)
	interactive := !opts.yes && term.IsTerminal(os.Stdin.Fd())

	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Headingf("Welcome to Jentic — let's get you set up"))
	fmt.Fprintln(a.Out, theme.Dim.Render("This guides you from a running stack to your first agent call."))
	fmt.Fprintln(a.Out)

	// Step 0: make sure the stack is actually up — waiting out a cold start so
	// the install → wizard handoff doesn't race `compose up -d`. Everything
	// below needs the stack answering its health probe.
	if !a.wizardWaitForStack(ctx, baseURL) {
		fmt.Fprintln(a.Out, theme.Warnf("The Jentic stack does not appear to be running at %s.", baseURL))
		fmt.Fprintln(a.Out, theme.Dim.Render("Start it with `jenticctl start` (or `jenticctl install`), then re-run `jenticctl wizard`."))
		return nil
	}

	// Step 1: first admin account (UI-first).
	if !opts.noWait {
		if !a.wizardWaitForAdmin(ctx, baseURL, opts) {
			// User chose to exit / timed out; leave a clear resume path.
			return nil
		}
	}

	if opts.skipOperator {
		fmt.Fprintln(a.Out, theme.Dim.Render("Operator setup skipped (--skip-operator). Connect one later with `jentic bootstrap`."))
		return nil
	}

	// Step 2: offer to connect an operator.
	if interactive {
		connect := true
		if err := install.RunConfirm(huh.NewConfirm().
			Title("Connect an AI operator now?").
			Description("Registers an agent for your operator (Claude, Cursor, …) and writes its skill.").
			Affirmative("Yes, connect it").
			Negative("Not now").
			Value(&connect)); err != nil {
			if errors.Is(err, huh.ErrUserAborted) {
				a.wizardExitHint()
				return nil
			}
			return err
		}
		if !connect {
			a.wizardExitHint()
			return nil
		}
	}

	// Step 3+4: bootstrap (register + approval wait + skill) via the shared flow.
	// bootstrap already prints the clickable console approval link and polls.
	// operators empty + yes=false runs bootstrap's operator picker (detected
	// runtimes pre-selected). A named operator drives it non-interactively.
	// Non-interactive with no operator auto-detects (bootstrap targets every
	// detected operator, or errors if it can't tell).
	var operators []string
	var bootYes bool
	switch {
	case opts.operator != "":
		operators = []string{opts.operator}
		bootYes = true
	case interactive:
		bootYes = false
	default:
		bootYes = true
	}

	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Step.Render("Connecting your operator"))
	if err := localagentcmd.New(a.App).BootstrapForWizard(ctx, baseURL, opts.timeout, operators, bootYes); err != nil {
		return err
	}

	// Step 5: example prompts.
	a.wizardExamplePrompts(baseURL)
	return nil
}

// resolveWizardBaseURL prefers an explicit flag, then config, then the default.
func (a *app) resolveWizardBaseURL(flag string) string {
	if strings.TrimSpace(flag) != "" {
		return flag
	}
	if cfg, err := config.Load(a.Paths); err == nil {
		return cfg.ResolvedBaseURL()
	}
	return config.DefaultBaseURL
}

// wizardStackUp reports whether the control plane answers a health probe.
func (a *app) wizardStackUp(baseURL string) bool {
	return serverinfo.Probe(baseURL, wizardStackProbeTimeout).Running
}

// wizardWaitForStack reports whether the control plane answers a health probe,
// waiting out a cold start before giving up. The first probe is immediate so a
// re-run against an already-running stack returns instantly (no progress line);
// only when the stack isn't answering yet does it print a "waiting …" line and
// poll (using the shared cadence) up to wizardStackReadyTimeout. This covers the
// install → wizard handoff, where the app is still booting after `compose up -d`
// and a single short probe would falsely report the stack as down. Returns false
// on timeout or context cancellation. See #697.
func (a *app) wizardWaitForStack(ctx context.Context, baseURL string) bool {
	if a.wizardStackUp(baseURL) {
		return true
	}

	fmt.Fprint(a.Out, theme.Dim.Render(fmt.Sprintf("Waiting for the Jentic stack to come up at %s ", baseURL)))
	deadline := time.Now().Add(wizardStackReadyTimeout)
	delay := pollInitialDelay
	for {
		if time.Now().After(deadline) {
			fmt.Fprintln(a.Out)
			return false
		}
		select {
		case <-ctx.Done():
			fmt.Fprintln(a.Out)
			return false
		case <-time.After(delay):
		}
		if delay < pollMaxDelay {
			delay += pollDelayStep
		}
		if a.wizardStackUp(baseURL) {
			fmt.Fprintln(a.Out)
			return true
		}
		fmt.Fprint(a.Out, theme.Dim.Render("."))
	}
}

// wizardWaitForAdmin drives the UI-first first-admin step: it opens the setup
// page in the browser and polls /health until an admin account exists. Returns
// true once setup is complete, false if the user chose to exit or the wait
// timed out.
func (a *app) wizardWaitForAdmin(ctx context.Context, baseURL string, opts *wizardOptions) bool {
	// The very first decisive read happens right after `compose up -d` / app
	// start, when the process is listening but the DB session may not be ready.
	// The admin /health endpoint answers `setup_required:false` when its DB
	// session raises (it fails open on liveness, not setup), so a single
	// `false` here can falsely mean "an admin already exists" on a fresh box —
	// the exact symptom this wizard exists to prevent. Require the negative to
	// be confirmed by a second consistent read a moment later before trusting
	// it; a real, populated DB stays `false` across both reads, while a startup
	// blip flips to `true`/errors on the confirm and falls through to the wait.
	if a.adminAlreadyExists(ctx, baseURL) {
		fmt.Fprintln(a.Out, theme.Successf("Admin account already exists — skipping account creation."))
		return true
	}

	setupURL := config.AppURL(baseURL, "setup")
	fmt.Fprintln(a.Out, theme.Step.Render("Step 1 — create your admin account"))
	fmt.Fprintln(a.Out, theme.Dim.Render("We recommend doing this in the browser so you get oriented in the UI."))
	fmt.Fprintf(a.Out, "    %s\n", theme.Command.Render(setupURL))
	if !opts.noOpen {
		if err := openBrowser(setupURL); err != nil {
			fmt.Fprintln(a.Out, theme.Dim.Render("(couldn't open the browser automatically — open the link above)"))
		}
	}
	fmt.Fprintln(a.Out, theme.Dim.Render("Prefer the terminal? Press Ctrl-C, run `jenticctl setup`, then re-run `jenticctl wizard`."))
	fmt.Fprintln(a.Out)
	fmt.Fprint(a.Out, theme.Dim.Render("Waiting for you to finish creating the account ..."))

	deadline := time.Now().Add(opts.timeout)
	delay := pollInitialDelay
	for {
		if time.Now().After(deadline) {
			fmt.Fprintln(a.Out)
			fmt.Fprintln(a.Out, theme.Warnf("Timed out waiting for the admin account. Re-run `jenticctl wizard` once it's created."))
			return false
		}
		select {
		case <-ctx.Done():
			fmt.Fprintln(a.Out)
			fmt.Fprintln(a.Out, theme.Dim.Render("Stopped. Resume any time with `jenticctl wizard`."))
			return false
		case <-time.After(delay):
		}
		if delay < pollMaxDelay {
			delay += pollDelayStep
		}

		required, err := setupRequired(ctx, baseURL)
		if err != nil {
			// Transient blip while the server settles; keep waiting.
			fmt.Fprint(a.Out, theme.Dim.Render("."))
			continue
		}
		if !required {
			fmt.Fprintln(a.Out)
			fmt.Fprintln(a.Out, theme.Successf("Admin account created."))
			return true
		}
		fmt.Fprint(a.Out, theme.Dim.Render("."))
	}
}

// adminAlreadyExists reports whether the deployment already has an admin
// account, confirmed by TWO consistent reads. The admin /health endpoint
// answers setup_required:false when its DB session raises (it fails open on
// liveness), so a single false read right after the stack starts can falsely
// claim an admin exists on a fresh box. A real, populated DB stays false across
// both reads; a startup blip flips to true/errors on the confirm read, so we
// return false and let the caller fall through to the wait loop.
func (a *app) adminAlreadyExists(ctx context.Context, baseURL string) bool {
	required, err := setupRequired(ctx, baseURL)
	if err != nil || required {
		return false
	}
	select {
	case <-ctx.Done():
		return false
	case <-time.After(adminConfirmDelay):
	}
	confirm, err := setupRequired(ctx, baseURL)
	return err == nil && !confirm
}

// wizardExitHint tells the user how to finish setup later.
func (a *app) wizardExitHint() {
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Dim.Render("No problem. When you're ready, connect an operator with `jentic bootstrap`."))
}

// wizardExamplePrompts prints a few copy-pasteable things the user can ask
// their freshly connected agent to do. baseURL is the resolved control-plane
// URL so the console link points at the same host the wizard ran against (not
// the built-in default).
func (a *app) wizardExamplePrompts(baseURL string) {
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Headingf("You're all set — try asking your agent:"))
	for _, p := range []string{
		`"Search the catalog for a weather API and import it."`,
		`"List the operations available on the API you just imported."`,
		`"Call an endpoint and show me the response."`,
	} {
		fmt.Fprintf(a.Out, "    %s\n", theme.Command.Render(p))
	}
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Dim.Render("Manage agents and approvals anytime in the console: ")+
		theme.Command.Render(config.AppURL(baseURL, "agents")))
}

// setupRequired performs a GET against the admin /health endpoint and reports
// whether the deployment still needs its first admin account. It tries the
// combined-mode path (/admin/health) first, then the standalone path (/health),
// because only a response that actually carries the setup_required field is
// trusted (the combined app's root /health is a generic liveness probe without
// it). Each probe gets its own timeout derived from the caller's context — not
// from the previous iteration — so a slow/missing first path never starves the
// fallback.
func setupRequired(ctx context.Context, baseURL string) (bool, error) {
	base := strings.TrimRight(strings.TrimSpace(baseURL), "/")
	if base == "" {
		return false, errors.New("empty base URL")
	}
	var lastErr error
	for _, p := range healthSetupPaths {
		ictx, cancel := context.WithTimeout(ctx, setupProbeTimeout)
		req, err := http.NewRequestWithContext(ictx, http.MethodGet, base+p, nil)
		if err != nil {
			cancel()
			lastErr = err
			continue
		}
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			cancel()
			lastErr = err
			continue
		}
		if resp.StatusCode != http.StatusOK {
			_ = resp.Body.Close()
			cancel()
			lastErr = fmt.Errorf("health %s: status %d", p, resp.StatusCode)
			continue
		}
		var body struct {
			SetupRequired *bool `json:"setup_required"`
		}
		err = json.NewDecoder(io.LimitReader(resp.Body, 1<<16)).Decode(&body)
		_ = resp.Body.Close()
		cancel()
		if err != nil {
			lastErr = err
			continue
		}
		// A 200 without setup_required (e.g. the combined app's root /health
		// liveness probe) does NOT carry the signal — keep looking rather than
		// treating a missing field as "no setup needed".
		if body.SetupRequired == nil {
			lastErr = fmt.Errorf("health %s: response has no setup_required field", p)
			continue
		}
		return *body.SetupRequired, nil
	}
	return false, lastErr
}

// openBrowser opens url in the user's default browser, cross-platform. It is
// best-effort: callers fall back to printing the URL on error.
func openBrowser(url string) error {
	var (
		bin  string
		args []string
	)
	switch runtime.GOOS {
	case "darwin":
		bin = "open"
		args = []string{url}
	case "windows":
		bin = "rundll32"
		args = []string{"url.dll,FileProtocolHandler", url}
	default: // linux, *bsd
		bin = "xdg-open"
		args = []string{url}
	}
	return exec.Command(bin, args...).Start() //nolint:gosec // bin is one of three hardcoded launchers; args is the CLI-built console URL, not user input.
}
