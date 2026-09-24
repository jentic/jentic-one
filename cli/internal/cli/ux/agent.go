package ux

import (
	"errors"
	"fmt"
	"os"

	"github.com/jentic/jentic-one/cli/internal/theme"
)

// AgentUX is the ruthlessly strict machine mode shared by `agent` and
// `service-account` (impl/3.1 §0): never prompts, no color, one JSON document per
// Render on stdout, structured error envelope on stderr.
type AgentUX struct {
	theme theme.Palette
	// assumeYes mirrors the global --yes. AgentUX cannot prompt, so this is the ONLY
	// way AskConfirm returns true for an agent (the root hook wires it in).
	assumeYes bool
}

// NewAgentUX builds the agent audience. The palette is always no-color.
func NewAgentUX(assumeYes bool) *AgentUX {
	return &AgentUX{theme: theme.Themes["no-color"], assumeYes: assumeYes}
}

// Ask cannot prompt an agent; it returns a MISSING_ARGUMENT CodedError.
func (a *AgentUX) Ask(_ /*question*/, flagName string, _ bool) (string, error) {
	// Agents cannot be prompted. Fail immediately with a typed error so ReportError
	// emits the machine contract's error_code (13 §3a).
	return "", &CodedError{
		Code:       CodeMissingArgument,
		Msg:        fmt.Sprintf("the required flag '--%s' was not provided", flagName),
		Actionable: fmt.Sprintf("Re-run the command with --%s <value>", flagName),
	}
}

// AskConfirm returns true only if the global --yes pre-authorized; otherwise it
// returns an INTERACTIVE_CONFIRM_BLOCKED CodedError.
func (a *AgentUX) AskConfirm(_ string) (bool, error) {
	// The global --yes is the explicit, auditable authorization; without it, reject
	// with guidance rather than blocking.
	if a.assumeYes {
		return true, nil
	}
	return false, &CodedError{
		Code:       CodeConfirmBlocked,
		Msg:        "command requires confirmation",
		Actionable: "Re-run with the '--yes' flag to explicitly authorize this action",
	}
}

// Render writes one compact, redacted JSON document to stdout.
func (a *AgentUX) Render(data any) {
	// One compact JSON document per call (one object, or one array for collections),
	// newline-terminated. NOT NDJSON: each Render produces exactly one top-level
	// value — what oapi response structs unmarshal to.
	fmt.Fprintln(os.Stdout, string(safeMarshal(data)))
}

// ReportError writes the redacted structured error envelope to stderr.
func (a *AgentUX) ReportError(err error, step string) {
	// JSON so the agent can parse it. safeMarshal routes the error string through the
	// byte-level redaction backstop (M6): agents capture 2>&1, so an error echoing a
	// backend body is scrubbed like any payload.
	ae := AgentError{
		SchemaVersion: currentSchemaVersion,
		ErrorCode:     CodeInternalError, // fallback; unknown/internal => "stop and report"
		Error:         err.Error(),
		Actionable:    step,
	}
	var coded *CodedError
	if errors.As(err, &coded) {
		ae.ErrorCode = coded.Code
		ae.Details = coded.Details
		if ae.Actionable == "" {
			ae.Actionable = coded.Actionable
		}
		coded.MarkReported()
	}
	fmt.Fprintln(os.Stderr, string(safeMarshal(ae)))
}

// Theme returns the agent (always no-color) palette.
func (a *AgentUX) Theme() Palette { return a.theme }

// IsFenced reports that agents are locked out of admin commands.
func (a *AgentUX) IsFenced() bool { return true }

// ForcesNoColor reports that agent output must never carry ANSI (protects LLM parsers).
func (a *AgentUX) ForcesNoColor() bool { return true }
