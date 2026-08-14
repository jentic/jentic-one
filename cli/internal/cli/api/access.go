package api

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"regexp"
	"strings"
	"time"

	"github.com/jentic/jentic-one/cli/client/auth"
	"github.com/jentic/jentic-one/cli/client/generated/control"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

// Access-request lifecycle status values. The generated SDK models item/request
// status as a plain string, so the CLI keeps its own named constants (moved off
// the deleted internal/accessclient in ARCH-21 A3).
const (
	statusPending           = "pending"
	statusApproved          = "approved"
	statusPartiallyApproved = "partially_approved"
	statusDenied            = "denied"
	statusWithdrawn         = "withdrawn"
	statusExpired           = "expired"
)

// requestIsTerminal reports whether an access request has left the pending
// state (replaces the deleted accessclient.Request.IsTerminal()).
func requestIsTerminal(r *control.AccessRequestResponse) bool {
	return r.Status != statusPending
}

// staleScopes returns the scopes the agent has been granted that the presented
// token does not yet carry — grants that landed after the token was minted and
// won't take effect until it is refreshed (`jentic access refresh`, issue #673).
// A nil token-scope list means the server did not report token scopes at all
// (staleness is then unknowable), so we report none rather than nagging; an
// explicitly empty list is honored. Replaces accessclient.Me.StaleScopes().
func staleScopes(scopes, tokenScopes []string) []string {
	if tokenScopes == nil {
		return nil
	}
	inToken := make(map[string]struct{}, len(tokenScopes))
	for _, s := range tokenScopes {
		inToken[s] = struct{}{}
	}
	var stale []string
	for _, s := range scopes {
		if _, ok := inToken[s]; !ok {
			stale = append(stale, s)
		}
	}
	return stale
}

var (
	errAccessTargetRequired = errors.New("specify what to request: --toolkit <vendor/name>, --toolkit-id <tk_…>, --scope <scope>, or --provision <vendor/name> (repeat and combine to compose one request)")
	errAccessWaitTimeout    = errors.New("timed out waiting for a decision")
)

// newAccessCmd assembles the `jentic access` group: an agent's self-service
// surface for the access it is missing. It can see what it currently has
// (whoami), ask for more (request), and watch/withdraw those asks (list,
// status, withdraw). Granting is a human action and lives in the dashboard.
func newAccessCmd(app *app) *cobra.Command {
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

func newAccessWhoamiCmd(app *app) *cobra.Command {
	var jsonFlag bool
	cmd := &cobra.Command{
		Use:   "whoami",
		Short: "Show your agent identity, scopes, and toolkit bindings",
		Long: "whoami answers \"what can I do right now?\" — your agent id, status, granted\n" +
			"scopes, and the toolkits you are bound to. An empty bindings list means you\n" +
			"cannot execute against any API yet; use `jentic access request` to ask.",
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
			return app.accessRequestE(cmd, opts)
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
func (o *accessRequestOptions) compose() ([]control.AccessRequestItemRequest, error) {
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

	var items []control.AccessRequestItemRequest
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
		items = append(items, control.AccessRequestItemRequest{
			ResourceType: control.AccessRequestItemRequestResourceTypeToolkit, Action: control.Bind, ResourceReference: &ref,
		})
	}
	for _, id := range toolkitIDs {
		items = append(items, control.AccessRequestItemRequest{
			ResourceType: control.AccessRequestItemRequestResourceTypeToolkit, Action: control.Bind, ResourceId: ptr(id),
		})
	}
	for _, s := range scopes {
		items = append(items, control.AccessRequestItemRequest{
			ResourceType: control.AccessRequestItemRequestResourceTypeScope, Action: control.Grant, ResourceId: ptr(s),
		})
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
func buildProvisionPlan(provision, auth, rulesJSON string) ([]control.AccessRequestItemRequest, error) {
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
	items := make([]control.AccessRequestItemRequest, 0, 4)
	// Step 1: create a toolkit that will serve this API.
	items = append(items, control.AccessRequestItemRequest{
		ResourceType: control.AccessRequestItemRequestResourceTypeToolkit, Action: control.Create, ResourceReference: &ref,
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
	items = append(items, control.AccessRequestItemRequest{
		ResourceType: control.AccessRequestItemRequestResourceTypeCredential, Action: control.Provision, ResourceReference: &provRef,
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
	items = append(items, control.AccessRequestItemRequest{
		ResourceType: control.AccessRequestItemRequestResourceTypeCredential, Action: control.Bind, ResourceReference: &ref, Rules: rules,
	})
	// Step 4: bind the agent to the toolkit, named by the same API reference.
	items = append(items, control.AccessRequestItemRequest{
		ResourceType: control.AccessRequestItemRequestResourceTypeToolkit, Action: control.Bind, ResourceReference: &ref,
	})
	return items, nil
}

// parseProposedRules decodes the agent's proposed permission rules from a JSON
// array (--rules-json). Empty input yields no rules (the server substitutes a
// read-only default on the credential:bind item). Returns nil (not an empty
// pointer) when absent, so the item's `rules` stays omitted on the wire.
func parseProposedRules(raw string) (*[]control.JenticOneControlWebSchemasAccessRequestsPermissionRuleSchema, error) {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return nil, nil
	}
	var rules []control.JenticOneControlWebSchemasAccessRequestsPermissionRuleSchema
	if err := json.Unmarshal([]byte(raw), &rules); err != nil {
		return nil, fmt.Errorf("--rules-json must be a JSON array of rules: %w", err)
	}
	return &rules, nil
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

func (a *app) accessWhoamiE(cmd *cobra.Command, jsonFlag bool) error {
	ctx := cmd.Context()
	me, err := a.getMe(ctx)
	if err != nil {
		return err
	}
	if jsonOrPretty(cmd, jsonFlag) {
		return writeJSON(a.Out, me)
	}
	a.printMe(me)
	return nil
}

// getMe fetches the caller's identity via GET /me and returns the AGENT variant.
//
// GET /me returns a discriminated union (MeUser | MeAgent | MeServiceAccount)
// keyed on `type`. The generated AsMeAgent() does NOT validate the discriminator
// — it would happily decode a user/service-account body into an agent-shaped
// value with empty bindings, which reads as an approved agent bound to nothing.
// So we probe the raw body's `type` first and reject a non-agent token, matching
// the guard the deleted accessclient.Me() enforced.
func (a *app) getMe(ctx context.Context) (*control.MeAgent, error) {
	client, err := a.controlClient(ctx)
	if err != nil {
		return nil, err
	}
	resp, err := client.GetMeWithResponse(ctx)
	if err := apiErrorFor(resp, err); err != nil {
		return nil, err
	}
	// Decode straight from the raw body rather than resp.JSON200: /me is a
	// discriminated union and we need the `type` discriminator to reject a
	// non-agent token (AsMeAgent does not validate it — it would decode a
	// user/service-account into an empty-bindings agent). Reading resp.Body also
	// avoids depending on the response Content-Type (the generated typed field
	// is only populated for an application/json content type).
	var probe struct {
		Type string `json:"type"`
	}
	if err := json.Unmarshal(resp.Body, &probe); err != nil {
		return nil, fmt.Errorf("decode /me response: %w", err)
	}
	if probe.Type != "" && probe.Type != "agent" {
		return nil, fmt.Errorf("this token belongs to a %q, not an agent; agent commands require an agent token", probe.Type)
	}
	var agent control.MeAgent
	if err := json.Unmarshal(resp.Body, &agent); err != nil {
		return nil, fmt.Errorf("decode /me response: %w", err)
	}
	return &agent, nil
}

func (a *app) accessRequestE(cmd *cobra.Command, opts *accessRequestOptions) error {
	items, err := opts.compose()
	if err != nil {
		return err
	}

	ctx := cmd.Context()
	st, err := a.requireState(ctx)
	if err != nil {
		return err
	}
	client, err := a.controlClient(ctx)
	if err != nil {
		return err
	}

	fileResp, err := client.FileAccessRequestWithResponse(ctx, control.AccessRequestFileRequest{
		Reason: strEmptyToNil(opts.reason),
		Items:  items,
	})
	if err != nil {
		return err
	}
	var req *control.AccessRequestResponse
	switch {
	case fileResp.JSON202 != nil:
		req = fileResp.JSON202
	case fileResp.ApplicationproblemJSON409 != nil:
		dup := fileResp.ApplicationproblemJSON409
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
				dup.ExistingRequestId, dup.ExistingRequestId, dup.ExistingRequestId)
		}
		fmt.Fprintln(a.Out, theme.Warnf("A pending request already exists (%s); attaching to it.", dup.ExistingRequestId))
		req, err = a.getAccessRequest(ctx, client, dup.ExistingRequestId)
		if err != nil {
			return err
		}
	default:
		// A non-2xx, non-409 response: surface it through the shared adapter.
		if aerr := apiErrorFor(fileResp, nil); aerr != nil {
			return aerr
		}
		return fmt.Errorf("unexpected backend response (status %d)", fileResp.StatusCode())
	}

	timedOut := false
	if opts.wait && !requestIsTerminal(req) {
		waited, waitErr := a.pollAccessRequest(ctx, client, req.Id, opts.timeout)
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
		absolutizeApproveURL(st.BaseURL, req)
		if err := writeJSON(a.Out, req); err != nil {
			return err
		}
	} else {
		absolutizeApproveURL(st.BaseURL, req)
		a.printRequest(req, true)
	}

	switch {
	case timedOut:
		// Codes here mirror the exit taxonomy the help text documents (AGT-6):
		// the decorator renders the envelope/styled line from the CodedError, so
		// envelope code and exit code come from the same table.
		return &ux.CodedError{
			Code:       ux.CodeTimeoutPending,
			Msg:        fmt.Sprintf("still pending after %s", opts.timeout),
			Actionable: "jentic access status " + req.Id,
		}
	case req.Status == statusPartiallyApproved:
		// A newly-granted scope only takes effect once re-minted into the token;
		// do it for the agent so it needn't run a separate `access refresh`.
		a.refreshIfScopeGranted(cmd, req)
	}
	if err := terminalAccessError(req); err != nil {
		return err
	}
	// Fully approved. A newly-granted scope bakes into the token at mint time, so
	// re-mint now if the request granted one — the agent can then execute
	// immediately without a separate `access refresh`. A binding-only plan
	// (toolkit/credential binds, no scope) needs no re-mint: bindings are live
	// server-side, so this is a no-op in that case.
	a.refreshIfScopeGranted(cmd, req)
	return nil
}

// terminalAccessError maps a decided access request's status to the coded error
// that drives the exit taxonomy documented in the `access request` help (AGT-6):
//
//	denied / expired / withdrawn -> BROKER_DENIED  (exit 2) — the agent still
//	    cannot do what it asked, so this must never look like success.
//	partially_approved           -> PARTIAL_APPROVAL (exit 4) — some items were
//	    granted but at least one was not, so a scripted agent must not proceed as
//	    if the capability is fully available; the printed items show what remains.
//	anything else (approved, …)  -> nil (exit 0).
//
// Pure and status-only so the exit-code contract can be tested without a live
// backend (QA-20). The caller handles timeout (TIMEOUT_PENDING) and the re-mint
// side effect before calling this.
func terminalAccessError(req *control.AccessRequestResponse) error {
	switch req.Status {
	case statusDenied:
		return &ux.CodedError{
			Code:       ux.CodeBrokerDenied,
			Msg:        fmt.Sprintf("access request %s was denied", req.Id),
			Actionable: "jentic access status " + req.Id,
		}
	case statusExpired, statusWithdrawn:
		return &ux.CodedError{
			Code:       ux.CodeBrokerDenied,
			Msg:        fmt.Sprintf("request %s is %s, not approved; nothing was granted", req.Id, req.Status),
			Actionable: "jentic access status " + req.Id,
		}
	case statusPartiallyApproved:
		return &ux.CodedError{
			Code:       ux.CodePartialApproval,
			Msg:        "partially approved — not all requested items were granted",
			Actionable: "jentic access status " + req.Id,
		}
	}
	return nil
}

// refreshIfScopeGranted re-mints the agent's token when (and only when) the
// decided request granted a new scope — the one thing that is baked into the
// token at mint time and so needs a refresh to become usable. Toolkit/credential
// bindings are resolved live by the broker, so a `--provision`/`--toolkit` plan
// needs no re-mint; re-minting anyway would be a wasted round-trip. Best-effort:
// a mint failure is non-fatal (the agent can still run `jentic access refresh`),
// and static credentials (injected token, API key) are skipped.
func (a *app) refreshIfScopeGranted(cmd *cobra.Command, req *control.AccessRequestResponse) {
	if !requestGrantedScope(req) {
		return
	}
	st := clictx.ActiveV2(cmd.Context())
	if st == nil {
		return
	}
	creds := credsFromState(st)
	// Static credentials (injected token, jak_* API key) have no mintable
	// token — nothing to refresh.
	if creds.InjectedBearerToken != "" {
		return
	}
	if key, err := auth.ReadAPIKey(creds.IdentityRef()); err == nil && key != "" {
		return
	}
	if _, err := auth.RefreshBearerToken(creds); err != nil {
		fmt.Fprintln(a.Err, theme.Dimf("granted scope not yet on your token; run `jentic access refresh` to pick it up"))
	}
}

// requestGrantedScope reports whether a decided request approved a scope:grant
// item — the only grant that bakes into the token and so needs a re-mint.
// Toolkit/credential binds are resolved live by the broker, so a binding-only
// plan returns false (no re-mint needed).
func requestGrantedScope(req *control.AccessRequestResponse) bool {
	for _, it := range req.Items {
		if it.ResourceType == "scope" && it.Action == "grant" && it.Status == "approved" {
			return true
		}
	}
	return false
}

func (a *app) accessListE(cmd *cobra.Command, opts *accessListOptions) error {
	ctx := cmd.Context()
	client, err := a.controlClient(ctx)
	if err != nil {
		return err
	}

	const maxPages = 1000
	var all []control.AccessRequestResponse
	var hasMore bool
	var nextCursor string
	cursor := opts.cursor

	for page := 0; ; page++ {
		params := &control.ListAccessRequestsParams{}
		if opts.status != "" {
			params.Status = ptr(opts.status)
		}
		if cursor != "" {
			params.Cursor = ptr(cursor)
		}
		if opts.limit > 0 {
			params.Limit = ptr(opts.limit)
		}
		resp, listErr := client.ListAccessRequestsWithResponse(ctx, params)
		if err := apiErrorFor(resp, listErr); err != nil {
			return err
		}
		if resp.JSON200 == nil {
			return fmt.Errorf("unexpected backend response (status %d)", resp.StatusCode())
		}
		res := resp.JSON200
		all = append(all, res.Data...)
		hasMore = res.HasMore
		nextCursor = deref(res.NextCursor)
		if !opts.all || !res.HasMore || nextCursor == "" {
			break
		}
		if page+1 >= maxPages {
			break
		}
		cursor = nextCursor
	}

	if jsonOrPretty(cmd, opts.json) {
		return writeList(a.Out, all, nextCursor, nil)
	}
	a.printRequestList(all, hasMore)
	return nil
}

func (a *app) accessStatusE(cmd *cobra.Command, id string, jsonFlag bool) error {
	ctx := cmd.Context()
	st, err := a.requireState(ctx)
	if err != nil {
		return err
	}
	client, err := a.controlClient(ctx)
	if err != nil {
		return err
	}
	req, err := a.getAccessRequest(ctx, client, id)
	if err != nil {
		return err
	}
	absolutizeApproveURL(st.BaseURL, req)
	if jsonOrPretty(cmd, jsonFlag) {
		return writeJSON(a.Out, req)
	}
	a.printRequest(req, true)
	return nil
}

func (a *app) accessWithdrawE(cmd *cobra.Command, id string, jsonFlag bool) error {
	ctx := cmd.Context()
	client, err := a.controlClient(ctx)
	if err != nil {
		return err
	}
	resp, err := client.WithdrawAccessRequestWithResponse(ctx, id)
	if err := apiErrorFor(resp, err); err != nil {
		return err
	}
	if resp.JSON200 == nil {
		return fmt.Errorf("unexpected backend response (status %d)", resp.StatusCode())
	}
	req := resp.JSON200
	if jsonOrPretty(cmd, jsonFlag) {
		return writeJSON(a.Out, req)
	}
	fmt.Fprintln(a.Out, theme.Successf("Withdrew access request %s.", req.Id))
	a.printRequest(req, false)
	return nil
}

// getAccessRequest fetches a single access request by id via GET
// /access-requests/{id}, mapping non-2xx through the shared adapter.
func (a *app) getAccessRequest(ctx context.Context, client *control.ClientWithResponses, id string) (*control.AccessRequestResponse, error) {
	resp, err := client.GetAccessRequestWithResponse(ctx, id)
	if err := apiErrorFor(resp, err); err != nil {
		return nil, err
	}
	if resp.JSON200 == nil {
		return nil, fmt.Errorf("unexpected backend response (status %d)", resp.StatusCode())
	}
	return resp.JSON200, nil
}

func (a *app) accessRefreshE(cmd *cobra.Command, jsonFlag bool) error {
	st, err := a.requireState(cmd.Context())
	if err != nil {
		return err
	}
	return a.accessRefreshContextE(cmd, st, jsonFlag)
}

// accessRefreshContextE is the context arm of `jentic access refresh`: force
// a fresh assertion exchange for the active (identity, environment) so the new
// token carries the current scope grants, then confirm with /me. Same
// fresh-mint-not-refresh-token semantics as the legacy arm (issue #673).
func (a *app) accessRefreshContextE(cmd *cobra.Command, st *clictx.ActiveState, jsonFlag bool) error {
	creds := credsFromState(st)
	if creds.InjectedBearerToken != "" {
		return errors.New("this session uses an injected bearer token ($JENTIC_BEARER_TOKEN), which the CLI cannot re-mint; " +
			"obtain a fresh token from your orchestrator")
	}
	if key, err := auth.ReadAPIKey(creds.IdentityRef()); err == nil && key != "" {
		return fmt.Errorf("identity %q authenticates with a static API key, which has no token to refresh; "+
			"its scopes change only when an admin updates the key", st.IdentityName)
	}
	if st.BaseURL == "" {
		return &ux.CodedError{
			Code:       ux.CodeResolveFailed,
			Msg:        fmt.Sprintf("environment %q has no base_url", st.EnvironmentName),
			Actionable: "Set it with `jentic env add` / edit the environment.",
		}
	}
	// Force a fresh assertion exchange so the re-minted token carries the
	// current scope grants (invalidate + re-mint); the SDK client getMe uses
	// then resolves that freshly-cached token. Issue #673.
	if _, err := auth.RefreshBearerToken(creds); err != nil {
		return asCoded(err)
	}
	me, err := a.getMe(cmd.Context())
	if err != nil {
		return err
	}
	if jsonOrPretty(cmd, jsonFlag) {
		return writeJSON(a.Out, me)
	}
	fmt.Fprintln(a.Out, theme.Successf("Refreshed token for %s.", me.Id))
	a.printMe(me)
	return nil
}

// pollAccessRequest loops Get until the request leaves the pending state, the
// timeout elapses, or the context is cancelled. It reuses the register poll
// cadence so the wait backs off the same way.
func (a *app) pollAccessRequest(ctx context.Context, client *control.ClientWithResponses, id string, timeout time.Duration) (*control.AccessRequestResponse, error) {
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
		req, err := a.getAccessRequest(ctx, client, id)
		if err != nil {
			return nil, err
		}
		if requestIsTerminal(req) {
			return req, nil
		}
	}
}

func (a *app) printMe(me *control.MeAgent) {
	fmt.Fprintln(a.Out, theme.Heading.Render("Identity"))
	fmt.Fprintln(a.Out, "  "+theme.Field("agent", me.Id))
	if me.Name != "" {
		fmt.Fprintln(a.Out, "  "+theme.Field("name", me.Name))
	}
	fmt.Fprintln(a.Out, "  "+theme.Field("status", me.Status))
	scopes := "none"
	if len(me.Scopes) > 0 {
		scopes = strings.Join(me.Scopes, ", ")
	}
	fmt.Fprintln(a.Out, "  "+theme.Field("scopes", scopes))
	if stale := staleScopes(me.Scopes, me.TokenScopes); len(stale) > 0 {
		fmt.Fprintln(a.Out, "  "+theme.Warnf("granted but not yet on your token: %s", strings.Join(stale, ", ")))
		fmt.Fprintln(a.Out, "  "+theme.Dim.Render("run `jentic access refresh` to pick them up"))
	}

	fmt.Fprintln(a.Out, theme.Heading.Render("Toolkit bindings"))
	if len(me.ToolkitBindings) == 0 {
		fmt.Fprintln(a.Out, "  "+theme.Dim.Render("none — you cannot execute yet; run `jentic access request --toolkit <vendor/name>`"))
	} else {
		for _, b := range me.ToolkitBindings {
			if name := deref(b.Name); name != "" {
				fmt.Fprintln(a.Out, "  "+theme.Command.Render(name)+"  "+theme.Dim.Render(b.ToolkitId))
			} else {
				fmt.Fprintln(a.Out, "  "+theme.Command.Render(b.ToolkitId))
			}
		}
	}

	// whoami answers "what can I do?" for the control-plane (scopes, toolkits); the
	// filesystem side of that answer lives in `context view`, which maps every
	// directory the agent can reach. Point at it so an agent has one place to learn
	// its full access surface.
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Dim.Render("To see which directories you can access, run `jentic context view`."))
}

func (a *app) printRequestList(reqs []control.AccessRequestResponse, hasMore bool) {
	fmt.Fprintln(a.Out, theme.Heading.Render("Access Requests"))
	if len(reqs) == 0 {
		fmt.Fprintln(a.Out, "  "+theme.Dim.Render("no requests"))
		return
	}
	for i := range reqs {
		r := &reqs[i]
		fmt.Fprintln(a.Out, "  "+theme.Command.Render(r.Id)+"  "+statusStyle(r.Status))
		for j := range r.Items {
			fmt.Fprintln(a.Out, "    "+theme.Dim.Render(itemSummary(&r.Items[j])))
		}
	}
	if hasMore {
		fmt.Fprintln(a.Out, "  "+theme.Dim.Render("… more available (use --all or --cursor)"))
	}
}

func (a *app) printRequest(r *control.AccessRequestResponse, showApprove bool) {
	fmt.Fprintln(a.Out, theme.Heading.Render("Access Request"))
	fmt.Fprintln(a.Out, "  "+theme.Field("id", r.Id))
	fmt.Fprintln(a.Out, "  "+theme.Dim.Render(fmt.Sprintf("%-9s ", "status:"))+statusStyle(r.Status))
	if reason := deref(r.Reason); reason != "" {
		fmt.Fprintln(a.Out, "  "+theme.Field("reason", reason))
	}
	for i := range r.Items {
		it := &r.Items[i]
		fmt.Fprintln(a.Out, "  "+theme.Dim.Render(itemSummary(it))+"  "+statusStyle(it.Status))
		// A denied item carries the reason it couldn't be granted (e.g. "No
		// toolkit serves API …; provision and bind a credential for it first").
		// Surface it so the agent/operator learns what to fix; JSON output
		// already includes decision_reason.
		if reason := deref(it.DecisionReason); reason != "" {
			fmt.Fprintln(a.Out, "    "+theme.Warn.Render(reason))
		}
	}
	if showApprove && r.Status == statusPending && r.ApproveUrl != "" {
		fmt.Fprintln(a.Out, "\n  "+theme.Info.Render("Share this with your operator to approve:"))
		fmt.Fprintln(a.Out, "  "+theme.Command.Render(r.ApproveUrl))
	}
}

func itemSummary(it *control.AccessRequestItemResponse) string {
	target := deref(it.ResourceId)
	if target == "" && it.ResourceReference != nil {
		ref := *it.ResourceReference
		vendor, _ := ref["vendor"].(string)
		name, _ := ref["name"].(string)
		target = strings.Trim(vendor+"/"+name, "/")
	}
	return fmt.Sprintf("%s:%s %s", it.ResourceType, it.Action, target)
}

// absolutizeApproveURL rewrites a relative approve_url onto the environment's base
// URL so the value the CLI prints (or emits as JSON) is directly openable, rather
// than a path fragment the operator has to guess a host for (impl/5.0 §6b,
// jentic-one#777). An already-absolute URL and an empty value are left untouched.
func absolutizeApproveURL(baseURL string, r *control.AccessRequestResponse) {
	if r == nil || r.ApproveUrl == "" {
		return
	}
	if u, err := url.Parse(r.ApproveUrl); err == nil && u.IsAbs() {
		return
	}
	base, err := url.Parse(strings.TrimRight(baseURL, "/"))
	if err != nil {
		return
	}
	ref, err := url.Parse(r.ApproveUrl)
	if err != nil {
		return
	}
	r.ApproveUrl = base.ResolveReference(ref).String()
}

func statusStyle(status string) string {
	switch status {
	case statusApproved:
		return theme.Success.Render(status)
	case statusDenied, statusExpired, statusWithdrawn:
		return theme.Warn.Render(status)
	default:
		return theme.Accent.Render(status)
	}
}
