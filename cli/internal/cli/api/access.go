package api

import (
	"time"

	"github.com/spf13/cobra"
)

// newAccessCmd assembles the `jentic access` group: an agent's self-service
// surface for the access it is missing. It can see what it currently has
// (whoami), ask for more (request), and watch/withdraw those asks (list,
// status, withdraw). Granting is a human action and lives in the dashboard.
func newAccessCmd(app *app) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "access",
		Short: "Inspect your access and request more (credentials, scopes)",
		Long: "access is how an agent closes the gap between having an identity and having\n" +
			"the access to use it. An approved agent starts bound to no credentials, so its\n" +
			"first execute fails with a 403 telling it to request access. Use this group\n" +
			"to see what you can do now (whoami), ask a human to grant more (request),\n" +
			"and track those requests (list, status, withdraw).\n\n" +
			"Approval is a human action: filing a request prints an approve_url for your\n" +
			"operator. Output defaults to JSON when stdout is not a TTY (agent-friendly).",
		Args: cobra.NoArgs,
	}
	cmd.AddCommand(
		newAccessWhoamiCmd(app),
		newAccessRequestCmd(app),
		newAccessListCmd(app),
		newAccessStatusCmd(app),
		newAccessWithdrawCmd(app),
		newAccessRefreshCmd(app),
	)
	return cmd
}

func newAccessWhoamiCmd(app *app) *cobra.Command {
	var jsonFlag bool
	cmd := &cobra.Command{
		Use:   "whoami",
		Short: "Show your agent identity, scopes, and credential bindings",
		Long: "whoami answers \"what can I do right now?\" — your agent id, status, granted\n" +
			"scopes, and the credentials you are bound to (each with the APIs it serves).\n" +
			"An empty bindings list means you cannot execute against any API yet; use\n" +
			"`jentic access request` to ask.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.accessWhoamiE(cmd, jsonFlag)
		},
	}
	cmd.Flags().BoolVar(&jsonFlag, "json", false, "force JSON output (default when stdout is not a TTY)")
	return cmd
}

func newAccessRequestCmd(app *app) *cobra.Command {
	opts := &accessRequestOptions{}
	cmd := &cobra.Command{
		Use:   "request",
		Short: "File a request for credential bindings, scope grants, or provisioning plans",
		Long: "request files one access request for the access you are missing and prints an\n" +
			"approve_url for your human operator. Name an API you found in search to be\n" +
			"bound to a credential serving it (--api vendor/name), or ask for a scope\n" +
			"(--scope). Use --wait to block until a human decides (or --timeout elapses).\n\n" +
			"When no credential serves an API yet (a fresh import), use --provision\n" +
			"vendor/name to file the whole path to first execution as one plan: provision\n" +
			"a credential and bind yourself to it (with your proposed --rules-json). A\n" +
			"human fulfils the provision step in the dashboard (they enter the secret —\n" +
			"it never rides in your request) and approves. Use --auth to declare the\n" +
			"credential type you detected from the spec (bearer, api_key, basic, oauth2,\n" +
			"or none for a no-auth API).\n\n" +
			"All target flags repeat and combine, so a job needing several APIs files ONE\n" +
			"composite request the human decides in one sitting: each --provision appends\n" +
			"a provisioning plan, each --api/--scope appends a single item. With more\n" +
			"than one --provision, key --auth and --rules-json by the same\n" +
			"vendor/name[/version] passed to --provision; the bare form applies when\n" +
			"there is exactly one.\n\n" +
			"An existing pending request for the same resource is reused when this request\n" +
			"names a single target; a composite aborts instead (drop the already-pending\n" +
			"target or withdraw the older request, then re-file).\n\n" +
			"Exit codes:\n" +
			"  0 — request filed (or, with --wait, fully approved)\n" +
			"  2 — request was denied, expired, or withdrawn (only with --wait)\n" +
			"  3 — still pending when --timeout elapsed (only with --wait)\n" +
			"  4 — partially approved; not all items granted (only with --wait)",
		Example: "  jentic access request --api httpbin.org/httpbin --reason \"smoke test\"\n" +
			"  jentic access request --scope owner:credentials:read --json\n" +
			"  jentic access request --provision posthog.com/posthog-api --auth bearer \\\n" +
			"    --rules-json '[{\"effect\":\"allow\",\"methods\":[\"GET\"],\"path\":\".*\"}]' --wait\n" +
			"  jentic access request --provision slack.com/api --auth slack.com/api=bearer \\\n" +
			"    --provision googleapis.com/sheets --auth googleapis.com/sheets=oauth2 \\\n" +
			"    --api github.com/api --scope apis:write \\\n" +
			"    --reason \"release-notes automation\" --wait",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.accessRequestE(cmd, opts)
		},
	}
	cmd.Flags().StringArrayVar(&opts.apis, "api", nil, "request a binding to a credential serving this API (vendor/name[/version]; repeatable)")
	// --toolkit is the deprecated spelling of --api (toolkits were retired,
	// theme-5): hidden from help, accepted for one release, folded into the
	// same list. accessRequestE prints the deprecation warning.
	cmd.Flags().StringArrayVar(&opts.deprecatedToolkits, "toolkit", nil, "deprecated alias for --api")
	_ = cmd.Flags().MarkHidden("toolkit")
	// --toolkit-id stays registered (hidden) so old callers get compose()'s
	// re-file error naming --api instead of an unknown-flag failure.
	cmd.Flags().StringArrayVar(&opts.toolkitIDs, "toolkit-id", nil, "retired; use --api <vendor/name>")
	_ = cmd.Flags().MarkHidden("toolkit-id")
	cmd.Flags().StringArrayVar(&opts.scopes, "scope", nil, "request this scope be granted (repeatable)")
	cmd.Flags().StringArrayVar(&opts.provisions, "provision", nil, "file a full provisioning plan to make this API executable (vendor/name[/version]; repeatable)")
	cmd.Flags().StringArrayVar(&opts.auths, "auth", nil, "credential auth type for --provision: bearer, api_key, basic, oauth2, or none (default bearer); key by API when --provision repeats (vendor/name[/version]=<type>)")
	cmd.Flags().StringArrayVar(&opts.rulesJSONs, "rules-json", nil, "proposed permission rules for --provision, as a JSON array; key by API when --provision repeats (vendor/name[/version]=<json>)")
	cmd.Flags().StringVar(&opts.reason, "reason", "", "human-readable justification shown to the approver")
	cmd.Flags().BoolVar(&opts.wait, "wait", false, "block until the request is decided")
	cmd.Flags().DurationVar(&opts.timeout, "timeout", 10*time.Minute, "max time to wait with --wait")
	cmd.Flags().BoolVar(&opts.json, "json", false, "force JSON output (default when stdout is not a TTY)")
	return cmd
}

func newAccessListCmd(app *app) *cobra.Command {
	opts := &accessListOptions{}
	cmd := &cobra.Command{
		Use:   "list",
		Short: "List your access requests",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.accessListE(cmd, opts)
		},
	}
	cmd.Flags().StringVar(&opts.status, "status", "", "filter by status (pending, approved, denied, withdrawn, …)")
	cmd.Flags().IntVar(&opts.limit, "limit", 0, "max results per page (0 = server default)")
	cmd.Flags().StringVar(&opts.cursor, "cursor", "", "pagination cursor from a previous response")
	cmd.Flags().BoolVar(&opts.all, "all", false, "follow pagination and return all results")
	cmd.Flags().BoolVar(&opts.json, "json", false, "force JSON output (default when stdout is not a TTY)")
	return cmd
}

func newAccessStatusCmd(app *app) *cobra.Command {
	var jsonFlag bool
	cmd := &cobra.Command{
		Use:   "status <request-id>",
		Short: "Show one access request, including per-item state and approve_url",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return app.accessStatusE(cmd, args[0], jsonFlag)
		},
	}
	cmd.Flags().BoolVar(&jsonFlag, "json", false, "force JSON output (default when stdout is not a TTY)")
	return cmd
}

func newAccessWithdrawCmd(app *app) *cobra.Command {
	var jsonFlag bool
	cmd := &cobra.Command{
		Use:   "withdraw <request-id>",
		Short: "Withdraw a pending access request",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return app.accessWithdrawE(cmd, args[0], jsonFlag)
		},
	}
	cmd.Flags().BoolVar(&jsonFlag, "json", false, "force JSON output (default when stdout is not a TTY)")
	return cmd
}

func newAccessRefreshCmd(app *app) *cobra.Command {
	var jsonFlag bool
	cmd := &cobra.Command{
		Use:   "refresh",
		Short: "Re-mint your token so newly granted scopes take effect",
		Long: "refresh forces a fresh token mint, picking up any scopes granted since your\n" +
			"current token was issued. Tokens bake in their scopes at mint time, so after\n" +
			"an approved `scope:grant` request your existing token still can't exercise the\n" +
			"new scope until you refresh. Run this when `jentic access whoami` shows a scope\n" +
			"under \"granted\" that isn't yet active on your token.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.accessRefreshE(cmd, jsonFlag)
		},
	}
	cmd.Flags().BoolVar(&jsonFlag, "json", false, "force JSON output (default when stdout is not a TTY)")
	return cmd
}

type accessListOptions struct {
	status string
	limit  int
	cursor string
	all    bool
	json   bool
}
