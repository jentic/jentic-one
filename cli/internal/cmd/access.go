package cmd

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"strings"
	"time"

	"github.com/jentic/jentic-one/cli/internal/accessclient"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

var (
	errAccessTargetRequired = errors.New("specify what to request: --toolkit <vendor/name>, --toolkit-id <tk_…>, --scope <scope>, or --provision <vendor/name> (repeat and combine to compose one request)")
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
		Short: "File a request for toolkit bindings, scope grants, or provisioning plans",
		Long: "request files one access request for the access you are missing and prints an\n" +
			"approve_url for your human operator. Name a toolkit by the API you found in\n" +
			"search (--toolkit vendor/name), by id (--toolkit-id tk_…), or ask for a scope\n" +
			"(--scope). Use --wait to block until a human decides (or --timeout elapses).\n\n" +
			"When nothing serves an API yet (a fresh import with no toolkit/credential),\n" +
			"use --provision vendor/name to file the whole path to first execution as one\n" +
			"plan: create a toolkit, provision a credential, bind it (with your proposed\n" +
			"--rules-json), and bind yourself. A human fulfils the create/provision steps\n" +
			"in the dashboard (they enter the secret — it never rides in your request) and\n" +
			"approves. Use --auth to declare the credential type you detected from the spec\n" +
			"(bearer, api_key, basic, oauth2, or none for a no-auth API).\n\n" +
			"All target flags repeat and combine, so a job needing several APIs files ONE\n" +
			"composite request the human decides in one sitting: each --provision appends\n" +
			"a provisioning plan, each --toolkit/--toolkit-id/--scope appends a single\n" +
			"item. With more than one --provision, key --auth and --rules-json by the\n" +
			"same vendor/name[/version] passed to --provision; the bare form applies\n" +
			"when there is exactly one.\n\n" +
			"An existing pending request for the same resource is reused when this request\n" +
			"names a single target; a composite aborts instead (drop the already-pending\n" +
			"target or withdraw the older request, then re-file).\n\n" +
			"Exit codes:\n" +
			"  0 — request filed (or, with --wait, fully approved)\n" +
			"  2 — request was denied, expired, or withdrawn (only with --wait)\n" +
			"  3 — still pending when --timeout elapsed (only with --wait)\n" +
			"  4 — partially approved; not all items granted (only with --wait)",
		Example: "  jentic access request --toolkit httpbin.org/httpbin --reason \"smoke test\"\n" +
			"  jentic access request --toolkit-id tk_123 --wait\n" +
			"  jentic access request --scope owner:toolkits:read --json\n" +
			"  jentic access request --provision posthog.com/posthog-api --auth bearer \\\n" +
			"    --rules-json '[{\"effect\":\"allow\",\"methods\":[\"GET\"],\"path\":\".*\"}]' --wait\n" +
			"  jentic access request --provision slack.com/api --auth slack.com/api=bearer \\\n" +
			"    --provision googleapis.com/sheets --auth googleapis.com/sheets=oauth2 \\\n" +
			"    --toolkit github.com/api --scope apis:write \\\n" +
			"    --reason \"release-notes automation\" --wait",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.accessRequestE(cmd, ident, opts)
		},
	}
	cmd.Flags().StringArrayVar(&opts.toolkits, "toolkit", nil, "request a binding to the toolkit serving this API (vendor/name[/version]; repeatable)")
	cmd.Flags().StringArrayVar(&opts.toolkitIDs, "toolkit-id", nil, "request a binding to this toolkit id (tk_…; repeatable)")
	cmd.Flags().StringArrayVar(&opts.scopes, "scope", nil, "request this scope be granted (repeatable)")
	cmd.Flags().StringArrayVar(&opts.provisions, "provision", nil, "file a full provisioning plan to make this API executable (vendor/name[/version]; repeatable)")
	cmd.Flags().StringArrayVar(&opts.auths, "auth", nil, "credential auth type for --provision: bearer, api_key, basic, oauth2, or none (default bearer); key by API when --provision repeats (vendor/name[/version]=<type>)")
	cmd.Flags().StringArrayVar(&opts.rulesJSONs, "rules-json", nil, "proposed permission rules for --provision, as a JSON array; key by API when --provision repeats (vendor/name[/version]=<json>)")
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
	toolkits   []string
	toolkitIDs []string
	scopes     []string
	provisions []string
	auths      []string
	rulesJSONs []string
	reason     string
	wait       bool
	timeout    time.Duration
	json       bool
}

// targetCount is the number of distinct targets the request names — each
// --provision plan counts as one target, as does each --toolkit/--toolkit-id/
// --scope item. It decides composite behavior (e.g. the 409 handling).
func (o *accessRequestOptions) targetCount() int {
	return len(cleanValues(o.provisions)) + len(cleanValues(o.toolkits)) +
		len(cleanValues(o.toolkitIDs)) + len(cleanValues(o.scopes))
}

// compose builds the full item list for the request from every target flag, in
// fulfilment order: provisioning plans first (one 4-item chain per --provision,
// in flag order), then toolkit binds by reference, by id, and scope grants.
// Targets are validated as a set — duplicates and a --toolkit/--provision pair
// naming the same API are rejected, since they would file conflicting or
// redundant intents the approving human then has to untangle.
func (o *accessRequestOptions) compose() ([]accessclient.Item, error) {
	provisions := cleanValues(o.provisions)
	toolkits := cleanValues(o.toolkits)
	toolkitIDs := cleanValues(o.toolkitIDs)
	scopes := cleanValues(o.scopes)

	if len(provisions)+len(toolkits)+len(toolkitIDs)+len(scopes) == 0 {
		return nil, errAccessTargetRequired
	}
	if len(provisions) == 0 && (len(cleanValues(o.auths)) > 0 || len(cleanValues(o.rulesJSONs)) > 0) {
		return nil, errors.New("--auth and --rules-json only apply with --provision")
	}

	// Canonicalize the reference-shaped targets so duplicates are caught
	// regardless of spelling (whitespace) and so keyed --auth/--rules-json
	// values can be matched to their chain.
	provKeys, err := canonicalRefKeys("--provision", provisions)
	if err != nil {
		return nil, err
	}
	toolkitKeys, err := canonicalRefKeys("--toolkit", toolkits)
	if err != nil {
		return nil, err
	}
	for _, k := range toolkitKeys {
		for _, p := range provKeys {
			if k == p {
				return nil, fmt.Errorf("%s is named by both --toolkit and --provision; "+
					"a provisioning plan already ends with the toolkit binding, so drop the --toolkit", k)
			}
		}
	}
	if dup := firstDuplicate(toolkitIDs); dup != "" {
		return nil, fmt.Errorf("--toolkit-id %s given more than once", dup)
	}
	if dup := firstDuplicate(scopes); dup != "" {
		return nil, fmt.Errorf("--scope %s given more than once", dup)
	}

	auths, err := resolveKeyedValues("--auth", o.auths, provKeys)
	if err != nil {
		return nil, err
	}
	rulesJSONs, err := resolveKeyedValues("--rules-json", o.rulesJSONs, provKeys)
	if err != nil {
		return nil, err
	}

	var items []accessclient.Item
	for i, p := range provisions {
		chain, planErr := buildProvisionPlan(p, auths[provKeys[i]], rulesJSONs[provKeys[i]])
		if planErr != nil {
			return nil, planErr
		}
		items = append(items, chain...)
	}
	for _, t := range toolkits {
		ref, refErr := parseToolkitRef(t)
		if refErr != nil {
			return nil, refErr
		}
		items = append(items, accessclient.Item{ResourceType: "toolkit", Action: "bind", ResourceReference: ref})
	}
	for _, id := range toolkitIDs {
		items = append(items, accessclient.Item{ResourceType: "toolkit", Action: "bind", ResourceID: id})
	}
	for _, s := range scopes {
		items = append(items, accessclient.Item{ResourceType: "scope", Action: "grant", ResourceID: s})
	}
	return items, nil
}

// cleanValues trims each value and drops empties, preserving order.
func cleanValues(values []string) []string {
	var out []string
	for _, v := range values {
		if s := strings.TrimSpace(v); s != "" {
			out = append(out, s)
		}
	}
	return out
}

// firstDuplicate returns the first value that appears more than once, or "".
func firstDuplicate(values []string) string {
	seen := make(map[string]bool, len(values))
	for _, v := range values {
		if seen[v] {
			return v
		}
		seen[v] = true
	}
	return ""
}

// canonicalRefKeys parses each vendor/name[/version] value and returns its
// canonical key form, rejecting duplicates within the flag.
func canonicalRefKeys(flag string, values []string) ([]string, error) {
	keys := make([]string, 0, len(values))
	for _, v := range values {
		ref, err := parseToolkitRef(v)
		if err != nil {
			return nil, err
		}
		key := refKey(ref)
		for _, k := range keys {
			if k == key {
				return nil, fmt.Errorf("%s %s given more than once", flag, key)
			}
		}
		keys = append(keys, key)
	}
	return keys, nil
}

// refKey renders a parsed reference back to its canonical vendor/name[/version]
// string, used to match keyed --auth/--rules-json values to their chain and to
// reject duplicates. Vendor/name are slugified exactly like the server does
// (jentic_one.shared.models.api_identity.slugify_api_field), so raw-domain and
// slug spellings of the same API ("httpbin.org" vs "httpbin-org") collide here
// instead of filing as two distinct chains that the server would then merge.
func refKey(ref map[string]any) string {
	vendor, _ := ref["vendor"].(string)
	name, _ := ref["name"].(string)
	key := slugifyAPIField(vendor) + "/" + slugifyAPIField(name)
	if version, ok := ref["version"].(string); ok && version != "" {
		key += "/" + version
	}
	return key
}

var apiSlugRe = regexp.MustCompile(`[^a-z0-9-]+`)

// slugifyAPIField mirrors the server's canonical slug form for API vendor/name
// fields: lowercase, strip, runs of non-[a-z0-9-] become a single hyphen,
// leading/trailing hyphens trimmed.
func slugifyAPIField(value string) string {
	slug := apiSlugRe.ReplaceAllString(strings.ToLower(strings.TrimSpace(value)), "-")
	return strings.Trim(slug, "-")
}

// resolveKeyedValues maps repeatable per-API option values (--auth,
// --rules-json) onto their --provision chains. Each value is either keyed
// ("vendor/name[/version]=<value>") or bare; the bare form is only unambiguous
// with exactly one --provision. Returns a map from canonical provision key to
// the value for that chain (missing keys mean "use the default").
func resolveKeyedValues(flag string, values, provKeys []string) (map[string]string, error) {
	out := make(map[string]string, len(values))
	for _, raw := range cleanValues(values) {
		key, value, keyed, err := splitKeyedValue(flag, raw, provKeys)
		if err != nil {
			return nil, err
		}
		if !keyed {
			if len(provKeys) != 1 {
				return nil, fmt.Errorf("%s %q must be keyed by API when --provision repeats (e.g. %s %s=<value>)",
					flag, raw, flag, firstOr(provKeys, "vendor/name"))
			}
			key, value = provKeys[0], raw
		}
		if _, exists := out[key]; exists {
			return nil, fmt.Errorf("%s given more than once for %s", flag, key)
		}
		out[key] = value
	}
	return out, nil
}

// splitKeyedValue splits "key=value" when the key part is shaped like an API
// reference (vendor/name…). A shaped key that matches no --provision target is
// an error (a typo, or a chain that was never requested); anything not shaped
// like a keyed form — including JSON payloads that happen to contain '=' — is
// treated as a bare value.
func splitKeyedValue(flag, raw string, provKeys []string) (key, value string, keyed bool, err error) {
	// A value that starts like a JSON document is always a bare payload — a
	// rules array can legitimately contain '=' (e.g. inside a path regex) and
	// must not be probed for a key.
	if trimmed := strings.TrimSpace(raw); strings.HasPrefix(trimmed, "[") || strings.HasPrefix(trimmed, "{") {
		return "", "", false, nil
	}
	eq := strings.Index(raw, "=")
	if eq <= 0 {
		return "", "", false, nil
	}
	candidate := strings.TrimSpace(raw[:eq])
	ref, refErr := parseToolkitRef(candidate)
	if refErr != nil {
		return "", "", false, nil //nolint:nilerr // an unparsable key prefix means "bare value", not a failure.
	}
	canonical := refKey(ref)
	for _, k := range provKeys {
		if canonical == k {
			return k, raw[eq+1:], true, nil
		}
	}
	return "", "", false, fmt.Errorf("%s is keyed to %s, which is not among the --provision targets", flag, canonical)
}

func firstOr(values []string, fallback string) string {
	if len(values) > 0 {
		return values[0]
	}
	return fallback
}

// validAuthTypes are the credential auth types --auth accepts. "none" marks a
// no-auth API: the plan's credential:provision item carries security_scheme=
// no_auth and the wizard auto-creates a NO_AUTH credential (no secret prompt).
var validAuthTypes = map[string]bool{
	"bearer": true, "api_key": true, "basic": true, "oauth2": true, "none": true,
}

// buildProvisionPlan builds one full provisioning plan for a --provision
// target: the ordered set of items describing the whole path to first
// execution. The agent files intent (create toolkit, provision a credential,
// bind it with proposed rules, bind the agent); a human fulfils the
// create/provision steps via the dashboard, which writes the resulting ids back
// onto the bind items before approving. Returns the items in fulfilment order.
func buildProvisionPlan(provision, auth, rulesJSON string) ([]accessclient.Item, error) {
	ref, err := parseToolkitRef(provision)
	if err != nil {
		return nil, err
	}

	auth = strings.TrimSpace(auth)
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

	rules, err := parseProposedRules(rulesJSON)
	if err != nil {
		return nil, err
	}

	// The plan is a fixed 4-item chain (toolkit:create, credential:provision,
	// credential:bind, toolkit:bind); preallocate to that capacity.
	items := make([]accessclient.Item, 0, 4)
	// Step 1: create a toolkit that will serve this API.
	items = append(items, accessclient.Item{
		ResourceType: "toolkit", Action: "create", ResourceReference: ref,
	})
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
	// Step 3: bind the (to-be-created) credential to the (to-be-created)
	// toolkit, carrying the agent's proposed first-pass rules. The operator
	// amends the concrete credential/toolkit ids onto this item before approval.
	// The API reference is stamped on so the item names its chain: item order
	// is not guaranteed server-side, and in a composite request with several
	// plans the bare item would be indistinguishable from its siblings (it also
	// keeps pending-dedup from colliding two different plans' bind items). The
	// server ignores the reference for credential:bind — only the amended ids
	// wire the effect.
	items = append(items, accessclient.Item{
		ResourceType: "credential", Action: "bind", ResourceReference: ref, Rules: rules,
	})
	// Step 4: bind the agent to the toolkit, named by the same API reference.
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
	items, err := opts.compose()
	if err != nil {
		return err
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
		// Filing is all-or-nothing, so a 409 on a composite means one of its
		// targets is already pending and NOTHING was filed. Attaching would
		// silently swap the composite for the older, smaller request — the
		// agent would wait on a grant that covers only part of what it asked
		// for. Surface the collision instead so the agent drops the pending
		// target or withdraws the older request and re-files.
		if opts.targetCount() > 1 {
			return fmt.Errorf("nothing was filed: one of the requested targets already has a pending request (%s); "+
				"inspect it with `jentic access status %s`, then either drop that target from this request "+
				"or withdraw the pending one (`jentic access withdraw %s`) and re-file",
				dup.ExistingRequestID, dup.ExistingRequestID, dup.ExistingRequestID)
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
	} else {
		for _, b := range me.ToolkitBindings {
			if b.Name != "" {
				fmt.Fprintln(a.Out, "  "+theme.Command.Render(b.Name)+"  "+theme.Dim.Render(b.ToolkitID))
			} else {
				fmt.Fprintln(a.Out, "  "+theme.Command.Render(b.ToolkitID))
			}
		}
	}

	// whoami answers "what can I do?" for the control-plane (scopes, toolkits); the
	// filesystem side of that answer lives in `profile view`, which maps every
	// directory the agent can reach. Point at it so an agent has one place to learn
	// its full access surface.
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Dim.Render("To see which directories you can access, run `jentic profile view`."))
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
