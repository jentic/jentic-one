package clictx

import (
	"os"
	"path/filepath"
	"testing"
)

func TestResolveMode_Ladder(t *testing.T) {
	t.Setenv("JENTIC_MODE", "")
	os.Unsetenv("JENTIC_MODE")

	if got := ResolveMode("agent", "human"); got != "agent" {
		t.Errorf("--mode override lost: %q", got)
	}

	t.Setenv("JENTIC_MODE", "service-account")
	if got := ResolveMode("", "human"); got != "service-account" {
		t.Errorf("JENTIC_MODE not honored: %q", got)
	}

	t.Setenv("JENTIC_MODE", "")
	os.Unsetenv("JENTIC_MODE")
	if got := ResolveMode("", "agent"); got != "agent" {
		t.Errorf("persisted mode not honored: %q", got)
	}
	if got := ResolveMode("", ""); got != ModeHuman {
		t.Errorf("default should be human, got %q", got)
	}
}

func TestResolveActiveState_FileLess(t *testing.T) {
	// The SDK file-less path (JENTIC_BASE_URL + JENTIC_BEARER_TOKEN) resolves to
	// agent mode with an injected token, bypassing disk entirely.
	t.Setenv("JENTIC_BASE_URL", "https://example.test")
	t.Setenv("JENTIC_BEARER_TOKEN", "tok-123")
	t.Setenv("JENTIC_MODE", "")
	os.Unsetenv("JENTIC_MODE")

	st, err := ResolveActiveState("", "")
	if err != nil {
		t.Fatalf("file-less resolve failed: %v", err)
	}
	if st.Mode != ModeAgent {
		t.Errorf("file-less mode = %q, want agent", st.Mode)
	}
	if st.BaseURL != "https://example.test" || st.InjectedBearerToken != "tok-123" {
		t.Errorf("file-less state not mapped: %+v", st.ResolvedState)
	}
}

func TestResolveActiveState_LegacyFallback(t *testing.T) {
	// No XDG config, but a legacy ~/.jentic/config.yaml exists: the adapter must
	// resolve state (V1 keeps working) rather than erroring.
	home := t.TempDir()
	t.Setenv("HOME", home)
	// Isolate the XDG path so LoadState finds nothing there.
	t.Setenv("XDG_CONFIG_HOME", filepath.Join(home, "xdg-empty"))
	t.Setenv("JENTIC_HOME", "")
	os.Unsetenv("JENTIC_HOME")
	// Ensure file-less env is off.
	t.Setenv("JENTIC_BASE_URL", "")
	os.Unsetenv("JENTIC_BASE_URL")
	t.Setenv("JENTIC_BEARER_TOKEN", "")
	os.Unsetenv("JENTIC_BEARER_TOKEN")
	t.Setenv("JENTIC_MODE", "")
	os.Unsetenv("JENTIC_MODE")

	// Write a minimal legacy config at ~/.jentic/config.yaml.
	legacyDir := filepath.Join(home, ".jentic")
	if err := os.MkdirAll(legacyDir, 0o700); err != nil {
		t.Fatal(err)
	}
	legacy := "base_url: https://legacy.test\ndefault_profile: oldprofile\n"
	if err := os.WriteFile(filepath.Join(legacyDir, "config.yaml"), []byte(legacy), 0o600); err != nil {
		t.Fatal(err)
	}

	st, err := ResolveActiveState("", "")
	if err != nil {
		t.Fatalf("legacy fallback should resolve, got error: %v", err)
	}
	if st.BaseURL != "https://legacy.test" {
		t.Errorf("legacy base URL not mapped: %q", st.BaseURL)
	}
	if st.IdentityName != "oldprofile" {
		t.Errorf("legacy profile not mapped to identity: %q", st.IdentityName)
	}
	// Legacy store has no mode -> defaults to human.
	if st.Mode != ModeHuman {
		t.Errorf("legacy mode should default to human, got %q", st.Mode)
	}
}

func TestResolveActiveState_NoConfigErrors(t *testing.T) {
	// No XDG config AND no legacy config: surface the original error.
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("XDG_CONFIG_HOME", filepath.Join(home, "xdg-empty"))
	t.Setenv("JENTIC_HOME", "")
	os.Unsetenv("JENTIC_HOME")
	t.Setenv("JENTIC_BASE_URL", "")
	os.Unsetenv("JENTIC_BASE_URL")
	t.Setenv("JENTIC_BEARER_TOKEN", "")
	os.Unsetenv("JENTIC_BEARER_TOKEN")

	if _, err := ResolveActiveState("", ""); err == nil {
		t.Error("expected an error when neither XDG nor legacy config exists")
	}
}

func TestActiveStateContextRoundTrip(t *testing.T) {
	st := &ActiveState{Mode: ModeAgent, ThemeName: "no-color"}
	ctx := WithActiveState(t.Context(), st)
	if got := FromContext(ctx); got == nil || got.Mode != ModeAgent {
		t.Errorf("ActiveState did not round-trip: %+v", got)
	}
	if FromContext(t.Context()) != nil {
		t.Error("missing ActiveState should return nil")
	}
}
