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
