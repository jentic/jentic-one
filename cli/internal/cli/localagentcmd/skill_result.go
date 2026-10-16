package localagentcmd

import (
	"io"

	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/skillgen"
	"github.com/spf13/cobra"
)

// skill_result.go holds the machine-readable outcome model shared by the
// mutating skill verbs (init/update/remove, #830). Each verb first collects one
// skillResult per (operator, skill, scope) it touched, then renders either the
// human report or the versioned JSON envelope from the same slice, so the two
// views can never disagree about what happened.

// skillResultsSchemaVersion pins the `{schema_version, results}` envelope
// emitted by `skill init/update/remove --json`.
const skillResultsSchemaVersion = "1"

// Closed vocabulary for skillResult.Action. With dry_run=true the action names
// what a real run would have done; nothing was written.
const (
	skillActionCreated      = "created"       // a new install was written
	skillActionUpdated      = "updated"       // an existing install was rewritten
	skillActionUnchanged    = "unchanged"     // already up to date; nothing to write
	skillActionRemoved      = "removed"       // the install was stripped/deleted
	skillActionNotInstalled = "not_installed" // remove: nothing installed at that scope
	skillActionUserEdits    = "user_edits"    // manual edits detected; left alone (needs --force)
	skillActionError        = "error"         // this target failed; see error
)

// skillResult is one (operator, skill, scope) outcome of a mutating skill verb.
// Path is the resolved absolute target (the same value `skill list --json`
// reports in installs[].path).
type skillResult struct {
	Operator  string `json:"operator"`
	Skill     string `json:"skill"`
	Scope     string `json:"scope"`
	Path      string `json:"path"`
	Action    string `json:"action"`
	DryRun    bool   `json:"dry_run"`
	UserEdits bool   `json:"user_edits"`
	Error     string `json:"error,omitempty"`

	// err is the per-target failure behind Action == "error", kept for the
	// human renderer's wording.
	err error
	// applied is the raw init outcome, kept so the human renderer reproduces
	// its exact wording (e.g. a dry run reports "would update" even when the
	// content is current).
	applied skillgen.Outcome
}

// skillResultsEnvelope is the JSON document init/update/remove write to stdout.
type skillResultsEnvelope struct {
	SchemaVersion string        `json:"schema_version"`
	Results       []skillResult `json:"results"`
}

// writeSkillResults emits the versioned outcome envelope. A nil slice is
// encoded as [] (never null) so consumers can iterate unconditionally.
func writeSkillResults(w io.Writer, results []skillResult) error {
	if results == nil {
		results = []skillResult{}
	}
	return cmdcore.WriteJSON(w, skillResultsEnvelope{
		SchemaVersion: skillResultsSchemaVersion,
		Results:       results,
	})
}

// applyAction maps one Apply outcome (and its error) onto the closed action
// vocabulary.
func applyAction(out skillgen.Outcome, err error) string {
	switch {
	case err != nil:
		return skillActionError
	case out.UserEdits:
		return skillActionUserEdits
	case out.Skipped:
		return skillActionUnchanged
	case out.Created:
		return skillActionCreated
	default:
		return skillActionUpdated
	}
}

// updateAction maps one refresh Apply outcome onto the closed action
// vocabulary. update only re-renders existing installs, so a write is always
// "updated" (never "created") and anything else is "unchanged".
func updateAction(out skillgen.Outcome, err error) string {
	switch {
	case err != nil:
		return skillActionError
	case out.UserEdits:
		return skillActionUserEdits
	case out.Changed:
		return skillActionUpdated
	default:
		return skillActionUnchanged
	}
}

// removeAction maps one Remove outcome (and its error) onto the closed action
// vocabulary.
func removeAction(out skillgen.RemoveOutcome, err error) string {
	switch {
	case err != nil:
		return skillActionError
	case out.UserEdits:
		return skillActionUserEdits
	case out.Missing:
		return skillActionNotInstalled
	default:
		return skillActionRemoved
	}
}

// errString renders an optional error for the JSON `error` field.
func errString(err error) string {
	if err == nil {
		return ""
	}
	return err.Error()
}

// withOut returns a shallow copy of a whose stdout is w. JSON mode uses it to
// send the human-facing side reports (defaulted-target echo, MCP
// registration) to stderr, keeping stdout a single JSON document.
func (a *Cmd) withOut(w io.Writer) *Cmd {
	app := *a.App
	app.Out = w
	return &Cmd{App: &app}
}

// wantsJSON resolves the output shape for a mutating skill verb through the
// shared --json/mode/TTY seam. A nil cmd (programmatic callers such as
// `jenticctl update` refreshing skills) always renders for humans.
func wantsJSON(cmd *cobra.Command, jsonFlag bool) bool {
	if cmd == nil {
		return jsonFlag
	}
	return cmdcore.JSONOrPretty(cmd, jsonFlag)
}
