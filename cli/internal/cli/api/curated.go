package api

import (
	"github.com/jentic/jentic-one/cli/client/generated/control"
)

// PositionalArg is the CuratedBinding.Bind value for a spec field satisfied by
// a positional argument rather than a flag.
const PositionalArg = "<arg>"

// CuratedBinding declares, for ONE curated command, how the exported fields of
// its generated request params/body struct surface on the CLI. It is the
// registry Test1G_SpecFlagCoverageParity (impl/0.0 §1G) reflects over: every
// json field of Params must appear in exactly one of Bind or NotExposed, every
// Bind flag must exist on the command, and neither map may name a field the
// struct no longer has. The payoff: `make generate-api` after a spec addition
// makes 1G fail until a human classifies the new field — bind it or record why
// not — so a curated command can never silently lag its spec (GEN-1).
type CuratedBinding struct {
	// Command is the space-separated path under the `jentic` root, e.g.
	// "history export".
	Command string
	// Params is the zero value of the generated request params/body struct the
	// command constructs.
	Params any
	// Bind maps a json field name to the flag that populates it (or
	// PositionalArg when a positional argument does).
	Bind map[string]string
	// NotExposed maps a json field name to the one-line reviewed reason it has
	// no flag. `jentic api <op>` still reaches every one of these.
	NotExposed map[string]string
}

// CuratedBindings returns the registry of curated commands that construct
// generated request structs. Commands living entirely on internal/apiclient
// (search, apis list/show, execute) have no generated struct to drift from and
// are not listed; they migrate in here as they move onto the generated SDK.
func CuratedBindings() []CuratedBinding {
	return []CuratedBinding{
		{
			Command: "history export",
			Params:  control.ListExecutionsParams{},
			Bind: map[string]string{
				"trace_id": "trace",
				"from":     "from",
				"to":       "to",
				"api":      "api",
				"cursor":   "cursor",
				"limit":    "limit",
			},
			NotExposed: map[string]string{
				"toolkit_id": "niche filter; reachable via `jentic api ListExecutions`",
				"status":     "--include-failures covers the success/failure split; full status filtering via `jentic api`",
				"actor_id":   "an agent's history is already scoped to itself; cross-actor queries are an operator task",
				"origin":     "niche filter; reachable via `jentic api ListExecutions`",
			},
		},
		{
			Command: "events watch",
			Params:  control.StreamEventsParams{},
			Bind: map[string]string{
				"trace_id":      "trace",
				"event_type":    "type",
				"Last-Event-ID": "last-event-id",
			},
			NotExposed: map[string]string{
				"since":           "resume is by event id (--last-event-id), not wall clock; time windows via `jentic api`",
				"severity":        "agents filter client-side on the NDJSON stream; reachable via `jentic api`",
				"requires_action": "operator-console concept; reachable via `jentic api`",
				"actor_id":        "an agent's stream is already scoped to itself",
				"actor_type":      "an agent's stream is already scoped to itself",
			},
		},
		{
			Command: "credentials list",
			Params:  control.ListCredentialsParams{},
			Bind: map[string]string{
				"vendor": "vendor",
				"cursor": "cursor",
				"limit":  "limit",
			},
			NotExposed: map[string]string{},
		},
		{
			// search: the query is the positional arg (also settable via -q);
			// --api/--limit/--cursor drive the rest of the body (ARCH-21 A2,
			// migrated off internal/searchclient onto the generated SDK).
			Command: "search",
			Params:  control.SearchRequest{},
			Bind: map[string]string{
				"query":  PositionalArg,
				"apis":   "api",
				"limit":  "limit",
				"cursor": "cursor",
			},
			NotExposed: map[string]string{
				"revision_pins": "pin-to-revision is an operator/reproducibility concern; reachable via `jentic api SearchOperations`",
			},
		},
		{
			Command: "apis import",
			Params:  control.ApiImportRequest{},
			Bind: map[string]string{
				// The single source (file path or URL) is the positional
				// argument; --vendor/--name/--version override its metadata.
				"sources": PositionalArg,
			},
			NotExposed: map[string]string{},
		},
		// GEN-20: the request-body UNION MEMBERS `apis import` actually builds.
		// Test1G reflects over ApiImportRequest above, but the wire payload is one
		// of these union members — registering them here means a new optional field
		// on either (like submitted_by, which was silently unwired) fails 1G until a
		// human binds it or records why not. Both members share the same CLI flag
		// surface; `type` is the discriminator the code sets, not a user flag.
		{
			Command: "apis import",
			Params:  control.ApiSourceUrl{},
			Bind: map[string]string{
				"url":          PositionalArg,
				"vendor":       "vendor",
				"api_name":     "name",
				"version":      "version",
				"submitted_by": "submitted-by",
			},
			NotExposed: map[string]string{
				"type": "union discriminator; set to \"url\" by buildImportSource, not a user flag",
			},
		},
		{
			Command: "apis import",
			Params:  control.ApiSourceInline{},
			Bind: map[string]string{
				// content+filename are derived from reading the positional file
				// argument, so the positional arg satisfies both.
				"content":      PositionalArg,
				"filename":     PositionalArg,
				"vendor":       "vendor",
				"api_name":     "name",
				"version":      "version",
				"submitted_by": "submitted-by",
			},
			NotExposed: map[string]string{
				"type": "union discriminator; set to \"inline\" by buildImportSource, not a user flag",
			},
		},
	}
}
