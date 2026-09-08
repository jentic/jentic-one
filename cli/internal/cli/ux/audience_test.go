package ux

import (
	"context"
	"encoding/json"
	"errors"
	"testing"
)

func TestExitCodeFor(t *testing.T) {
	cases := map[string]int{
		CodeMissingArgument:   ExitError,
		CodeFenced:            ExitError,
		CodeBrokerDenied:      ExitDenied,
		CodeResolveFailed:     ExitDenied,
		CodeTimeoutPending:    ExitTimeoutPending,
		CodePartialApproval:   ExitPartial,
		CodePendingApproval:   ExitError, // exit 3 only via TIMEOUT_PENDING, a distinct code
		"UNKNOWN_FUTURE_CODE": ExitError, // unknown => generic failure bucket
		"":                    ExitError,
	}
	for code, want := range cases {
		if got := exitCodeFor(code); got != want {
			t.Errorf("exitCodeFor(%q) = %d, want %d", code, got, want)
		}
	}
}

func TestCodedError_SatisfiesExitCoder(t *testing.T) {
	// CodedError.ExitCode() must map through the contract table.
	var err error = &CodedError{Code: CodeBrokerDenied, Msg: "denied"}
	type exitCoder interface{ ExitCode() int }
	ec, ok := err.(exitCoder)
	if !ok {
		t.Fatal("CodedError does not satisfy the ExitCoder shape")
	}
	if ec.ExitCode() != ExitDenied {
		t.Errorf("BROKER_DENIED exit = %d, want %d", ec.ExitCode(), ExitDenied)
	}
}

func TestAgentUX_Ask_FailsWithMissingArgument(t *testing.T) {
	a := NewAgentUX(false)
	_, err := a.Ask("name?", "name", true)
	var coded *CodedError
	if !errors.As(err, &coded) || coded.Code != CodeMissingArgument {
		t.Fatalf("Ask err = %v, want MISSING_ARGUMENT CodedError", err)
	}
}

func TestAgentUX_AskConfirm(t *testing.T) {
	// Without --yes: rejected with a coded error.
	deny := NewAgentUX(false)
	ok, err := deny.AskConfirm("delete everything?")
	if ok {
		t.Error("agent AskConfirm returned true without --yes")
	}
	var coded *CodedError
	if !errors.As(err, &coded) || coded.Code != CodeConfirmBlocked {
		t.Errorf("err = %v, want INTERACTIVE_CONFIRM_BLOCKED", err)
	}
	// With --yes: proceeds.
	yes := NewAgentUX(true)
	if ok, err := yes.AskConfirm("go?"); !ok || err != nil {
		t.Errorf("agent --yes AskConfirm = (%v,%v), want (true,nil)", ok, err)
	}
}

func TestAgentUX_ModeFlags(t *testing.T) {
	a := NewAgentUX(false)
	if !a.IsFenced() {
		t.Error("AgentUX must be fenced")
	}
	if !a.ForcesNoColor() {
		t.Error("AgentUX must force no-color")
	}
}

// TestAgentError_EnvelopeShape verifies ReportError's envelope carries the coded
// error's code, actionable step, and details, redacted. We can't easily capture
// os.Stderr here without plumbing, so exercise the envelope construction directly
// via safeMarshal on the same shape ReportError builds.
func TestAgentError_EnvelopeShape(t *testing.T) {
	coded := &CodedError{
		Code:       CodeBrokerDenied,
		Msg:        "broker denied: missing scope",
		Actionable: "jentic access request --scope write:issues",
		Details:    map[string]any{"http_status": 403, "api_key": "leaked-secret"},
	}
	ae := AgentError{
		SchemaVersion: currentSchemaVersion,
		ErrorCode:     coded.Code,
		Error:         coded.Msg,
		Actionable:    coded.Actionable,
		Details:       coded.Details,
	}
	out := safeMarshal(ae)
	var got map[string]any
	if err := json.Unmarshal(out, &got); err != nil {
		t.Fatalf("envelope not valid JSON: %v", err)
	}
	if got["error_code"] != CodeBrokerDenied {
		t.Errorf("error_code = %v", got["error_code"])
	}
	if got["schema_version"] != "1" {
		t.Errorf("schema_version = %v", got["schema_version"])
	}
	// Details.api_key must be redacted even inside the error envelope.
	details, _ := got["details"].(map[string]any)
	if details["api_key"] != "[REDACTED]" {
		t.Errorf("sensitive detail not redacted: %v", details["api_key"])
	}
}

func TestHumanUX_ModeFlags(t *testing.T) {
	h := NewHumanUX(Palette{}, false)
	if h.IsFenced() {
		t.Error("HumanUX must not be fenced")
	}
	if h.ForcesNoColor() {
		t.Error("HumanUX must not force no-color")
	}
}

func TestFromContext_FallsBackToAgent(t *testing.T) {
	// No audience in context => strict agent fallback (never a human prompt).
	aud := FromContext(context.Background())
	if !aud.IsFenced() {
		t.Error("missing-audience fallback should be fenced agent mode")
	}
}
