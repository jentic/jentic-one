package ux

import "github.com/jentic/jentic-one/cli/internal/theme"

// Palette aliases the global theme palette so command code depends only on ux.
type Palette = theme.Palette

// Result is the canonical success payload for state-mutating commands (context
// use, env add, identity add, credential create, identity register, ...). It
// replaces ad-hoc map[string]string literals: those had no compile-time safety
// and drifted on key names and status verbs. Sensitive fields MUST use
// `redact:"true"` so layer 1 scrubs them — never put a raw secret in Fields.
type Result struct {
	// SchemaVersion pins the envelope shape for agents (13 §2/§6). Call sites may
	// leave it empty; marshalRedacted stamps the current version at render time.
	SchemaVersion string `json:"schema_version"`
	// Status is the outcome verb (StatusCreated, StatusSwitched, ...).
	Status string `json:"status"`
	// Resource is the kind of thing acted on ("environment", "context", "identity").
	Resource string `json:"resource,omitempty"`
	// Name is the human/identifier name of the affected resource, when it has one.
	Name string `json:"name,omitempty"`
	// ID is the server- or client-assigned identifier, when one was produced.
	ID string `json:"id,omitempty"`
	// Message is optional human-oriented context (e.g. "awaiting operator approval").
	Message string `json:"message,omitempty"`
	// Fields is the escape hatch for genuinely command-specific data. Prefer a
	// first-class field above; use this only when nothing else fits.
	Fields map[string]any `json:"fields,omitempty"`
}

// Status verbs — the closed set of outcomes a Result may report. Named constants
// (not literals per call site) prevent the "added"/"created"/"switched" drift the
// map[string]string approach allowed. Mirrors 13 §2.
const (
	StatusCreated    = "created"
	StatusAdded      = "added"
	StatusUpdated    = "updated"
	StatusSwitched   = "switched"
	StatusDeleted    = "deleted"
	StatusRegistered = "registered"
	StatusPending    = "pending"
)

// Page is the pagination envelope for list commands (13 §2, impl/3.3). It carries
// the underlying items plus the opaque cursor for the next page. Cursor form only
// — Jentic list APIs are cursor-paginated (data + has_more + next_cursor); there
// is no page-number UX.
type Page struct {
	SchemaVersion string `json:"schema_version"`
	Items         any    `json:"items"`
	// NextToken is the opaque cursor for the next page; empty on the last page.
	NextToken string `json:"next_token,omitempty"`
}

// HasNext reports whether another page is available.
func (p Page) HasNext() bool { return p.NextToken != "" }

// List is the canonical, versioned envelope for data-plane list output (AGT-1/
// AGT-5). Before this, list commands wrote ad-hoc maps: most used
// {data, has_more, next_cursor} but `endpoints` used {endpoints} and NONE
// carried schema_version, so an agent could not (a) detect the envelope version
// or (b) rely on a single collection/pagination key across commands. This struct
// makes every list emit the same three keys plus schema_version; marshalRedacted
// stamps the version like the other envelopes. Data is `any` so each command
// passes its concrete non-nil slice (an empty result serialises as [], not null).
type List struct {
	SchemaVersion string `json:"schema_version"`
	Data          any    `json:"data"`
	// HasMore/NextCursor mirror the Jentic list APIs' cursor pagination. NextCursor
	// is omitted when empty; HasMore is always present so an agent can branch on it
	// without a null check.
	HasMore    bool   `json:"has_more"`
	NextCursor string `json:"next_cursor,omitempty"`
	// Meta carries command-specific summary fields that are NOT the collection
	// itself (e.g. catalog's catalog_total / manifest_age_seconds). Kept under one
	// key so the top level stays a stable, closed shape across every list command;
	// omitted entirely when a command has no summary.
	Meta map[string]any `json:"meta,omitempty"`
}

// NewList builds a List envelope. nextCursor is empty on the last (or only) page;
// hasMore is derived from it so callers cannot set the two inconsistently. meta,
// when non-empty, carries command-specific summary fields under `meta`.
func NewList(data any, nextCursor string, meta map[string]any) List {
	l := List{Data: data, HasMore: nextCursor != "", NextCursor: nextCursor}
	if len(meta) > 0 {
		l.Meta = meta
	}
	return l
}

// Export is the versioned envelope for bulk export commands (history export). It
// wraps a fully-walked, filtered result set (never a single page) plus the pivot
// it was queried by, so an agent can template a workflow from a run's real
// executions. SchemaVersion is stamped at render time like the other envelopes.
type Export struct {
	SchemaVersion string `json:"schema_version"`
	// TraceID is the correlation pivot the export was queried by (impl/5.0 §2).
	TraceID string `json:"trace_id,omitempty"`
	// Items is the exported records (kept as `any` so the same envelope serves any
	// export; history passes []control.ExecutionResponse).
	Items any `json:"items"`
	// Count is the number of exported records after filtering.
	Count int `json:"count"`
}

// NewPage builds a Page envelope from a typed item slice and the opaque
// next-page cursor (empty on the last page). It is the bridge from the SDK's
// client/paginate walk helper (whose Page[T].Next is the cursor) to the machine
// contract's rendered envelope: a command walks pages with paginate.All/ForEach,
// then hands the collected items here for Render. Kept generic so callers pass
// their concrete []T without an interface conversion at the call site.
func NewPage[T any](items []T, nextToken string) Page {
	return Page{Items: items, NextToken: nextToken}
}

// NextHint is the human-mode footer telling the user how to fetch the next page.
// Cursor APIs use `--cursor <token>` (never `--page N`), so the hint is explicit
// about the token to pass.
func (p Page) NextHint() string {
	if !p.HasNext() {
		return ""
	}
	return "More results available. Re-run with --cursor " + p.NextToken
}

// CodedError is the typed error every fenced/validation/denial path returns so the
// error envelope can carry a closed-enum error_code. The enum values and their
// exit codes are owned by 13 §3a — do not invent codes here; add them to
// errorCodeExit in contract.go.
type CodedError struct {
	Code       string         // e.g. "MISSING_ARGUMENT", "BROKER_DENIED"
	Msg        string         // human/LLM-readable prose (redacted on output)
	Actionable string         // machine-runnable recovery step, when one exists
	Details    map[string]any // e.g. {"agent_directive": ..., "http_status": 403}
	// Cause optionally preserves the underlying error for errors.Is/As
	// classification (e.g. distinguishing a pre-send dial failure from a
	// mid-flight timeout). It is never rendered: Msg carries the prose.
	Cause error

	// reported records that an Audience.ReportError already rendered this error
	// (envelope on stderr in agent mode, styled line in human mode), so the
	// root-level residual-error backstop doesn't render it twice. Set by
	// ReportError; read via IsReported.
	reported bool
}

func (e *CodedError) Error() string { return e.Msg }

// Unwrap exposes the preserved cause (when any) to errors.Is/errors.As, so
// callers can classify the underlying failure without parsing Msg.
func (e *CodedError) Unwrap() error { return e.Cause }

// MarkReported flags the error as already rendered by an Audience. Called by
// every ReportError implementation; command code never needs it.
func (e *CodedError) MarkReported() { e.reported = true }

// IsReported reports whether an Audience already rendered this error.
func (e *CodedError) IsReported() bool { return e.reported }

// ExitCode makes every CodedError satisfy pkg/core's ExitCoder mechanically, so
// core.Run maps the closed enum to the exit taxonomy (13 §4) with no per-command
// wiring. Without it, a forgotten wrapper would exit 1 even for BROKER_DENIED
// (exit 2) or TIMEOUT_PENDING (exit 3). The code->exit table lives in contract.go.
func (e *CodedError) ExitCode() int { return exitCodeFor(e.Code) }

// AgentError is the stderr error envelope — the Go shape of the contract in 13 §3.
// That document is canonical; this struct mirrors it.
type AgentError struct {
	SchemaVersion string         `json:"schema_version"`
	ErrorCode     string         `json:"error_code"`
	Error         string         `json:"error"`
	Actionable    string         `json:"actionable_step,omitempty"`
	Details       map[string]any `json:"details,omitempty"`
}
