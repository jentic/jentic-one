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
			// connect: start a connect session for a registry vendor (theme-7
			// Phase 1b). The vendor key is the positional argument;
			// --scopes/--reason shape the ask the human approver reviews.
			Command: "connect",
			Params:  control.IntegrationsConnectRequest{},
			Bind: map[string]string{
				"vendor":           PositionalArg,
				"requested_scopes": "scopes",
				"reason":           "reason",
			},
			NotExposed: map[string]string{
				"agent_id":                   "never sent: an agent caller IS the agent (identity injected server-side; an override is refused 403); connecting FOR an agent is a dashboard action",
				"preferred_flow":             "the deployment's registry default flow is the right answer on this surface; flow selection is reachable via `jentic api IntegrationsConnect`",
				"requested_permission_rules": "a nested rule list has no sane flag shape; the approver sets rules on the approve page, and a pre-filled ask is reachable via `jentic api IntegrationsConnect`",
			},
		},
		{
			// connect --wait poll leg: the poll_token capability is threaded
			// from the create response in memory, never a user flag.
			Command: "connect",
			Params:  control.PollConnectSessionStatusParams{},
			Bind:    map[string]string{},
			NotExposed: map[string]string{
				"poll_token": "internal poll capability from the create response, threaded by --wait; reachable via `jentic api PollConnectSessionStatus`",
			},
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
		{
			// catalog list: browse/filter catalog entries. --registered/
			// --unregistered/--limit drive the query; q + outdated_only +
			// include_snoozed are reached by the sibling `catalog search` (positional
			// query) and `catalog outdated` (--include-snoozed) commands, and cursor
			// is internal keyset pagination the --all loop threads (ARCH-21 A4,
			// migrated off internal/catalogclient onto the generated SDK).
			Command: "catalog list",
			Params:  control.ListCatalogParams{},
			Bind: map[string]string{
				"registered_only":   "registered",
				"unregistered_only": "unregistered",
				"limit":             "limit",
			},
			NotExposed: map[string]string{
				"q":               "keyword search is the sibling `catalog search <query>` positional; reachable via `jentic api ListCatalog`",
				"outdated_only":   "set by the sibling `catalog outdated` command, not a `catalog list` flag",
				"include_snoozed": "exposed as --include-snoozed on the sibling `catalog outdated` command",
				"cursor":          "internal keyset cursor threaded by the --all pagination loop, not a user flag",
			},
		},
		{
			// catalog show: preview an entry's operations. --tag/--limit page the
			// preview; offset is the internal preview cursor and q is unused by the
			// show path (full operation search is a `jentic api` concern).
			Command: "catalog show",
			Params:  control.PreviewCatalogOperationsParams{},
			Bind: map[string]string{
				"tag":   "tag",
				"limit": "limit",
			},
			NotExposed: map[string]string{
				"offset": "internal preview offset (show fetches from 0); reachable via `jentic api PreviewCatalogOperations`",
				"q":      "operation-level keyword filter is a `jentic api PreviewCatalogOperations` concern, not a show flag",
			},
		},
		{
			// apis list: page the local registry. --vendor/--limit drive the query;
			// cursor is the internal keyset cursor the --all loop threads (ARCH-21
			// A5, migrated off internal/apiclient onto the generated SDK).
			Command: "apis list",
			Params:  control.ListApisParams{},
			Bind: map[string]string{
				"vendor": "vendor",
				"limit":  "limit",
			},
			NotExposed: map[string]string{
				"cursor": "internal keyset cursor threaded by the --all pagination loop, not a user flag",
			},
		},
		{
			// apis revisions: page an API's revisions. --state (repeatable)/--limit
			// drive the query; cursor is the internal --all pagination cursor.
			Command: "apis revisions",
			Params:  control.ListApiRevisionsParams{},
			Bind: map[string]string{
				"state": "state",
				"limit": "limit",
			},
			NotExposed: map[string]string{
				"cursor": "internal keyset cursor threaded by the --all pagination loop, not a user flag",
			},
		},
		{
			// apis operations (current revision): --limit pages; cursor is threaded
			// by --all. --revision selects the by-revision params variant below.
			Command: "apis operations",
			Params:  control.ListApiOperationsParams{},
			Bind: map[string]string{
				"limit": "limit",
			},
			NotExposed: map[string]string{
				"cursor": "internal keyset cursor threaded by the --all pagination loop, not a user flag",
			},
		},
		{
			// apis operations (--revision path): same flag surface, different
			// generated params struct (the by-revision route).
			Command: "apis operations",
			Params:  control.ListApiRevisionOperationsParams{},
			Bind: map[string]string{
				"limit": "limit",
			},
			NotExposed: map[string]string{
				"cursor": "internal keyset cursor threaded by the --all pagination loop, not a user flag",
			},
		},
		{
			// apis spec (current revision): only carries an `overlays` toggle, which
			// the CLI does not surface (spec download is the plain document; overlay
			// composition is reachable via `jentic api GetApiSpec`).
			Command: "apis spec",
			Params:  control.GetApiSpecParams{},
			Bind:    map[string]string{},
			NotExposed: map[string]string{
				"overlays": "overlay composition is a `jentic api GetApiSpec` concern; spec download serves the plain document",
			},
		},
		{
			// apis spec (--revision path): same overlays-only params for the
			// by-revision route.
			Command: "apis spec",
			Params:  control.GetApiRevisionSpecParams{},
			Bind:    map[string]string{},
			NotExposed: map[string]string{
				"overlays": "overlay composition is a `jentic api GetApiRevisionSpec` concern; spec download serves the plain document",
			},
		},
		{
			// inspect: the target operation is the positional arg (sent as id= for a
			// METHOD/URL pair, else operation_id=); --revision pins a revision. The
			// `detail` enum (full/summary) is not exposed — inspect always fetches
			// full detail (reachable via `jentic api InspectOperation`).
			Command: "inspect",
			Params:  control.InspectOperationParams{},
			Bind: map[string]string{
				"id":           PositionalArg,
				"operation_id": PositionalArg,
				"revision_id":  "revision",
			},
			NotExposed: map[string]string{
				"detail": "inspect always fetches full detail; the summary/full toggle is reachable via `jentic api InspectOperation`",
			},
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
		{
			// ARCH-21 Part B (B7): `jentic identity claim <id> --token <t>` builds
			// the :claim request body. The agent id is the path parameter (not part
			// of this struct); token is the single-use claim capability.
			Command: "identity claim",
			Params:  control.ClaimRequest{},
			Bind: map[string]string{
				"token": "token",
			},
		},
	}
}
