package ux

// Plan is the machine-readable execution plan emitted for --dry-run/--export-plan
// (impl/5.0 §5): the operation a mutating command WOULD invoke and the hydrated
// payload it would send, without firing the call. It runs through the same
// redaction funnel as every other rendered value, so a plan can't leak a secret
// in its payload. SchemaVersion is stamped at render time like the other
// envelopes.
type Plan struct {
	SchemaVersion string `json:"schema_version"`
	Operation     string `json:"operation"`
	DryRun        bool   `json:"dry_run"`
	Payload       any    `json:"payload,omitempty"`
}
