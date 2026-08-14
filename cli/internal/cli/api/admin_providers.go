package api

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"sort"
	"strings"

	"github.com/charmbracelet/huh"
	"github.com/charmbracelet/x/term"
	"github.com/jentic/jentic-one/cli/client/generated/control"
	"github.com/jentic/jentic-one/cli/internal/cli/prompt"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

// providerSetOptions binds the flags for `admin config providers set`. Secret
// values (client_secret) are never accepted as a flag — they are prompted for
// interactively (no echo) or read from stdin — so they never land in shell
// history. Non-secret fields map straight onto the provider config.
type providerSetOptions struct {
	projectID      string
	clientID       string
	environment    string
	connectBaseURL string
	secretStdin    bool
	json           bool
}

// newAdminProvidersCmd is the `admin config providers` parent.
func newAdminProvidersCmd(app *app) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "providers",
		Short: "Manage credential provider configuration",
		Long: "providers configures credential providers (e.g. pipedream) at runtime.\n" +
			"A successful `set` rebuilds the server's provider registry so the change\n" +
			"takes effect without a restart. Secret fields are encrypted at rest and\n" +
			"redacted on read.",
		Args: cobra.NoArgs,
	}
	cmd.AddCommand(
		newProvidersSetCmd(app),
		newProvidersGetCmd(app),
		newProvidersListCmd(app),
	)
	return cmd
}

func newProvidersSetCmd(app *app) *cobra.Command {
	opts := &providerSetOptions{}
	cmd := &cobra.Command{
		Use:   "set <name>",
		Short: "Create or update a provider config",
		Long: "set validates the provider-specific fields, encrypts the secret, and\n" +
			"activates the config at runtime. For `pipedream` the recognised flags are\n" +
			"--project-id, --client-id, --environment, and --connect-base-url; the\n" +
			"client_secret is prompted for without echo (or read from stdin with\n" +
			"--secret-stdin).",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return app.providersSet(cmd, opts, args[0])
		},
	}
	cmd.Flags().StringVar(&opts.projectID, "project-id", "", "provider project id")
	cmd.Flags().StringVar(&opts.clientID, "client-id", "", "provider client id")
	cmd.Flags().StringVar(&opts.environment, "environment", "", "provider environment (e.g. production)")
	cmd.Flags().StringVar(&opts.connectBaseURL, "connect-base-url", "", "provider connect base URL")
	cmd.Flags().BoolVar(&opts.secretStdin, "secret-stdin", false, "read client_secret from stdin instead of prompting")
	cmd.Flags().BoolVar(&opts.json, "json", false, "emit JSON output")
	return cmd
}

func newProvidersGetCmd(app *app) *cobra.Command {
	jsonFlag := false
	cmd := &cobra.Command{
		Use:   "get <name>",
		Short: "Show a provider config (secrets redacted)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return app.providersGet(cmd, jsonFlag, args[0])
		},
	}
	cmd.Flags().BoolVar(&jsonFlag, "json", false, "emit JSON output")
	return cmd
}

func newProvidersListCmd(app *app) *cobra.Command {
	jsonFlag := false
	cmd := &cobra.Command{
		Use:   "list",
		Short: "List provider configs (secrets redacted)",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.providersList(cmd, jsonFlag)
		},
	}
	cmd.Flags().BoolVar(&jsonFlag, "json", false, "emit JSON output")
	return cmd
}

// ── auth ─────────────────────────────────────────────────────────────────────

// adminClient resolves the generated control client for the active context
// (ARCH-21: auth/session/retry are baked into the SDK transport, so there is no
// base-URL/token to thread through the admin calls).
func (a *app) adminClient(ctx context.Context) (*control.ClientWithResponses, error) {
	return a.controlClient(ctx)
}

// ── set ──────────────────────────────────────────────────────────────────────

func (a *app) providersSet(cmd *cobra.Command, opts *providerSetOptions, name string) error {
	ctx := cmd.Context()
	secret, err := a.readProviderSecret(opts.secretStdin)
	if err != nil {
		return err
	}

	config := map[string]any{}
	putIfSet(config, "project_id", opts.projectID)
	putIfSet(config, "client_id", opts.clientID)
	putIfSet(config, "environment", opts.environment)
	putIfSet(config, "connect_base_url", opts.connectBaseURL)
	if secret != "" {
		config["client_secret"] = secret
	}

	client, err := a.adminClient(ctx)
	if err != nil {
		return err
	}
	resp, err := client.SetProviderConfigWithResponse(ctx, name,
		control.SetProviderConfigJSONRequestBody{Config: config})
	if err := apiErrorFor(resp, err); err != nil {
		return err
	}
	rec := resp.JSON200

	if jsonOrPretty(cmd, opts.json) {
		return writeJSON(a.Out, rec)
	}
	fmt.Fprintln(a.Out, dotOK()+" "+theme.Accent.Render(rec.Name)+" "+theme.Dim.Render("configured"))
	a.printProviderDetail(rec)
	return nil
}

// readProviderSecret obtains client_secret from stdin (when --secret-stdin) or
// an interactive no-echo prompt. A non-TTY stdin without --secret-stdin yields
// an empty secret; the server treats an omitted secret as "keep the existing
// one" (read-modify-merge), so on update the previously stored value is
// preserved. On first create there is nothing to merge, so the server rejects
// a config with no client_secret.
func (a *app) readProviderSecret(fromStdin bool) (string, error) {
	if fromStdin {
		data, err := io.ReadAll(bufio.NewReader(os.Stdin))
		if err != nil {
			return "", fmt.Errorf("read client_secret from stdin: %w", err)
		}
		return strings.TrimRight(string(data), "\r\n"), nil
	}
	if !term.IsTerminal(os.Stdin.Fd()) {
		return "", nil
	}
	var secret string
	form := prompt.NewForm(
		huh.NewGroup(
			prompt.Input().Title("client_secret").EchoMode(huh.EchoModePassword).
				Value(&secret).
				Validate(func(s string) error {
					if s == "" {
						return errors.New("client_secret is required")
					}
					return nil
				}),
		),
	)
	if err := form.Run(); err != nil {
		return "", err
	}
	return secret, nil
}

// ── get ──────────────────────────────────────────────────────────────────────

func (a *app) providersGet(cmd *cobra.Command, jsonFlag bool, name string) error {
	ctx := cmd.Context()
	client, err := a.adminClient(ctx)
	if err != nil {
		return err
	}
	resp, err := client.GetProviderConfigWithResponse(ctx, name)
	if err := apiErrorFor(resp, err); err != nil {
		return err
	}
	rec := resp.JSON200
	if jsonOrPretty(cmd, jsonFlag) {
		return writeJSON(a.Out, rec)
	}
	a.printProviderDetail(rec)
	return nil
}

// ── list ─────────────────────────────────────────────────────────────────────

func (a *app) providersList(cmd *cobra.Command, jsonFlag bool) error {
	ctx := cmd.Context()
	client, err := a.adminClient(ctx)
	if err != nil {
		return err
	}
	resp, err := client.ListProviderConfigsWithResponse(ctx)
	if err := apiErrorFor(resp, err); err != nil {
		return err
	}
	recs := resp.JSON200.Data
	if jsonOrPretty(cmd, jsonFlag) {
		if recs == nil {
			recs = []control.ProviderConfigResponse{}
		}
		return writeList(a.Out, recs, "", nil)
	}
	fmt.Fprintln(a.Out, theme.Heading.Render("Provider configs"))
	if len(recs) == 0 {
		fmt.Fprintln(a.Out, dotDown()+" "+theme.Dim.Render("none configured"))
		return nil
	}
	for _, rec := range recs {
		fmt.Fprintln(a.Out, dotOK()+" "+theme.Accent.Render(rec.Name))
	}
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Dim.Render(fmt.Sprintf("%d provider(s)", len(recs))))
	return nil
}

// ── render ───────────────────────────────────────────────────────────────────

func (a *app) printProviderDetail(rec *control.ProviderConfigResponse) {
	fmt.Fprintln(a.Out, theme.Heading.Render(rec.Name))
	keys := make([]string, 0, len(rec.Config))
	for k := range rec.Config {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		fmt.Fprintln(a.Out, "  "+theme.Field(k, fmt.Sprintf("%v", rec.Config[k])))
	}
}

// putIfSet writes a non-empty string value into the config map.
func putIfSet(m map[string]any, key, value string) {
	if value != "" {
		m[key] = value
	}
}
