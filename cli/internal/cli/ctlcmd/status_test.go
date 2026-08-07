package ctlcmd

import (
	"bytes"
	"context"
	"strings"
	"testing"
	"time"

	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/profile"
)

// A fresh setup (no manifest, no server, no profile) should render all three
// sections and report each missing piece without erroring.
func TestStatusEmptySetupDegradesGracefully(t *testing.T) {
	app := testApp(t)
	// Point at a closed port so the server probe is deterministically offline
	// and the test never depends on a live local control plane.
	if err := app.statusE(context.Background(), &identityOptions{BaseURL: "http://127.0.0.1:1"}); err != nil {
		t.Fatalf("statusE on empty setup: %v", err)
	}
	got := app.Out.(*bytes.Buffer).String()
	for _, want := range []string{"Install", "Server", "Broker", "Agent", "no install manifest", "offline", "not registered"} {
		if !strings.Contains(got, want) {
			t.Errorf("status output missing %q\n---\n%s", want, got)
		}
	}
}

// A recorded manifest should surface the install mode and source line.
func TestStatusShowsInstallManifest(t *testing.T) {
	app := testApp(t)
	m := &config.Manifest{Repo: "jentic/jentic-one", Ref: "feat/cli", Commit: "abc1234", Mode: config.ModeDocker, DB: "postgres"}
	if err := m.Save(app.Paths); err != nil {
		t.Fatalf("save manifest: %v", err)
	}
	if err := app.statusE(context.Background(), &identityOptions{BaseURL: "http://127.0.0.1:1"}); err != nil {
		t.Fatalf("statusE: %v", err)
	}
	got := app.Out.(*bytes.Buffer).String()
	for _, want := range []string{"docker", "postgres", "jentic/jentic-one@feat/cli (abc1234)"} {
		if !strings.Contains(got, want) {
			t.Errorf("status output missing %q\n---\n%s", want, got)
		}
	}
}

func TestTokenStatus(t *testing.T) {
	if label, _ := tokenStatus(nil); label != "none" {
		t.Errorf("nil tokens label = %q, want none", label)
	}
	if label, _ := tokenStatus(&profile.Tokens{AccessToken: "a", AccessExpiresAt: time.Now().Add(-time.Hour)}); label != "expired" {
		t.Errorf("past tokens label = %q, want expired", label)
	}
	if label, _ := tokenStatus(&profile.Tokens{AccessToken: "a", AccessExpiresAt: time.Now().Add(time.Hour)}); !strings.HasPrefix(label, "valid") {
		t.Errorf("future tokens label = %q, want valid*", label)
	}
}

func TestIdentityLabel(t *testing.T) {
	if got := identityLabel(map[string]any{"sub": "agnt_1", "name": "Bot"}); got != "Bot" {
		t.Errorf("identityLabel preferred name, got %q", got)
	}
	if got := identityLabel(map[string]any{}); got != "ok" {
		t.Errorf("identityLabel empty = %q, want ok", got)
	}
}
