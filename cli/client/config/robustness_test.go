package config

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
)

// TestMutateConfigConcurrentContention is the V2 (client/config) port of the
// legacy 16-writer contention test (QA-6). MutateConfig does a read-modify-write
// under an advisory flock; without the lock, concurrent writers each adding a
// distinct environment would clobber one another's additions (lost update). We
// fire 16 goroutines each adding a uniquely-named environment and require ALL 16
// to survive the reload — proving the lock serialises the critical section and
// the yaml.Node merge does not drop concurrently-added entries.
func TestMutateConfigConcurrentContention(t *testing.T) {
	withEnvDirs(t)

	const writers = 16
	var wg sync.WaitGroup
	errs := make(chan error, writers)
	for i := range writers {
		wg.Add(1)
		go func() {
			defer wg.Done()
			name := fmt.Sprintf("env-%02d", i)
			errs <- MutateConfig(func(c *Config) error {
				c.Environments[name] = Env{BaseURL: fmt.Sprintf("https://e%02d.example.com", i)}
				return nil
			})
		}()
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		if err != nil {
			t.Fatalf("concurrent MutateConfig: %v", err)
		}
	}

	got, err := Load()
	if err != nil {
		t.Fatalf("reload: %v", err)
	}
	if len(got.Environments) != writers {
		names := make([]string, 0, len(got.Environments))
		for n := range got.Environments {
			names = append(names, n)
		}
		t.Fatalf("lost update under contention: %d/%d environments survived: %v",
			len(got.Environments), writers, names)
	}
}

// TestLoadStateRejectsCorruptConfig proves the V2 resolver fails CLOSED on a
// malformed config.yaml (QA-7): a truncated / non-YAML / wrong-typed document
// must surface a parse error rather than panicking or silently resolving to an
// empty (and thus misleadingly "unconfigured") state. Each case writes a broken
// file into the XDG config dir and asserts LoadState returns a non-nil error.
func TestLoadStateRejectsCorruptConfig(t *testing.T) {
	cases := map[string]string{
		"not yaml":                  "\x00\x01 this is: [not: valid: yaml",
		"scalar not mapping":        "just-a-string\n",
		"active_context wrong-type": "active_context: [1, 2, 3]\n",
		"tab-indent mapping":        "contexts:\n\tprod: {}\n", // tabs are illegal YAML indentation
	}
	for name, body := range cases {
		t.Run(name, func(t *testing.T) {
			dir := withEnvDirs(t)
			cfgDir := filepath.Join(dir, "config", "jentic")
			if err := os.MkdirAll(cfgDir, 0o700); err != nil {
				t.Fatalf("mkdir: %v", err)
			}
			if err := os.WriteFile(filepath.Join(cfgDir, "config.yaml"), []byte(body), 0o600); err != nil {
				t.Fatalf("write: %v", err)
			}
			_, err := LoadState("")
			if err == nil {
				t.Fatalf("LoadState on corrupt config %q = nil error, want a parse/type error", name)
			}
			// The error must be about the config, not a nil-deref stack — a bare
			// panic would fail the test process, so reaching here means it errored
			// gracefully; sanity-check the message references parsing/config.
			msg := strings.ToLower(err.Error())
			if !strings.Contains(msg, "config") && !strings.Contains(msg, "yaml") && !strings.Contains(msg, "decode") && !strings.Contains(msg, "unmarshal") {
				t.Errorf("LoadState error should reference the config/YAML problem, got: %v", err)
			}
		})
	}
}
