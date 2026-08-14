package ctlcmd

import (
	"context"
	"fmt"
	"time"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/client/auth"
	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/client/generated/control"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/proc"
	"github.com/jentic/jentic-one/cli/internal/serverinfo"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

func newStatusCmd(app *app) *cobra.Command {
	opts := &identityOptions{}
	cmd := &cobra.Command{
		Use:   "status",
		Short: "Show install, server, and identity health",
		Long: "status is a health dashboard for the local jentic setup. It reports the\n" +
			"recorded install (mode/db/source), whether the control-plane server is\n" +
			"reachable and its version, and the active V2 context's identity/token\n" +
			"state. It degrades gracefully: missing pieces are reported, not fatal.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.statusE(cmd.Context(), opts)
		},
	}
	opts.Bind(cmd)
	return cmd
}

func (a *app) statusE(ctx context.Context, opts *identityOptions) error {
	baseURL, err := a.ResolveBaseURL(opts.BaseURL)
	if err != nil {
		return err
	}

	a.statusInstall()
	fmt.Fprintln(a.Out)
	a.statusServer(baseURL)
	fmt.Fprintln(a.Out)
	a.statusBroker()
	fmt.Fprintln(a.Out)
	a.statusIdentity(ctx)
	return nil
}

// statusInstall reports what the install manifest recorded (mode, db, source).
func (a *app) statusInstall() {
	fmt.Fprintln(a.Out, theme.Heading.Render("Install"))

	m, found, err := config.LoadManifest(a.Paths)
	if err != nil {
		fmt.Fprintln(a.Out, dotWarn()+" "+theme.Warnf("manifest unreadable: %v", err))
		return
	}
	if !found {
		fmt.Fprintln(a.Out, dotDown()+" "+theme.Dim.Render("no install manifest — run `jenticctl install`"))
		return
	}

	fmt.Fprintln(a.Out, dotOK()+" "+theme.Field("mode", valueOr(m.Mode, "unknown")))
	fmt.Fprintln(a.Out, "  "+theme.Field("database", valueOr(m.DB, "-")))

	source := m.ResolvedRepo()
	if m.Ref != "" {
		source += "@" + m.Ref
	}
	if m.Commit != "" {
		source += " (" + m.Commit + ")"
	}
	fmt.Fprintln(a.Out, "  "+theme.Field("source", source))
	fmt.Fprintln(a.Out, "  "+theme.Field("cli", valueOr(m.CLIVersion, version)))
	if m.InstalledAt != "" {
		fmt.Fprintln(a.Out, "  "+theme.Field("installed", m.InstalledAt))
	}
	if cfgPath := a.Paths.InstallConfigPath(); proc.FileExists(cfgPath) {
		fmt.Fprintln(a.Out, "  "+theme.Field("config", cfgPath))
	}
}

// statusServer probes the control-plane health route and reports the local
// deploy (Docker stack vs background process) backing it.
func (a *app) statusServer(baseURL string) {
	fmt.Fprintln(a.Out, theme.Heading.Render("Server"))

	info := serverinfo.Probe(baseURL, serverinfo.DefaultTimeout)
	if info.Running {
		fmt.Fprintln(a.Out, dotOK()+" "+theme.Field("control", baseURL))
		fmt.Fprintln(a.Out, "  "+theme.Field("version", valueOr(info.Version, "running")))
	} else {
		fmt.Fprintln(a.Out, dotDown()+" "+theme.Field("control", baseURL))
		fmt.Fprintln(a.Out, "  "+theme.Dim.Render("offline"))
	}
	a.statusDeploy()
}

// statusDeploy reports how the app is run locally: a generated compose file
// marks a Docker install; otherwise it inspects the background-process PID file.
func (a *app) statusDeploy() {
	if proc.FileExists(a.Paths.ComposePath()) {
		fmt.Fprintln(a.Out, "  "+theme.Field("deploy", "docker compose"))
		return
	}
	pid, alive, err := proc.LivePID(a.Paths.AppPIDPath())
	if err != nil || pid == 0 {
		return
	}
	if alive {
		fmt.Fprintln(a.Out, "  "+theme.Field("process", fmt.Sprintf("running (pid %d)", pid)))
	} else {
		fmt.Fprintln(a.Out, "  "+theme.Field("process", "stale pid file (not running)"))
	}
}

// statusBroker reports the configured broker target and probes its health
// endpoint. The target follows the same precedence as `run`/`execute`:
// defaults < config.yaml broker.{scheme,host}. status takes no broker flags, so
// only the file/default values are consulted here.
func (a *app) statusBroker() {
	fmt.Fprintln(a.Out, theme.Heading.Render("Broker"))

	scheme := config.DefaultBrokerScheme
	host := config.DefaultBrokerHost
	if cfg, err := config.Load(a.Paths); err == nil {
		scheme = cfg.ResolvedBrokerScheme("", false)
		host = cfg.ResolvedBrokerHost("", false)
	}
	baseURL := scheme + "://" + host

	info := serverinfo.Probe(baseURL, serverinfo.DefaultTimeout)
	if info.Running {
		fmt.Fprintln(a.Out, dotOK()+" "+theme.Field("target", baseURL))
		fmt.Fprintln(a.Out, "  "+theme.Field("version", valueOr(info.Version, "running")))
	} else {
		fmt.Fprintln(a.Out, dotDown()+" "+theme.Field("target", baseURL))
		fmt.Fprintln(a.Out, "  "+theme.Dim.Render("offline"))
	}
}

// statusIdentity reports the ACTIVE V2 context's identity and token state,
// read-only from the XDG store (it never mints or refreshes a token; the /me
// probe runs only with an already-valid cached token).
func (a *app) statusIdentity(ctx context.Context) {
	fmt.Fprintln(a.Out, theme.Heading.Render("Identity"))

	st, err := sdkconfig.LoadState("")
	if err != nil {
		fmt.Fprintln(a.Out, dotDown()+" "+theme.Dim.Render("no active context — run `jentic register` (or `jentic migrate` on an upgraded machine)"))
		return
	}
	if st.InjectedBearerToken != "" {
		fmt.Fprintln(a.Out, dotOK()+" "+theme.Field("session", "file-less ($JENTIC_BEARER_TOKEN)"))
		fmt.Fprintln(a.Out, "  "+theme.Field("base_url", st.BaseURL))
		return
	}

	ref := auth.IdentityRef{Identity: st.IdentityName, Environment: st.EnvironmentName}

	// API-key credential (user identity): present == usable, no expiry to show.
	if key, kerr := auth.ReadAPIKey(ref); kerr == nil && key != "" {
		fmt.Fprintln(a.Out, dotOK()+" "+theme.Field("identity", st.IdentityName))
		fmt.Fprintln(a.Out, "  "+theme.Field("environment", st.EnvironmentName))
		fmt.Fprintln(a.Out, "  "+theme.Field("base_url", st.BaseURL))
		fmt.Fprintln(a.Out, "  "+theme.Field("auth", "api-key"))
		return
	}

	tokens, _ := auth.ReadTokens(ref)
	state, dot := tokenStatus(tokens)
	fmt.Fprintln(a.Out, dot+" "+theme.Field("identity", st.IdentityName))
	fmt.Fprintln(a.Out, "  "+theme.Field("environment", st.EnvironmentName))
	fmt.Fprintln(a.Out, "  "+theme.Field("base_url", st.BaseURL))
	fmt.Fprintln(a.Out, "  "+theme.Field("token", state))

	if tokens != nil && tokens.AccessToken != "" && time.Now().Before(tokens.ExpiresAt) {
		a.statusCatalogUpdates(ctx)
	}
}

// statusCatalogUpdates reports how many registered APIs have an upstream update
// available. Best-effort like the identity check: it runs only with an already
// valid cached token, uses a tiny page (the count is whole-manifest, page-stable),
// and degrades silently — a missing token, offline server, or old backend without
// the field simply prints nothing (never errors out `status`).
func (a *app) statusCatalogUpdates(ctx context.Context) {
	client, err := clictx.GetControlClient(ctx)
	if err != nil {
		return
	}
	yes, one := true, 1
	resp, err := client.ListCatalogWithResponse(ctx, &control.ListCatalogParams{
		OutdatedOnly: &yes,
		Limit:        &one,
	})
	if err != nil || resp.JSON200 == nil {
		return
	}
	outdated := 0
	if resp.JSON200.OutdatedCount != nil {
		outdated = *resp.JSON200.OutdatedCount
	}
	if outdated > 0 {
		fmt.Fprintln(a.Out, "  "+theme.Field("updates", fmt.Sprintf("%d available (run `jentic catalog outdated`)", outdated)))
	} else {
		fmt.Fprintln(a.Out, "  "+theme.Field("updates", "none"))
	}
}
