package localagentcmd

import (
	"fmt"
	"io"

	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
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
	skillActionSkippedNewer = "skipped_newer" // install records a newer version than bundled; left alone (needs --force)
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
	// InstalledVersion and AvailableVersion name both sides of a
	// skipped_newer row (the install's recorded version, and the bundled one
	// a write would have downgraded it to); absent on every other action.
	InstalledVersion string `json:"installed_version,omitempty"`
	AvailableVersion string `json:"available_version,omitempty"`
	// AppliedVia names the operator whose row performed the write when this
	// operator shares the same artifact (codex and generic both load the
	// project AGENTS.md): the file is touched once and its outcome mirrored
	// here, so no shared target reports "updated" and "unchanged" in one run.
	AppliedVia string `json:"applied_via,omitempty"`

	// err is the per-target failure behind Action == "error", kept for the
	// human renderer's wording.
	err error
	// applied is the raw init outcome, kept so the human renderer reproduces
	// its exact wording (e.g. a dry run reports "would update" even when the
	// content is current).
	applied skillgen.Outcome
}

// sharedArtifacts remembers, within one verb invocation, the first result
// recorded for each (skill, path) artifact, so an operator that shares that
// artifact mirrors it instead of touching the file a second time.
type sharedArtifacts map[string]skillResult

// mirror returns the earlier result for (skill, path) re-attributed to
// operator, or false when this artifact has not been acted on yet.
func (s sharedArtifacts) mirror(operator, skill, path string) (skillResult, bool) {
	prev, ok := s[skillgen.ArtifactKey(skill, path)]
	if !ok {
		return skillResult{}, false
	}
	prev.AppliedVia = prev.Operator
	prev.Operator = operator
	return prev, true
}

// record stores r as the acting result for its artifact. Only results that
// actually touched the file are recorded, so the first one wins.
func (s sharedArtifacts) record(r skillResult) {
	key := skillgen.ArtifactKey(r.Skill, r.Path)
	if _, ok := s[key]; !ok {
		s[key] = r
	}
}

// skillResultsEnvelope is the JSON document init/update/remove write to stdout.
// It goes through cmdcore.WriteJSON (redacted) rather than ux.Render because
// its shape follows the cmdcore.JSONOrPretty seam: `--json` under default-human
// mode must still emit this document, while the Audience renders by mode alone.
type skillResultsEnvelope struct {
	SchemaVersion string        `json:"schema_version"`
	Results       []skillResult `json:"results"`
	// MCP is init's MCP registration outcome; absent for update/remove,
	// which never register MCP entries.
	MCP *mcpReport `json:"mcp,omitempty"`
}

// writeSkillResults emits the versioned outcome envelope. A nil slice is
// encoded as [] (never null) so consumers can iterate unconditionally; mcp is
// nil for the verbs that do not register MCP entries.
func writeSkillResults(w io.Writer, results []skillResult, mcp *mcpReport) error {
	if results == nil {
		results = []skillResult{}
	}
	return cmdcore.WriteJSON(w, skillResultsEnvelope{
		SchemaVersion: skillResultsSchemaVersion,
		Results:       results,
		MCP:           mcp,
	})
}

// setNewer fills the version pair on a skipped_newer row, so a consumer can
// see what a forced write would downgrade from and to.
func (r *skillResult) setNewer(out skillgen.Outcome) {
	if r.Action != skillActionSkippedNewer {
		return
	}
	r.InstalledVersion = out.InstalledVersion
	r.AvailableVersion = out.AppliedVersion
}

// newerInstalledNote is the human wording for a skipped_newer row.
func newerInstalledNote(installed, available string) string {
	return fmt.Sprintf("v%s installed is newer than bundled v%s; re-run with --force to downgrade", installed, available)
}

// applyAction maps one Apply outcome (and its error) onto the closed action
// vocabulary.
func applyAction(out skillgen.Outcome, err error) string {
	switch {
	case err != nil:
		return skillActionError
	case out.UserEdits:
		return skillActionUserEdits
	case out.NewerInstalled:
		return skillActionSkippedNewer
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
	case out.NewerInstalled:
		return skillActionSkippedNewer
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

// isMachine reports whether the resolved mode is agent or service-account,
// where the CLI never prompts. Unlike wantsJSON it ignores --json, which
// only selects the output shape.
func isMachine(cmd *cobra.Command) bool {
	if cmd == nil {
		return false
	}
	st := clictx.FromContext(cmd.Context())
	return st != nil && st.IsMachine()
}
