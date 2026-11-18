package ux

import (
	"bytes"
	"io"
	"os"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/theme"
)

// captureStdout redirects os.Stdout for fn's duration (Render writes to the
// process stream directly, per the stdout/stderr boundary contract).
func captureStdout(t *testing.T, fn func()) string {
	t.Helper()
	old := os.Stdout
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatalf("pipe: %v", err)
	}
	os.Stdout = w
	defer func() { os.Stdout = old }()
	fn()
	_ = w.Close()
	var buf bytes.Buffer
	_, _ = io.Copy(&buf, r)
	return buf.String()
}

// TestHumanRenderResultFields is the UX-3 regression: a Result's Fields map —
// e.g. `context view`'s environment/identity/mode — must reach the human, not
// just the agent envelope.
func TestHumanRenderResultFields(t *testing.T) {
	h := NewHumanUX(theme.Themes["no-color"], false)
	out := captureStdout(t, func() {
		h.Render(Result{
			Status:   "active",
			Resource: "context",
			Name:     "default",
			Fields: map[string]any{
				"environment": "local",
				"identity":    "agent1",
				"mode":        "agent",
			},
		})
	})
	for _, want := range []string{"context 'default' active", "environment: local", "identity: agent1", "mode: agent"} {
		if !strings.Contains(out, want) {
			t.Errorf("human output missing %q:\n%s", want, out)
		}
	}
}

// TestHumanRenderResultFieldsRedactsSecretKeys: Fields must never leak a
// secret-shaped value on the human path either.
func TestHumanRenderResultFieldsRedactsSecretKeys(t *testing.T) {
	h := NewHumanUX(theme.Themes["no-color"], false)
	out := captureStdout(t, func() {
		h.Render(Result{
			Status: "created",
			Fields: map[string]any{"api_key": "jak_supersecret"},
		})
	})
	if strings.Contains(out, "jak_supersecret") {
		t.Errorf("secret value leaked on human field path:\n%s", out)
	}
	if !strings.Contains(out, "[REDACTED]") {
		t.Errorf("expected [REDACTED] placeholder:\n%s", out)
	}
}

// TestHumanRenderMapListStyled is the UX-4 regression: config-list rows
// ([]map[string]any) get the styled list treatment — radio glyph, name header,
// indented fields — not a raw JSON dump.
func TestHumanRenderMapListStyled(t *testing.T) {
	h := NewHumanUX(theme.Themes["no-color"], false)
	out := captureStdout(t, func() {
		h.Render(NewPage([]map[string]any{
			{"name": "default", "environment": "local", "identity": "agent1", "active": true},
			{"name": "staging", "environment": "stg", "identity": "agent2", "active": false},
		}, ""))
	})
	if strings.Contains(out, "{") || strings.Contains(out, "[") {
		t.Errorf("map-list rows rendered as JSON, want styled lines:\n%s", out)
	}
	if !strings.Contains(out, theme.SelectOn+" default") {
		t.Errorf("active row missing filled radio + name header:\n%s", out)
	}
	if !strings.Contains(out, theme.SelectOff+" staging") {
		t.Errorf("inactive row missing hollow radio + name header:\n%s", out)
	}
	if !strings.Contains(out, "environment: local") {
		t.Errorf("row fields missing:\n%s", out)
	}
}

// TestHumanRenderTypedSliceStaysJSON: API-payload pages (typed slices) keep the
// JSON rendering — the data IS the payload there.
func TestHumanRenderTypedSliceStaysJSON(t *testing.T) {
	type item struct {
		ID string `json:"id"`
	}
	h := NewHumanUX(theme.Themes["no-color"], false)
	out := captureStdout(t, func() {
		h.Render(NewPage([]item{{ID: "a"}}, ""))
	})
	if !strings.Contains(out, `"id": "a"`) {
		t.Errorf("typed slice no longer renders as JSON:\n%s", out)
	}
}

// captureStderr redirects os.Stderr for fn's duration.
func captureStderr(t *testing.T, fn func()) string {
	t.Helper()
	old := os.Stderr
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatalf("pipe: %v", err)
	}
	os.Stderr = w
	defer func() { os.Stderr = old }()
	fn()
	_ = w.Close()
	var buf bytes.Buffer
	_, _ = io.Copy(&buf, r)
	return buf.String()
}

func TestFieldValueNeutralizesEscapes(t *testing.T) {
	h := NewHumanUX(theme.Themes["no-color"], false)

	got := h.fieldValue("description", "hello \x1b]52;c;SGVsbG8=\x07\x1b[2J\rworld")
	if strings.ContainsAny(got, "\x1b\x07\r") {
		t.Errorf("fieldValue left control characters intact: %q", got)
	}
	for _, want := range []string{`\u001b]52;c;SGVsbG8=\u0007`, `\u001b[2J`, `\u000dworld`} {
		if !strings.Contains(got, want) {
			t.Errorf("fieldValue(%q) missing escaped %q", got, want)
		}
	}

	listOut := captureStdout(t, func() {
		h.Render(NewPage([]map[string]any{
			{
				"name":   "Bearer secret-token-123 \x1b]0;title\x07",
				"active": true,
				"url":    "https://api.example.com/\x1b[2J",
			},
		}, "cursor-\x1b[2J"))
	})
	if strings.Contains(listOut, "secret-token-123") {
		t.Errorf("renderMapList leaked bearer token in name:\n%s", listOut)
	}
	if strings.ContainsAny(listOut, "\x1b\x07") {
		t.Errorf("renderMapList left terminal escapes in output:\n%q", listOut)
	}
	if !strings.Contains(listOut, `\u001b]0;title\u0007`) || !strings.Contains(listOut, `\u001b[2J`) {
		t.Errorf("renderMapList missing visible escape replacement:\n%s", listOut)
	}

	resOut := captureStdout(t, func() {
		h.Render(Result{
			Status:   "created",
			Resource: "environment",
			Name:     "prod\x1b[2J",
			Message:  "note\r\x1b]52;c;YQ==\x07",
		})
	})
	if strings.ContainsAny(resOut, "\x1b\x07\r") {
		t.Errorf("renderResultLine left terminal escapes in output:\n%q", resOut)
	}
	if !strings.Contains(resOut, `prod\u001b[2J`) || !strings.Contains(resOut, `note\u000d\u001b]52;c;YQ==\u0007`) {
		t.Errorf("renderResultLine missing visible escape replacement:\n%s", resOut)
	}
}

func TestReportErrorNeutralizesEscapes(t *testing.T) {
	h := NewHumanUX(theme.Themes["no-color"], false)
	errOut := captureStderr(t, func() {
		h.ReportError(
			&CodedError{
				Code:       CodeTransportError,
				Msg:        "upstream failed: \x1b[2J\x1b]52;c;SGVsbG8=\x07\rforged",
				Actionable: "run \x1b]8;;https://example.invalid\x1b\\jentic doctor\x1b]8;;\x1b\\",
			},
			"",
		)
	})
	if strings.ContainsAny(errOut, "\x1b\x07\r") {
		t.Errorf("ReportError left terminal escapes in stderr:\n%q", errOut)
	}
	for _, want := range []string{`\u001b[2J`, `\u001b]52;c;SGVsbG8=\u0007`, `\u000dforged`, `\u001b]8;;https://example.invalid`} {
		if !strings.Contains(errOut, want) {
			t.Errorf("ReportError stderr missing %q:\n%s", want, errOut)
		}
	}
}

func TestRenderDirectiveAndHeldNeutralizeEscapes(t *testing.T) {
	ctx := theme.WithContext(t.Context(), theme.Themes["no-color"])

	var dirBuf bytes.Buffer
	RenderDirective(ctx, &dirBuf, Directive{
		Strategy:    DirectiveRetry,
		Instruction: "check \x1b[2J instruction",
		Parameters: map[string]any{
			"suggested_command":   "jentic \x1b]52;c;YQ==\x07 whoami",
			"provisioning_url":    "https://console.example/\x1b[2J",
			"candidates":          []any{"cand1\rforged"},
			"retry_after_seconds": "10\x1b[2J",
		},
	})
	if strings.ContainsAny(dirBuf.String(), "\x1b\x07\r") {
		t.Errorf("RenderDirective left control characters in output:\n%q", dirBuf.String())
	}

	var heldBuf bytes.Buffer
	RenderHeld(ctx, &heldBuf, HeldNotice{
		HeldExecutionID: "held-\x1b[2J",
		ReviewURL:       "https://console.example/review/\x1b]0;pwn\x07",
		ExpiresAt:       "2026-11-14T00:00:00Z\rforged",
		Trusted:         true,
	})
	RenderHeldUnfinished(ctx, &heldBuf, HeldNotice{
		HeldExecutionID: "held-\x1b[2J",
		ReviewURL:       "https://console.example/review/\x1b]0;pwn\x07",
		ExpiresAt:       "2026-11-14T00:00:00Z\rforged",
		Trusted:         true,
	}, "Still held\x1b[2J")
	RenderHeldFinished(ctx, &heldBuf, "held-\x1b[2J", "outcome\rforged")
	RenderApprovalOutcome(ctx, &heldBuf, ApprovalDeniedType, "reviewer said \x1b[2J no")
	if strings.ContainsAny(heldBuf.String(), "\x1b\x07\r") {
		t.Errorf("RenderHeld* left control characters in output:\n%q", heldBuf.String())
	}
}
