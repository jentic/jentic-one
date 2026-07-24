package cmd

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/jentic/jentic-one/cli/internal/accessclient"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

var (
	errAccessTargetRequired = errors.New("specify what to request: --toolkit <vendor/name>, --toolkit-id <tk_…>, or --scope <scope>")
	errAccessTargetConflict = errors.New("specify exactly one of --toolkit, --toolkit-id, or --scope")
	errAccessWaitTimeout    = errors.New("timed out waiting for a decision")
)

// newAccessCmd assembles the `jentic access` group: an agent's self-service
// surface for the access it is missing. It can see what it currently has
// (whoami), ask for more (request), and watch/withdraw those asks (list,
// status, withdraw). Granting is a human action and lives in the dashboard.
func newAccessCmd(app *App) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "access",
		Short: "Inspect your access and request more (toolkits, scopes)",
		Long: "access is how an agent closes the gap between having an identity and having\n" +
			"the access to use it. An approved agent starts bound to no toolkits, so its\n" +
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

func newAccessWhoamiCmd(app *App) *cobra.Command {
	ident := &identityOptions{}
	var jsonFlag bool
	cmd := &cobra.Command{
		Use:   "whoami",
		Short: "Show your agent identity, scopes, and toolkit bindings",
		Long: "whoami answers \"what can I do right now?\" — your agent id, status, granted\n" +
			"scopes, and the toolkits you are bound to. An empty bindings list means you\n" +
			"cannot execute against any API yet; use `jentic access request` to ask.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.accessWhoamiE(cmd, ident, jsonFlag)
		},
	}
	cmd.Flags().BoolVar(&jsonFlag, "json", false, "force JSON output (default when stdout is not a TTY)")
	ident.bind(cmd)
	return cmd
}

func newAccessRequestCmd(app *App) *cobra.Command {
	ident := &identityOptions{}
	opts := &accessRequestOptions{}
	cmd := &cobra.Command{
		Use:   "request",
		Short: "File a request for a toolkit binding or a scope grant",
		Long: "request files an access request for the access you are missing and prints an\n" +
			"approve_url for your human operator. Name the toolkit by the API you found in\n" +
			"search (--toolkit vendor/name), by id (--toolkit-id tk_…), or ask for a scope\n" +
			"(--scope). Use --wait to block until a human decides (or --timeout elapses).\n\n" +
			"When nothing serves the API yet (a fresh import with no toolkit/credential),\n" +
			"use --provision vendor/name to file the whole path to first execution as one\n" +
			"plan: create a toolkit, provision a credential, bind it (with your proposed\n" +
			"--rules-json), and bind yourself. A human fulfils the create/provision steps\n" +
			"in the dashboard (they enter the secret — it never rides in your request) and\n" +
			"approves. Use --auth to declare the credential type you detected from the spec\n" +
			"(bearer, api_key, basic, oauth2, or none for a no-auth API).\n\n" +
			"An existing pending request for the same resource is reused, not duplicated.\n\n" +
			"Exit codes:\n" +
			"  0 — request filed (or, with --wait, fully approved)\n" +
			"  2 — request was denied, expired, or withdrawn (only with --wait)\n" +
			"  3 — still pending when --timeout elapsed (only with --wait)\n" +
			"  4 — partially approved; not all items granted (only with --wait)",
		Example: "  jentic access request --toolkit httpbin.org/httpbin --reason \"smoke test\"\n" +
			"  jentic access request --toolkit-id tk_123 --wait\n" +
			"  jentic access request --scope owner:toolkits:read --json\n" +
			"  jentic access request --provision posthog.com/posthog-api --auth bearer \\\n" +
			"    --rules-json '[{\"effect\":\"allow\",\"methods\":[\"GET\"],\"path\":\".*\"}]' --wait",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.accessRequestE(cmd, ident, opts)
		},
	}
	cmd.Flags().StringVar(&opts.toolkit, "toolkit", "", "request a binding to the toolkit serving this API (vendor/name[/version])")
	cmd.Flags().StringVar(&opts.toolkitID, "toolkit-id", "", "request a binding to this toolkit id (tk_…)")
	cmd.Flags().StringVar(&opts.scope, "scope", "", "request this scope be granted")
	cmd.Flags().StringVar(&opts.provision, "provision", "", "file a full provisioning plan to make this API executable (vendor/name[/version])")
	cmd.Flags().StringVar(&opts.auth, "auth", "", "credential auth type for --provision: bearer, api_key, basic, oauth2, or none (default bearer)")
	cmd.Flags().StringVar(&opts.rulesJSON, "rules-json", "", "proposed permission rules for --provision, as a JSON array")
	cmd.Flags().StringVar(&opts.reason, "reason", "", "human-readable justification shown to the approver")
	cmd.Flags().BoolVar(&opts.wait, "wait", false, "block until the request is decided")
	cmd.Flags().DurationVar(&opts.timeout, "timeout", 10*time.Minute, "max time to wait with --wait")
	cmd.Flags().BoolVar(&opts.json, "json", false, "force JSON output (default when stdout is not a TTY)")
	ident.bind(cmd)
	return cmd
}

func newAccessListCmd(app *App) *cobra.Command {
	ident := &identityOptions{}
	opts := &accessListOptions{}
	cmd := &cobra.Command{
		Use:   "list",
		Short: "List your access requests",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.accessListE(cmd, ident, opts)
		},
	}
	cmd.Flags().StringVar(&opts.status, "status", "", "filter by status (pending, approved, denied, withdrawn, …)")
	cmd.Flags().IntVar(&opts.limit, "limit", 0, "max results per page (0 = server default)")
	cmd.Flags().StringVar(&opts.cursor, "cursor", "", "pagination cursor from a previous response")
	cmd.Flags().BoolVar(&opts.all, "all", false, "follow pagination and return all results")
	cmd.Flags().BoolVar(&opts.json, "json", false, "force JSON output (default when stdout is not a TTY)")
	ident.bind(cmd)
	return cmd
}

func newAccessStatusCmd(app *App) *cobra.Command {
	ident := &identityOptions{}
	var jsonFlag bool
	cmd := &cobra.Command{
		Use:   "status <request-id>",
		Short: "Show one access request, including per-item state and approve_url",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return app.accessStatusE(cmd, ident, args[0], jsonFlag)
		},
	}
	cmd.Flags().BoolVar(&jsonFlag, "json", false, "force JSON output (default when stdout is not a TTY)")
	ident.bind(cmd)
	return cmd
}

func newAccessWithdrawCmd(app *App) *cobra.Command {
	ident := &identityOptions{}
	var jsonFlag bool
	cmd := &cobra.Command{
		Use:   "withdraw <request-id>",
		Short: "Withdraw a pending access request",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return app.accessWithdrawE(cmd, ident, args[0], jsonFlag)
		},
	}
	cmd.Flags().BoolVar(&jsonFlag, "json", false, "force JSON output (default when stdout is not a TTY)")
	ident.bind(cmd)
	return cmd
}

func newAccessRefreshCmd(app *App) *cobra.Command {
	ident := &identityOptions{}
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
			return app.accessRefreshE(cmd, ident, jsonFlag)
		},
	}
	cmd.Flags().BoolVar(&jsonFlag, "json", false, "force JSON output (default when stdout is not a TTY)")
	ident.bind(cmd)
	return cmd
}

type accessRequestOptions struct {
	toolkit   string
	toolkitID string
	scope     string
	provision string
	auth      string
	rulesJSON string
	reason    string
	wait      bool
	timeout   time.Duration
	json      bool
}

// item builds the single request item from the chosen target, enforcing that
// exactly one of --toolkit/--toolkit-id/--scope is set.
func (o *accessRequestOptions) item() (accessclient.Item, error) {
	chosen := 0
	for _, s := range []string{o.toolkit, o.toolkitID, o.scope} {
		if strings.TrimSpace(s) != "" {
			chosen++
		}
	}
	switch {
	case chosen == 0:
		return accessclient.Item{}, errAccessTargetRequired
	case chosen > 1:
		return accessclient.Item{}, errAccessTargetConflict
	}

	switch {
	case o.scope != "":
		return accessclient.Item{ResourceType: "scope", Action: "grant", ResourceID: o.scope}, nil
	case o.toolkitID != "":
		return accessclient.Item{ResourceType: "toolkit", Action: "bind", ResourceID: o.toolkitID}, nil
	default:
		ref, err := parseToolkitRef(o.toolkit)
		if err != nil {
			return accessclient.Item{}, err
		}
		return accessclient.Item{ResourceType: "toolkit", Action: "bind", ResourceReference: ref}, nil
	}
}

// validAuthTypes are the credential auth types --auth accepts. "none" marks a
// no-auth API: the plan's credential:provision item carries security_scheme=
// no_auth and the wizard auto-creates a NO_AUTH credential (no secret prompt).
var validAuthTypes = map[string]bool{
	"bearer": true, "api_key": true, "basic": true, "oauth2": true, "none": true,
}

// plan builds a full provisioning plan for --provision: the ordered set of
// items describing the whole path to first execution. The agent files intent
// (create toolkit, provision a credential, bind it with proposed rules, bind the
// agent); a human fulfils the create/provision steps via the dashboard, which
// writes the resulting ids back onto the bind items before approving. Returns
// the items in fulfilment order.
func (o *accessRequestOptions) plan() ([]accessclient.Item, error) {
	ref, err := parseToolkitRef(o.provision)
	if err != nil {
		return nil, err
	}

	auth := strings.TrimSpace(o.auth)
	if auth == "" {
		auth = "bearer"
	}
	if !validAuthTypes[auth] {
		return nil, fmt.Errorf("--auth must be one of bearer, api_key, basic, oauth2, none; got %q", auth)
	}
	// The credential:provision item carries the credential's security_scheme,
	// which the UI maps to a CredentialType. "none" maps to the NO_AUTH type
	// (`no_auth`); the other flag values already match the scheme names.
	authScheme := auth
	if auth == "none" {
		authScheme = "no_auth"
	}

	rules, err := parseProposedRules(o.rulesJSON)
	if err != nil {
		return nil, err
	}

	items := []accessclient.Item{
		// Step 1: create a toolkit that will serve this API.
		{ResourceType: "toolkit", Action: "create", ResourceReference: ref},
	}
	// Step 2: provision a credential for this API. security_scheme carries the
	// agent-detected auth type so the operator's credential form can pre-select
	// it; the operator enters the secret — it never rides in the agent-filed
	// plan. For a no-auth API (`--auth none`) we still emit this item with
	// security_scheme=no_auth: a credential row is required for the
	// credential:bind effect to attach the toolkit binding + rules to (the
	// broker keys rules on `(toolkit, credential)` and resolves a no_auth
	// credential as a no-op auth). The wizard auto-creates the NO_AUTH
	// credential — the operator is not prompted for a secret.
	provRef := map[string]any{}
	for k, v := range ref {
		provRef[k] = v
	}
	provRef["security_scheme"] = authScheme
	items = append(items, accessclient.Item{
		ResourceType: "credential", Action: "provision", ResourceReference: provRef,
	})
	// Step 3+4: bind the (to-be-created) credential to the (to-be-created)
	// toolkit, carrying the agent's proposed first-pass rules. The operator
	// amends the concrete credential/toolkit ids onto this item before approval.
	items = append(items, accessclient.Item{
		ResourceType: "credential", Action: "bind", Rules: rules,
	})
	// Step 5: bind the agent to the toolkit, named by the same API reference.
	items = append(items, accessclient.Item{
		ResourceType: "toolkit", Action: "bind", ResourceReference: ref,
	})
	return items, nil
}

// parseProposedRules decodes the agent's proposed permission rules from a JSON
// array (--rules-json). Empty input yields no rules (the server substitutes a
// read-only default on the credential:bind item).
func parseProposedRules(raw string) ([]accessclient.Rule, error) {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return nil, nil
	}
	var rules []accessclient.Rule
	if err := json.Unmarshal([]byte(raw), &rules); err != nil {
		return nil, fmt.Errorf("--rules-json must be a JSON array of rules: %w", err)
	}
	return rules, nil
}

// parseToolkitRef splits "vendor/name[/version]" into a resource_reference. The
// agent names the API it discovered via search; the server resolves it to a
// concrete toolkit at decide time.
func parseToolkitRef(s string) (map[string]any, error) {
	parts := strings.Split(strings.TrimSpace(s), "/")
	if len(parts) < 2 || parts[0] == "" || parts[1] == "" {
		return nil, fmt.Errorf("--toolkit must be vendor/name or vendor/name/version, got %q", s)
	}
	ref := map[string]any{"vendor": parts[0], "name": parts[1]}
	if len(parts) >= 3 && parts[2] != "" {
		ref["version"] = parts[2]
	}
	return ref, nil
}

type accessListOptions struct {
	status string
	limit  int
	cursor string
	all    bool
	json   bool
}

func (a *App) accessWhoamiE(cmd *cobra.Command, ident *identityOptions, jsonFlag bool) error {
	baseURL, token, err := a.agentSession(cmd.Context(), ident)
	if err != nil {
		return err
	}
	me, err := accessclient.New(baseURL).Me(cmd.Context(), token)
	if err != nil {
		return err
	}
	if jsonOrPretty(cmd, jsonFlag) {
		return writeJSON(a.Out, me)
	}
	a.printMe(me)
	return nil
}

func (a *App) accessRequestE(cmd *cobra.Command, ident *identityOptions, opts *accessRequestOptions) error {
	var items []accessclient.Item
	if strings.TrimSpace(opts.provision) != "" {
		// --provision is mutually exclusive with the single-item target flags.
		if opts.toolkit != "" || opts.toolkitID != "" || opts.scope != "" {
			return errors.New("--provision cannot be combined with --toolkit, --toolkit-id, or --scope")
		}
		planItems, err := opts.plan()
		if err != nil {
			return err
		}
		items = planItems
	} else {
		if opts.auth != "" || opts.rulesJSON != "" {
			return errors.New("--auth and --rules-json only apply with --provision")
		}
		item, err := opts.item()
		if err != nil {
			return err
		}
		items = []accessclient.Item{item}
	}

	baseURL, token, err := a.agentSession(cmd.Context(), ident)
	if err != nil {
		return err
	}
	client := accessclient.New(baseURL)

	req, err := client.File(cmd.Context(), token, accessclient.FileRequest{
		Reason: opts.reason,
		Items:  items,
	})
	if err != nil {
		var dup *accessclient.DuplicatePendingError
		if !errors.As(err, &dup) {
			return err
		}
		fmt.Fprintln(a.Out, theme.Warnf("A pending request already exists (%s); attaching to it.", dup.ExistingRequestID))
		req, err = client.Get(cmd.Context(), token, dup.ExistingRequestID)
		if err != nil {
			return err
		}
	}

	timedOut := false
	if opts.wait && !req.IsTerminal() {
		waited, waitErr := a.pollAccessRequest(cmd.Context(), client, token, req.ID, opts.timeout)
		if waitErr != nil {
			if errors.Is(waitErr, errAccessWaitTimeout) {
				// Print the (still-pending) request so the agent has the id and
				// approve_url, then signal "pending" via a distinct exit code.
				timedOut = true
			} else {
				return waitErr
			}
		} else {
			req = waited
		}
	}

	if jsonOrPretty(cmd, opts.json) {
		if err := writeJSON(a.Out, req); err != nil {
			return err
		}
	} else {
		a.printRequest(req, true)
	}

	switch {
	case timedOut:
		fmt.Fprintln(a.Err, theme.Warnf("Still pending after %s; check `jentic access status %s` later.", opts.timeout, req.ID))
		return &exitCodeError{code: 3}
	case req.Status == accessclient.StatusDenied:
		return &exitCodeError{code: 2}
	case req.Status == accessclient.StatusExpired, req.Status == accessclient.StatusWithdrawn:
		// Terminal but not granted: the agent still cannot do what it asked, so
		// this must not look like success (exit 0). Treat it like a denial.
		fmt.Fprintln(a.Err, theme.Warnf("Request %s is %s, not approved; nothing was granted.", req.ID, req.Status))
		return &exitCodeError{code: 2}
	case req.Status == accessclient.StatusPartiallyApproved:
		// A newly-granted scope only takes effect once re-minted into the token;
		// do it for the agent so it needn't run a separate `access refresh`.
		a.refreshIfScopeGranted(cmd, ident, req)
		// Some items were approved but at least one was not, so the capability
		// the agent asked for is not fully granted. Signal a distinct non-zero
		// code (not success) so a scripted agent doesn't proceed as if it can
		// now execute; the printed items show which line items remain.
		fmt.Fprintln(a.Err, theme.Warnf("Partially approved — not all requested items were granted; see `jentic access status %s`.", req.ID))
		return &exitCodeError{code: 4}
	}
	// Fully approved. A newly-granted scope bakes into the token at mint time, so
	// re-mint now if the request granted one — the agent can then execute
	// immediately without a separate `access refresh`. A binding-only plan
	// (toolkit/credential binds, no scope) needs no re-mint: bindings are live
	// server-side, so this is a no-op in that case.
	a.refreshIfScopeGranted(cmd, ident, req)
	return nil
}

// refreshIfScopeGranted re-mints the agent's token when (and only when) the
// decided request granted a new scope — the one thing that is baked into the
// token at mint time and so needs a refresh to become usable. Toolkit/credential
// bindings are resolved live by the broker, so a `--provision`/`--toolkit` plan
// needs no re-mint; re-minting anyway would be a wasted round-trip. Best-effort:
// a mint failure is non-fatal (the agent can still run `jentic access refresh`),
// and API-key profiles (no mintable token) are skipped.
func (a *App) refreshIfScopeGranted(cmd *cobra.Command, ident *identityOptions, req *accessclient.Request) {
	if !requestGrantedScope(req) {
		return
	}
	sess, _, err := a.agentSessionOpen(ident)
	if err != nil || sess.Meta.IsAPIKey() {
		return
	}
	if _, err := sess.MintFresh(cmd.Context()); err != nil {
		fmt.Fprintln(a.Err, theme.Dimf("granted scope not yet on your token; run `jentic access refresh` to pick it up"))
	}
}

// requestGrantedScope reports whether a decided request approved a scope:grant
// item — the only grant that bakes into the token and so needs a re-mint.
// Toolkit/credential binds are resolved live by the broker, so a binding-only
// plan returns false (no re-mint needed).
func requestGrantedScope(req *accessclient.Request) bool {
	for _, it := range req.Items {
		if it.ResourceType == "scope" && it.Action == "grant" && it.Status == "approved" {
			return true
		}
	}
	return false
}

func (a *App) accessListE(cmd *cobra.Command, ident *identityOptions, opts *accessListOptions) error {
	baseURL, token, err := a.agentSession(cmd.Context(), ident)
	if err != nil {
		return err
	}
	client := accessclient.New(baseURL)

	const maxPages = 1000
	var all []accessclient.Request
	var hasMore bool
	var nextCursor string
	cursor := opts.cursor

	for page := 0; ; page++ {
		res, listErr := client.List(cmd.Context(), token, opts.status, cursor, opts.limit)
		if listErr != nil {
			return listErr
		}
		all = append(all, res.Data...)
		hasMore = res.HasMore
		nextCursor = res.NextCursor
		if !opts.all || !res.HasMore || res.NextCursor == "" {
			break
		}
		if page+1 >= maxPages {
			break
		}
		cursor = res.NextCursor
	}

	if jsonOrPretty(cmd, opts.json) {
		return writeJSON(a.Out, map[string]any{
			"data":        all,
			"has_more":    hasMore,
			"next_cursor": nextCursor,
		})
	}
	a.printRequestList(all, hasMore)
	return nil
}

func (a *App) accessStatusE(cmd *cobra.Command, ident *identityOptions, id string, jsonFlag bool) error {
	baseURL, token, err := a.agentSession(cmd.Context(), ident)
	if err != nil {
		return err
	}
	req, err := accessclient.New(baseURL).Get(cmd.Context(), token, id)
	if err != nil {
		return err
	}
	if jsonOrPretty(cmd, jsonFlag) {
		return writeJSON(a.Out, req)
	}
	a.printRequest(req, true)
	return nil
}

func (a *App) accessWithdrawE(cmd *cobra.Command, ident *identityOptions, id string, jsonFlag bool) error {
	baseURL, token, err := a.agentSession(cmd.Context(), ident)
	if err != nil {
		return err
	}
	req, err := accessclient.New(baseURL).Withdraw(cmd.Context(), token, id)
	if err != nil {
		return err
	}
	if jsonOrPretty(cmd, jsonFlag) {
		return writeJSON(a.Out, req)
	}
	fmt.Fprintln(a.Out, theme.Successf("Withdrew access request %s.", req.ID))
	a.printRequest(req, false)
	return nil
}

func (a *App) accessRefreshE(cmd *cobra.Command, ident *identityOptions, jsonFlag bool) error {
	sess, profileName, err := a.agentSessionOpen(ident)
	if err != nil {
		return err
	}
	if sess.Meta.IsAPIKey() {
		return fmt.Errorf("profile %q authenticates with a static API key, which has no token to refresh; "+
			"its scopes change only when an admin updates the key", profileName)
	}
	// Force a fresh assertion mint (not a refresh-token rotation, which would
	// carry the old token's scopes forward unchanged) so the server re-reads the
	// agent's current scope grants. See issue #673.
	if _, err := sess.MintFresh(cmd.Context()); err != nil {
		return agentAuthErr(err, profileName)
	}
	token, err := sess.ValidToken(cmd.Context())
	if err != nil {
		return agentAuthErr(err, profileName)
	}
	me, err := accessclient.New(sess.Meta.BaseURL).Me(cmd.Context(), token)
	if err != nil {
		return err
	}
	if jsonOrPretty(cmd, jsonFlag) {
		return writeJSON(a.Out, me)
	}
	fmt.Fprintln(a.Out, theme.Successf("Refreshed token for %s.", me.ID))
	a.printMe(me)
	return nil
}

// pollAccessRequest loops Get until the request leaves the pending state, the
// timeout elapses, or the context is cancelled. It reuses the register poll
// cadence so the wait backs off the same way.
func (a *App) pollAccessRequest(ctx context.Context, client *accessclient.Client, token, id string, timeout time.Duration) (*accessclient.Request, error) {
	fmt.Fprintln(a.Out, theme.Dimf("Waiting for a human to decide request %s (up to %s; Ctrl-C to stop) …", id, timeout))
	deadline := time.Now().Add(timeout)
	delay := pollInitialDelay
	for {
		if time.Now().After(deadline) {
			return nil, fmt.Errorf("%w after %s (request %s)", errAccessWaitTimeout, timeout, id)
		}
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		case <-time.After(delay):
		}
		if delay < pollMaxDelay {
			delay += pollDelayStep
		}
		req, err := client.Get(ctx, token, id)
		if err != nil {
			return nil, err
		}
		if req.IsTerminal() {
			return req, nil
		}
	}
}

func (a *App) printMe(me *accessclient.Me) {
	fmt.Fprintln(a.Out, theme.Heading.Render("Identity"))
	fmt.Fprintln(a.Out, "  "+theme.Field("agent", me.ID))
	if me.Name != "" {
		fmt.Fprintln(a.Out, "  "+theme.Field("name", me.Name))
	}
	fmt.Fprintln(a.Out, "  "+theme.Field("status", me.Status))
	scopes := "none"
	if len(me.Scopes) > 0 {
		scopes = strings.Join(me.Scopes, ", ")
	}
	fmt.Fprintln(a.Out, "  "+theme.Field("scopes", scopes))
	if stale := me.StaleScopes(); len(stale) > 0 {
		fmt.Fprintln(a.Out, "  "+theme.Warnf("granted but not yet on your token: %s", strings.Join(stale, ", ")))
		fmt.Fprintln(a.Out, "  "+theme.Dim.Render("run `jentic access refresh` to pick them up"))
	}

	fmt.Fprintln(a.Out, theme.Heading.Render("Toolkit bindings"))
	if len(me.ToolkitBindings) == 0 {
		fmt.Fprintln(a.Out, "  "+theme.Dim.Render("none — you cannot execute yet; run `jentic access request --toolkit <vendor/name>`"))
		return
	}
	for _, b := range me.ToolkitBindings {
		if b.Name != "" {
			fmt.Fprintln(a.Out, "  "+theme.Command.Render(b.Name)+"  "+theme.Dim.Render(b.ToolkitID))
		} else {
			fmt.Fprintln(a.Out, "  "+theme.Command.Render(b.ToolkitID))
		}
	}
}

func (a *App) printRequestList(reqs []accessclient.Request, hasMore bool) {
	fmt.Fprintln(a.Out, theme.Heading.Render("Access Requests"))
	if len(reqs) == 0 {
		fmt.Fprintln(a.Out, "  "+theme.Dim.Render("no requests"))
		return
	}
	for i := range reqs {
		r := &reqs[i]
		fmt.Fprintln(a.Out, "  "+theme.Command.Render(r.ID)+"  "+statusStyle(r.Status))
		for j := range r.Items {
			fmt.Fprintln(a.Out, "    "+theme.Dim.Render(itemSummary(&r.Items[j])))
		}
	}
	if hasMore {
		fmt.Fprintln(a.Out, "  "+theme.Dim.Render("… more available (use --all or --cursor)"))
	}
}

func (a *App) printRequest(r *accessclient.Request, showApprove bool) {
	fmt.Fprintln(a.Out, theme.Heading.Render("Access Request"))
	fmt.Fprintln(a.Out, "  "+theme.Field("id", r.ID))
	fmt.Fprintln(a.Out, "  "+theme.Dim.Render(fmt.Sprintf("%-9s ", "status:"))+statusStyle(r.Status))
	if r.Reason != "" {
		fmt.Fprintln(a.Out, "  "+theme.Field("reason", r.Reason))
	}
	for i := range r.Items {
		it := &r.Items[i]
		fmt.Fprintln(a.Out, "  "+theme.Dim.Render(itemSummary(it))+"  "+statusStyle(it.Status))
		// A denied item carries the reason it couldn't be granted (e.g. "No
		// toolkit serves API …; provision and bind a credential for it first").
		// Surface it so the agent/operator learns what to fix; JSON output
		// already includes decision_reason.
		if it.DecisionReason != "" {
			fmt.Fprintln(a.Out, "    "+theme.Warn.Render(it.DecisionReason))
		}
	}
	if showApprove && r.Status == accessclient.StatusPending && r.ApproveURL != "" {
		fmt.Fprintln(a.Out, "\n  "+theme.Info.Render("Share this with your operator to approve:"))
		fmt.Fprintln(a.Out, "  "+theme.Command.Render(r.ApproveURL))
	}
}

func itemSummary(it *accessclient.ItemResponse) string {
	target := it.ResourceID
	if target == "" && it.ResourceReference != nil {
		vendor, _ := it.ResourceReference["vendor"].(string)
		name, _ := it.ResourceReference["name"].(string)
		target = strings.Trim(vendor+"/"+name, "/")
	}
	return fmt.Sprintf("%s:%s %s", it.ResourceType, it.Action, target)
}

func statusStyle(status string) string {
	switch status {
	case accessclient.StatusApproved:
		return theme.Success.Render(status)
	case accessclient.StatusDenied, accessclient.StatusExpired, accessclient.StatusWithdrawn:
		return theme.Warn.Render(status)
	default:
		return theme.Accent.Render(status)
	}
}
