package api

import (
	"bytes"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/profile"
	"github.com/spf13/cobra"
)

func TestProfileAddKeyStoresKey(t *testing.T) {
	app := testApp(t)
	opts := &addKeyOptions{apiKey: "jak_supersecretkey", baseURL: "http://example:9000"}
	if err := app.profileAddKeyE(&cobra.Command{}, "work", opts); err != nil {
		t.Fatalf("profileAddKeyE: %v", err)
	}

	p, err := profile.Open(app.Paths, "work")
	if err != nil {
		t.Fatalf("open profile: %v", err)
	}
	meta, err := p.LoadMeta()
	if err != nil {
		t.Fatalf("load meta: %v", err)
	}
	if !meta.IsAPIKey() {
		t.Errorf("profile not marked api_key: %+v", meta)
	}
	if meta.BaseURL != "http://example:9000" {
		t.Errorf("base_url = %q", meta.BaseURL)
	}
	key, err := p.LoadAPIKey()
	if err != nil {
		t.Fatalf("load key: %v", err)
	}
	if key != "jak_supersecretkey" {
		t.Errorf("stored key = %q", key)
	}

	out := app.Out.(*bytes.Buffer).String()
	if strings.Contains(out, "jak_supersecretkey") {
		t.Errorf("output leaked the full API key:\n%s", out)
	}
	if !strings.Contains(out, "Stored API key") {
		t.Errorf("missing confirmation:\n%s", out)
	}
}

func TestProfileAddKeyRejectsBadPrefix(t *testing.T) {
	app := testApp(t)
	opts := &addKeyOptions{apiKey: "nope_123"}
	err := app.profileAddKeyE(&cobra.Command{}, "work", opts)
	if err == nil || !strings.Contains(err.Error(), "must start with") {
		t.Fatalf("expected prefix error, got %v", err)
	}
}

// In the test runner stdin is not a TTY, so a missing key must error rather than
// block on an interactive prompt.
func TestProfileAddKeyMissingKeyNonTTYErrors(t *testing.T) {
	app := testApp(t)
	err := app.profileAddKeyE(&cobra.Command{}, "work", &addKeyOptions{})
	if err == nil || !strings.Contains(err.Error(), "no API key given") {
		t.Fatalf("expected no-key error, got %v", err)
	}
}

func TestMaskAPIKey(t *testing.T) {
	if got := maskAPIKey("jak_abcdefgh1234"); got != "jak_…1234" {
		t.Errorf("maskAPIKey = %q, want jak_…1234", got)
	}
	if got := maskAPIKey("jak_x"); got != "jak_…" {
		t.Errorf("short key mask = %q, want jak_…", got)
	}
	if got := apiKeyLabel(""); got != "missing" {
		t.Errorf("empty label = %q, want missing", got)
	}
}
