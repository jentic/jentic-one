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

	t.Setenv("JENTIC_MODE", "agent")
	if got := ResolveMode("", "human"); got != "agent" {
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

func TestResolveActiveState_LegacyIgnored(t *testing.T) {
	// A legacy ~/.jentic/config.yaml alone no longer resolves anything: the
	// activation release removed the legacy-read adapter, so resolution fails
	// exactly as if no config existed (the migrate gate points users at
	// `jentic migrate`).
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

	if _, err := ResolveActiveState("", ""); err == nil {
		t.Fatal("legacy config must NOT resolve — the V1 adapter was removed at activation")
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

// The retired service-account mode resolves to agent on every rung and reports
// the alias so the interceptor can warn (theme-8 D4).
func TestResolveModeLadder_ServiceAccountAliasesAgent(t *testing.T) {
	t.Setenv("JENTIC_MODE", "")
	os.Unsetenv("JENTIC_MODE")

	cases := []struct {
		name            string
		flag, env, pers string
	}{
		{"flag", "service-account", "", ""},
		{"env", "", "service-account", ""},
		{"persisted", "", "", "service-account"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if tc.env != "" {
				t.Setenv("JENTIC_MODE", tc.env)
			}
			mode, explicit, deprecated := ResolveModeLadder(tc.flag, tc.pers)
			if mode != ModeAgent || !explicit || deprecated != LegacyModeServiceAccount {
				t.Errorf("got (%q, %v, %q), want (agent, true, service-account)", mode, explicit, deprecated)
			}
		})
	}

	// A canonical or unknown value is never reported as a deprecated alias;
	// unknown values stay as-is for the interceptor to fail closed on.
	for _, raw := range []string{"agent", "human", "agnet"} {
		mode, _, deprecated := ResolveModeLadder(raw, "")
		if mode != raw || deprecated != "" {
			t.Errorf("%q: got (%q, %q)", raw, mode, deprecated)
		}
	}
}
