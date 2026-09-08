package api

import (
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"strings"
	"time"

	"github.com/jentic/jentic-one/cli/client/generated/control"
)

var errAccessTargetRequired = errors.New("specify what to request: --toolkit <vendor/name>, --toolkit-id <tk_…>, --scope <scope>, or --provision <vendor/name> (repeat and combine to compose one request)")

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
