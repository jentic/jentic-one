package cmd

import (
	"bytes"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/skillgen"
)

func TestPortSelect(t *testing.T) {
	// With sources present the default value is left as-is (the caller pre-sets it
	// to Yes), so the affirmative option stays selected for the common case.
	val := true
	_ = portSelect("Copy config?", "why", []string{"/Users/alice/.claude", "/Users/alice/.claude.json"}, &val)
	if !val {
		t.Error("portSelect must not flip a Yes default when there is something to copy")
	}

	// With no sources the select forces the value to false (nothing to copy) so the
	// operator is never offered a copy of an empty set.
	val = true
	portSelect("Copy config?", "why", nil, &val)
	if val {
		t.Error("portSelect must force the value to false when there is nothing to copy")
	}
}

func TestProviderToggleTitle(t *testing.T) {
	if got := providerToggleTitle("aws"); got != "Copy your aws provider config into the agent's home?" {
		t.Errorf("named provider title = %q", got)
	}
	// The Anthropic default (no separate provider config) gets the generic title.
	for _, name := range []string{"", "anthropic"} {
		if got := providerToggleTitle(name); got != "Copy your LLM provider config into the agent's home?" {
			t.Errorf("providerToggleTitle(%q) = %q, want generic", name, got)
		}
	}
}

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

// TestRecordAgentAccount checks the persisted booleans and that a re-record keeps
// the original CreatedAt stamp while updating the create flag.
func TestRecordAgentAccount(t *testing.T) {
	app := &App{Paths: config.Paths{Root: t.TempDir()}, Out: &bytes.Buffer{}, Err: &bytes.Buffer{}}

	// Declined: recorded as not created, no home, no config-dir reference.
	app.recordAgentAccount("alice-local-agent", "", "", false)
	cfg, err := config.Load(app.Paths)
	if err != nil {
		t.Fatalf("load: %v", err)
	}
	acct, ok := cfg.AgentAccount()
	if !ok || acct.AccountCreated || acct.Enabled || acct.User != "alice-local-agent" {
		t.Fatalf("declined account = %+v (ok=%v)", acct, ok)
	}
	if acct.ConfigDir != "" {
		t.Errorf("declined account should have no config-dir reference, got %q", acct.ConfigDir)
	}
	if acct.CreatedAt == "" {
		t.Fatal("expected CreatedAt to be stamped")
	}
	firstStamp := acct.CreatedAt

	// Later opting in: create + enabled flags flip, home + config-dir reference are
	// set, stamp is preserved.
	app.recordAgentAccount("alice-local-agent", "/Users/Shared/alice-local-agent", "/Users/Shared/alice-local-agent/.jentic", true)
	cfg, err = config.Load(app.Paths)
	if err != nil {
		t.Fatalf("reload: %v", err)
	}
	acct, _ = cfg.AgentAccount()
	if !acct.AccountCreated || !acct.Enabled {
		t.Error("expected AccountCreated and Enabled to flip to true")
	}
	if acct.HomeDir != "/Users/Shared/alice-local-agent" {
		t.Errorf("home = %q", acct.HomeDir)
	}
	if acct.ConfigDir != "/Users/Shared/alice-local-agent/.jentic" {
		t.Errorf("config_dir = %q, want the agent's ~/.jentic", acct.ConfigDir)
	}
	if acct.CreatedAt != firstStamp {
		t.Errorf("CreatedAt changed on re-record: %q → %q", firstStamp, acct.CreatedAt)
	}
}
