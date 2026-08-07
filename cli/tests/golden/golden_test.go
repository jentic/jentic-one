// Package golden holds the V1 characterization (golden) tests for the CLI
// (plan Phase 0, themes/cli-v2/impl/0.0 §2a, 16_landing_strategy.md §4).
//
// They freeze the shipped ("V1") CLI's observable contract — the off-TTY JSON
// envelope shapes, exit codes, and stderr error form of the core agent-facing
// commands — BEFORE any command is re-plumbed onto the V2 machinery. That makes
// "V1 keeps working" a CI property, not a reviewer promise, through every later
// migration phase: a golden diff is only mergeable when the same PR cites the
// breaking-change number (14_breaking_changes.md) that authorizes it.
//
// Drive model: each case builds the real `jentic` command tree via
// cmd.APITreeBuilder() and runs it through pkg/core.Run — the exact path the
// shipped binary uses — with stdout/stderr captured to buffers and a temp
// ~/.jentic. Output is recorded under testdata/golden/v1/<case>.txt; regenerate
// with `go test ./tests/golden -update`.
package golden

import (
	"bytes"
	"context"
	"flag"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/jentic/jentic-one/cli/internal/cmd"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/profile"
	"github.com/jentic/jentic-one/cli/pkg/core"
)

// update regenerates the golden files instead of comparing (`-update`).
var update = flag.Bool("update", false, "regenerate golden files under testdata/golden/v1")

// result is the observable contract of one CLI invocation.
type result struct {
	stdout   string
	stderr   string
	exitCode int
}

// runAPI builds the real jentic command tree against an injected container
// (buffered streams) and runs it through core.Run, returning the captured
// contract. The CLI home is redirected to `home` via $JENTIC_HOME (the shipped
// override), so no real ~/.jentic is touched. env adds/overrides further vars
// (e.g. JENTIC_MODE for agent-mode cases).
func runAPI(t *testing.T, home string, env map[string]string, args ...string) result {
	t.Helper()
	t.Setenv(config.HomeEnv, home)
	for k, v := range env {
		t.Setenv(k, v)
	}
	var out, errBuf bytes.Buffer
	deps := &core.AppContainer{
		In:  strings.NewReader(""),
		Out: &out,
		Err: &errBuf,
	}
	root := core.NewRootCmd(deps, cmd.APITreeBuilder())
	root.SetArgs(args)
	code := core.Run(root)
	return result{stdout: out.String(), stderr: errBuf.String(), exitCode: code}
}

// seedRegistered writes a registered profile with a cached, non-expired token
// pointed at baseURL under home, so profile-scoped commands resolve without any
// network round-trip — mirroring the shipped test helper of the same name.
func seedRegistered(t *testing.T, home, name, baseURL string) {
	t.Helper()
	paths := config.Paths{Root: home}
	p, err := profile.Open(paths, name)
	if err != nil {
		t.Fatalf("open profile: %v", err)
	}
	if err := p.SaveMeta(&profile.Meta{AgentID: "agnt_test", BaseURL: baseURL, KID: "k"}); err != nil {
		t.Fatalf("save meta: %v", err)
	}
	if err := p.SaveTokens(&profile.Tokens{AccessToken: "tok_abc", AccessExpiresAt: time.Now().Add(time.Hour)}); err != nil {
		t.Fatalf("save tokens: %v", err)
	}
}

// assertGolden compares (or, under -update, writes) the recorded contract.
func assertGolden(t *testing.T, name string, got result) {
	t.Helper()
	path := filepath.Join("testdata", "golden", "v1", name+".txt")
	rec := formatResult(got)
	if *update {
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatalf("mkdir golden dir: %v", err)
		}
		if err := os.WriteFile(path, []byte(rec), 0o644); err != nil {
			t.Fatalf("write golden: %v", err)
		}
		return
	}
	want, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read golden %s: %v (run `go test ./tests/golden -update` to create it)", path, err)
	}
	if string(want) != rec {
		t.Errorf("golden mismatch for %s.\nIf this change is intended, it MUST cite the authorizing BC number "+
			"(14_breaking_changes.md) in the PR, then run `go test ./tests/golden -update`.\n--- want ---\n%s\n--- got ---\n%s",
			name, string(want), rec)
	}
}

// formatResult renders the contract into a stable, diffable text block.
func formatResult(r result) string {
	var b strings.Builder
	b.WriteString("exit: ")
	b.WriteString(itoa(r.exitCode))
	b.WriteString("\n--- stdout ---\n")
	b.WriteString(r.stdout)
	if !strings.HasSuffix(r.stdout, "\n") {
		b.WriteString("\n")
	}
	b.WriteString("--- stderr ---\n")
	b.WriteString(r.stderr)
	if !strings.HasSuffix(r.stderr, "\n") {
		b.WriteString("\n")
	}
	return b.String()
}

func itoa(i int) string {
	// Small non-fmt helper to keep the recorder dependency-light.
	if i == 0 {
		return "0"
	}
	neg := i < 0
	if neg {
		i = -i
	}
	var buf [20]byte
	pos := len(buf)
	for i > 0 {
		pos--
		buf[pos] = byte('0' + i%10)
		i /= 10
	}
	if neg {
		pos--
		buf[pos] = '-'
	}
	return string(buf[pos:])
}

var _ = context.Background // reserved for cases that need an explicit context
