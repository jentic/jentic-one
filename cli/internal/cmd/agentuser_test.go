package cmd

import (
	"bytes"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/skillgen"
)

func TestFirstKnownAgent(t *testing.T) {
	// A launchable local agent among the selected operators resolves.
	id, desc, ok := firstKnownAgent([]string{"cursor", "claude", "generic"})
	if !ok || id != "claude" || desc.Binary != "claude" {
		t.Fatalf("firstKnownAgent = (%q, %+v, %v), want claude", id, desc, ok)
	}
	// Operators with no launchable local agent resolve to nothing.
	if _, _, ok := firstKnownAgent([]string{"cursor", "generic", "codex"}); ok {
		t.Fatal("did not expect a known local agent among non-launchable operators")
	}
	if _, _, ok := firstKnownAgent(nil); ok {
		t.Fatal("did not expect a known agent for an empty selection")
	}
}

func TestOperatorNames(t *testing.T) {
	reg := skillgen.DefaultRegistry()
	adapters := reg.Adapters()
	names := operatorNames(adapters)
	if len(names) != len(adapters) {
		t.Fatalf("operatorNames length = %d, want %d", len(names), len(adapters))
	}
	found := false
	for _, n := range names {
		if n == "claude" {
			found = true
		}
	}
	if !found {
		t.Errorf("expected claude among operator names, got %v", names)
	}
	if got := operatorNames(nil); len(got) != 0 {
		t.Errorf("operatorNames(nil) = %v, want empty", got)
	}
}

// TestRecordAgentAccount checks the persisted boolean and that a re-record keeps
// the original CreatedAt stamp while updating the create flag.
func TestRecordAgentAccount(t *testing.T) {
	app := &App{Paths: config.Paths{Root: t.TempDir()}, Out: &bytes.Buffer{}, Err: &bytes.Buffer{}}

	// Declined: recorded as not created, no home, no config-dir reference.
	app.recordAgentAccount("claude", "alice-local-agent", "claude", "", "", false)
	cfg, err := config.Load(app.Paths)
	if err != nil {
		t.Fatalf("load: %v", err)
	}
	entry, ok := cfg.LocalAgent("claude")
	if !ok || entry.AccountCreated || entry.User != "alice-local-agent" {
		t.Fatalf("declined entry = %+v (ok=%v)", entry, ok)
	}
	if entry.ConfigDir != "" {
		t.Errorf("declined entry should have no config-dir reference, got %q", entry.ConfigDir)
	}
	if entry.CreatedAt == "" {
		t.Fatal("expected CreatedAt to be stamped")
	}
	firstStamp := entry.CreatedAt

	// Later opting in: create flag flips, home + config-dir reference are set,
	// stamp is preserved.
	app.recordAgentAccount("claude", "alice-local-agent", "claude", "/Users/Shared/alice-local-agent", "/Users/Shared/alice-local-agent/.jentic", true)
	cfg, err = config.Load(app.Paths)
	if err != nil {
		t.Fatalf("reload: %v", err)
	}
	entry, _ = cfg.LocalAgent("claude")
	if !entry.AccountCreated {
		t.Error("expected AccountCreated to flip to true")
	}
	if entry.HomeDir != "/Users/Shared/alice-local-agent" {
		t.Errorf("home = %q", entry.HomeDir)
	}
	if entry.ConfigDir != "/Users/Shared/alice-local-agent/.jentic" {
		t.Errorf("config_dir = %q, want the agent's ~/.jentic", entry.ConfigDir)
	}
	if entry.CreatedAt != firstStamp {
		t.Errorf("CreatedAt changed on re-record: %q → %q", firstStamp, entry.CreatedAt)
	}
}
