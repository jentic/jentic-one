package config

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
)

// withAgentXDG points XDG_CONFIG_HOME at a fresh temp dir so every test reads
// and writes an isolated agent state, and returns the resolved agent-state
// path. Legacy Paths are per-test temp dirs already.
func withAgentXDG(t *testing.T) string {
	t.Helper()
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	path, err := AgentStatePath()
	if err != nil {
		t.Fatalf("AgentStatePath: %v", err)
	}
	return path
}

func TestAgentStateRoundTrip(t *testing.T) {
	withAgentXDG(t)
	paths := Paths{Root: t.TempDir()}

	st, err := LoadAgentState(paths)
	if err != nil {
		t.Fatalf("LoadAgentState: %v", err)
	}
	if st.Loaded || st.HasAgentUser() {
		t.Fatal("fresh state should be empty and not Loaded")
	}

	if _, err := MutateAgentState(paths, func(s *AgentState) error {
		s.SetAgentAccount(AgentAccount{
			User:           "alice-local-agent",
			AccountCreated: true,
			Enabled:        true,
			HomeDir:        "/Users/Shared/alice-local-agent",
		})
		if !s.AddGrantedDir("/Users/Shared/alice-local-agent/work") {
			t.Error("expected AddGrantedDir to report a new grant")
		}
		if s.AddGrantedDir("/Users/Shared/alice-local-agent/work") {
			t.Error("expected duplicate AddGrantedDir to be idempotent")
		}
		return nil
	}); err != nil {
		t.Fatalf("MutateAgentState: %v", err)
	}

	got, err := LoadAgentState(paths)
	if err != nil {
		t.Fatalf("reload: %v", err)
	}
	if !got.Loaded {
		t.Fatal("expected the XDG state file to exist after a mutation")
	}
	if !got.HasAgentUser() {
		t.Fatal("expected an enabled agent account after reload")
	}
	acct, ok := got.AgentAccount()
	if !ok {
		t.Fatal("agent account not persisted")
	}
	if acct.User != "alice-local-agent" || acct.HomeDir == "" {
		t.Errorf("unexpected account: %+v", acct)
	}
	if !acct.AccountCreated || !acct.Enabled {
		t.Error("expected AccountCreated and Enabled to round-trip as true")
	}
	if len(acct.GrantedDirs) != 1 {
		t.Fatalf("granted dirs = %v", acct.GrantedDirs)
	}

	if !got.RemoveGrantedDir("/Users/Shared/alice-local-agent/work") {
		t.Fatal("expected RemoveGrantedDir to report removal")
	}
	if got.RemoveGrantedDir("/nope") {
		t.Fatal("did not expect removal of an absent dir")
	}
	acct, _ = got.AgentAccount()
	if len(acct.GrantedDirs) != 0 {
		t.Errorf("granted dirs after remove = %v", acct.GrantedDirs)
	}
}

// TestAgentStateLegacyFallback proves a record written by an older release into
// ~/.jentic/config.yaml is still visible when no XDG state exists yet — listing
// grants, launching, and resetting all keep working before any migration.
func TestAgentStateLegacyFallback(t *testing.T) {
	withAgentXDG(t)
	paths := writeConfig(t, strings.Join([]string{
		"base_url: https://example.test",
		"agent_account:",
		"  user: alice-local-agent",
		"  account_created: true",
		"  enabled: true",
		"  home_dir: /Users/Shared/alice-local-agent",
		"  granted_dirs:",
		"    - /Users/Shared/alice-local-agent/work",
		"same_user_notice_seen: true",
	}, "\n")+"\n")

	st, err := LoadAgentState(paths)
	if err != nil {
		t.Fatalf("LoadAgentState: %v", err)
	}
	if st.Loaded {
		t.Error("legacy fallback must not claim the XDG file was loaded")
	}
	if !st.HasAgentUser() || !st.SameUserNoticeSeen {
		t.Fatalf("legacy record not projected: %+v", st)
	}
	acct, _ := st.AgentAccount()
	if len(acct.GrantedDirs) != 1 {
		t.Errorf("granted dirs = %v", acct.GrantedDirs)
	}
}

// TestMutateAgentStateAdoptsAndClearsLegacy proves the first write adopts a
// legacy record into the XDG file and strips the agent fields from the legacy
// config — leaving exactly one copy of the record — while preserving the legacy
// config's unrelated fields.
func TestMutateAgentStateAdoptsAndClearsLegacy(t *testing.T) {
	statePath := withAgentXDG(t)
	paths := writeConfig(t, strings.Join([]string{
		"base_url: https://keep.example.test",
		"agent_account:",
		"  user: alice-local-agent",
		"  account_created: true",
		"  enabled: true",
		"same_user_notice_seen: true",
	}, "\n")+"\n")

	got, err := MutateAgentState(paths, func(*AgentState) error { return nil })
	if err != nil {
		t.Fatalf("MutateAgentState: %v", err)
	}
	if !got.HasAgentUser() || !got.SameUserNoticeSeen {
		t.Fatalf("adopted state lost the legacy record: %+v", got)
	}
	if _, err := os.Stat(statePath); err != nil {
		t.Fatalf("XDG state file not written: %v", err)
	}

	legacy, err := Load(paths)
	if err != nil {
		t.Fatalf("reload legacy: %v", err)
	}
	if legacy.Agent != nil || legacy.SameUserNoticeSeen {
		t.Errorf("legacy agent fields should be cleared after adoption: %+v", legacy.Agent)
	}
	if legacy.BaseURL != "https://keep.example.test" {
		t.Errorf("unrelated legacy fields must survive, got base_url %q", legacy.BaseURL)
	}
}

// TestMutateAgentStateNeverCreatesLegacy proves the store keeps localagent out
// of ~/.jentic entirely: mutating agent state on a machine with no legacy
// config must not conjure one into being.
func TestMutateAgentStateNeverCreatesLegacy(t *testing.T) {
	withAgentXDG(t)
	paths := Paths{Root: filepath.Join(t.TempDir(), "jentic-home")}

	if _, err := MutateAgentState(paths, func(s *AgentState) error {
		s.SameUserNoticeSeen = true
		return nil
	}); err != nil {
		t.Fatalf("MutateAgentState: %v", err)
	}
	if _, err := os.Stat(paths.ConfigPath()); !os.IsNotExist(err) {
		t.Errorf("legacy config must not be created (err=%v)", err)
	}
}

// TestMutateAgentStateReloadsBeforeApplying proves MutateAgentState does a fresh
// read UNDER the lock, so a mutation applied to a STALE in-memory state (loaded
// before another writer committed) doesn't clobber the committed change.
func TestMutateAgentStateReloadsBeforeApplying(t *testing.T) {
	withAgentXDG(t)
	paths := Paths{Root: t.TempDir()}
	if _, err := MutateAgentState(paths, func(s *AgentState) error {
		s.SetAgentAccount(AgentAccount{User: "a", AccountCreated: true, Enabled: true})
		return nil
	}); err != nil {
		t.Fatalf("seed: %v", err)
	}

	// Writer 1 commits grant A.
	if _, err := MutateAgentState(paths, func(s *AgentState) error {
		s.AddGrantedDir("/opt/a/A")
		return nil
	}); err != nil {
		t.Fatalf("mutate A: %v", err)
	}

	// Writer 2 adds grant B; its fn reloads the on-disk state first, so it sees
	// A and appends B rather than replacing.
	got, err := MutateAgentState(paths, func(s *AgentState) error {
		s.AddGrantedDir("/opt/a/B")
		return nil
	})
	if err != nil {
		t.Fatalf("mutate B: %v", err)
	}
	acct, _ := got.AgentAccount()
	if len(acct.GrantedDirs) != 2 {
		t.Fatalf("expected both grants to survive, got %v", acct.GrantedDirs)
	}
}

// TestMutateAgentStateConcurrentContention exercises the advisory lock under
// REAL contention: N goroutines — separate lock-file descriptors, exactly like
// N concurrent jentic processes — each mutate a distinct granted dir. The lock
// serialises the read-modify-write cycles, so ALL N grants must survive; any
// lost update means two writers interleaved inside their critical sections.
// Runs on every platform, so CI's Windows job exercises lock_windows.go too.
func TestMutateAgentStateConcurrentContention(t *testing.T) {
	withAgentXDG(t)
	paths := Paths{Root: t.TempDir()}
	if _, err := MutateAgentState(paths, func(s *AgentState) error {
		s.SetAgentAccount(AgentAccount{User: "a", AccountCreated: true, Enabled: true})
		return nil
	}); err != nil {
		t.Fatalf("seed: %v", err)
	}

	const writers = 16
	var wg sync.WaitGroup
	errs := make(chan error, writers)
	for i := range writers {
		wg.Add(1)
		go func() {
			defer wg.Done()
			_, err := MutateAgentState(paths, func(s *AgentState) error {
				s.AddGrantedDir(fmt.Sprintf("/opt/a/dir-%02d", i))
				return nil
			})
			errs <- err
		}()
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		if err != nil {
			t.Fatalf("concurrent MutateAgentState: %v", err)
		}
	}

	got, err := LoadAgentState(paths)
	if err != nil {
		t.Fatalf("reload: %v", err)
	}
	acct, _ := got.AgentAccount()
	if len(acct.GrantedDirs) != writers {
		t.Fatalf("lost update under contention: %d/%d grants survived: %v",
			len(acct.GrantedDirs), writers, acct.GrantedDirs)
	}
}

// TestMutateAgentStateErrorLeavesStateUntouched proves a failing mutation does
// not write.
func TestMutateAgentStateErrorLeavesStateUntouched(t *testing.T) {
	withAgentXDG(t)
	paths := Paths{Root: t.TempDir()}
	if _, err := MutateAgentState(paths, func(s *AgentState) error {
		s.SetAgentAccount(AgentAccount{User: "keep"})
		return nil
	}); err != nil {
		t.Fatalf("seed: %v", err)
	}

	if _, err := MutateAgentState(paths, func(s *AgentState) error {
		s.SetAgentAccount(AgentAccount{User: "clobbered"})
		return os.ErrInvalid
	}); err == nil {
		t.Fatal("expected MutateAgentState to propagate the fn error")
	}
	got, _ := LoadAgentState(paths)
	acct, _ := got.AgentAccount()
	if acct.User != "keep" {
		t.Errorf("failed mutation must not persist changes, got %q", acct.User)
	}
}

// TestLegacyAgentFieldsStillParse pins the read-side of the legacy schema:
// FileConfig must keep parsing agent_account/same_user_notice_seen (and expose
// the getter) so the fallback and `jentic migrate` keep working.
func TestLegacyAgentFieldsStillParse(t *testing.T) {
	paths := writeConfig(t, "agent_account:\n  user: bob-local-agent\n  config_dir: /opt/bob-local-agent/.jentic\nsame_user_notice_seen: true\n")
	cfg, err := Load(paths)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	acct, ok := cfg.AgentAccount()
	if !ok || acct.User != "bob-local-agent" || acct.ConfigDir != "/opt/bob-local-agent/.jentic" {
		t.Fatalf("legacy agent fields did not parse: %+v (ok=%v)", acct, ok)
	}
	if !cfg.SameUserNoticeSeen {
		t.Error("legacy same_user_notice_seen did not parse")
	}
}
