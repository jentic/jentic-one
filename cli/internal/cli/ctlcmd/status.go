package ctlcmd

import (
	"context"
	"fmt"

	"github.com/jentic/jentic-one/cli/internal/agentauth"
	"github.com/jentic/jentic-one/cli/internal/catalogclient"
	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/proc"
	"github.com/jentic/jentic-one/cli/internal/serverinfo"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

func newStatusCmd(app *app) *cobra.Command {
	opts := &identityOptions{}
	cmd := &cobra.Command{
		Use:   "status",
		Short: "Show install, server, and agent health",
		Long: "status is a health dashboard for the local jentic setup. It reports the\n" +
			"recorded install (mode/db/source), whether the control-plane server is\n" +
			"reachable and its version, and the agent identity/token state for a\n" +
			"profile. It degrades gracefully: missing pieces are reported, not fatal.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.statusE(cmd.Context(), opts)
		},
	}
	opts.Bind(cmd)
	return cmd
}

func (a *app) statusE(ctx context.Context, opts *identityOptions) error {
	profileName, baseURL, err := a.ResolveIdentity(opts.Profile, opts.BaseURL)
	if err != nil {
		return err
	}

	a.statusInstall()
	fmt.Fprintln(a.Out)
	a.statusServer(baseURL)
	fmt.Fprintln(a.Out)
	a.statusBroker()
	fmt.Fprintln(a.Out)
	a.statusAgent(ctx, profileName, baseURL)
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

// statusAgent reports the profile's registration and token state, and performs a
// best-effort identity check only with an already-valid cached token (so status
// never mints/refreshes a token as a side effect).
func (a *app) statusAgent(ctx context.Context, profileName, baseURL string) {
	fmt.Fprintln(a.Out, theme.Heading.Render("Agent"))

	sess, err := agentauth.Open(a.Paths, profileName, baseURL)
	if err != nil {
		fmt.Fprintln(a.Out, dotWarn()+" "+theme.Warnf("profile %q unavailable: %v", profileName, err))
		return
	}
	if sess.Meta.IsAPIKey() {
		dot := dotOK()
		if sess.APIKey == "" {
			dot = dotWarn()
		}
		fmt.Fprintln(a.Out, dot+" "+theme.Field("profile", profileName))
		fmt.Fprintln(a.Out, "  "+theme.Field("auth", "api-key"))
		fmt.Fprintln(a.Out, "  "+theme.Field("base_url", sess.Meta.BaseURL))
		if sess.Meta.AgentID != "" {
			fmt.Fprintln(a.Out, "  "+theme.Field("agent_id", sess.Meta.AgentID))
		}
		fmt.Fprintln(a.Out, "  "+theme.Field("key", cmdcore.APIKeyLabel(sess.APIKey)))
		if sess.APIKey != "" {
			if me, meErr := sess.Client.Me(ctx, sess.APIKey); meErr == nil {
				fmt.Fprintln(a.Out, "  "+theme.Field("identity", identityLabel(me)))
			} else {
				fmt.Fprintln(a.Out, "  "+theme.Dimf("identity check failed: %v", meErr))
			}
		}
		return
	}
	if sess.Meta.AgentID == "" {
		fmt.Fprintln(a.Out, dotDown()+" "+theme.Field("profile", profileName))
		fmt.Fprintln(a.Out, "  "+theme.Dim.Render("not registered — run `jentic register`"))
		return
	}

	tokens, _ := sess.Profile.LoadTokens()
	state, dot := tokenStatus(tokens)
	fmt.Fprintln(a.Out, dot+" "+theme.Field("profile", profileName))
	fmt.Fprintln(a.Out, "  "+theme.Field("base_url", sess.Meta.BaseURL))
	fmt.Fprintln(a.Out, "  "+theme.Field("agent_id", sess.Meta.AgentID))
	if sess.Meta.AgentName != "" {
		fmt.Fprintln(a.Out, "  "+theme.Field("name", sess.Meta.AgentName))
	}
	fmt.Fprintln(a.Out, "  "+theme.Field("token", state))

	if tokens != nil && !tokens.Expired(0) {
		if me, meErr := sess.Client.Me(ctx, tokens.AccessToken); meErr == nil {
			fmt.Fprintln(a.Out, "  "+theme.Field("identity", identityLabel(me)))
		} else {
			fmt.Fprintln(a.Out, "  "+theme.Dimf("identity check failed: %v", meErr))
		}
		a.statusCatalogUpdates(ctx, sess.Meta.BaseURL, tokens.AccessToken)
	}
}

// statusCatalogUpdates reports how many registered APIs have an upstream update
// available. Best-effort like the identity check: it runs only with an already
// valid cached token, uses a tiny page (the count is whole-manifest, page-stable),
// and degrades silently — a missing token, offline server, or old backend without
// the field simply prints nothing (never errors out `status`).
func (a *app) statusCatalogUpdates(ctx context.Context, baseURL, token string) {
	res, err := catalogclient.New(baseURL).List(ctx, token, catalogclient.ListParams{Outdated: true, Limit: 1})
	if err != nil {
		return
	}
	if res.OutdatedCount > 0 {
		fmt.Fprintln(a.Out, "  "+theme.Field("updates", fmt.Sprintf("%d available (run `jentic catalog outdated`)", res.OutdatedCount)))
	} else {
		fmt.Fprintln(a.Out, "  "+theme.Field("updates", "none"))
	}
}
