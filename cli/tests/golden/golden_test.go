// Package golden holds the characterization (golden) tests for the CLI's
// agent-facing machine contract.
//
// HISTORY: this suite was introduced in Phase 0 to freeze the shipped V1
// contract through the re-plumbing phases. The activation release then removed
// the V1 surface outright (14 BC-1: context-only resolution, no legacy
// ~/.jentic reads, no --profile/--base-url data-plane flags) — the authorized
// breaking change that retired the V1 goldens. What remains frozen here is the
// V2 contract the same commands now expose: the off-TTY JSON envelope shapes,
// exit codes, and stderr error form that agents parse.
//
// Drive model: each case builds the real `jentic` command tree via
// api.TreeBuilder() and runs it through pkg/core.Run — the exact path the
// shipped binary uses — with stdout/stderr captured to buffers, an isolated
// $JENTIC_HOME, and the file-less env session (JENTIC_BASE_URL +
// JENTIC_BEARER_TOKEN) standing in for a configured context. Output is
// recorded under testdata/golden/v2/<case>.txt; regenerate with
// `go test ./tests/golden -update`.
package golden

import (
	"bytes"
	"flag"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/cli/api"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/pkg/core"
)

// update regenerates the golden files instead of comparing (`-update`).
var update = flag.Bool("update", false, "regenerate golden files under testdata/golden/v2")

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
	// Isolate the XDG store too: the V2 resolver reads ~/.config/jentic when
	// the env session vars are absent, and a developer's real config must
	// never leak into a recorded contract.
	t.Setenv("XDG_CONFIG_HOME", filepath.Join(home, "xdg-config"))
	t.Setenv("XDG_STATE_HOME", filepath.Join(home, "xdg-state"))
	for k, v := range env {
		t.Setenv(k, v)
	}
	var out, errBuf bytes.Buffer
	deps := &core.AppContainer{
		In:  strings.NewReader(""),
		Out: &out,
		Err: &errBuf,
	}
	root := core.NewRootCmd(deps, api.TreeBuilder())
	root.SetArgs(args)
	code := core.Run(root)
	return result{stdout: out.String(), stderr: errBuf.String(), exitCode: code}
}

// seedSession points the run at baseURL via the file-less env override — the
// V2 stand-in for the old registered-profile seeder: commands resolve an
// authenticated session without any network or disk config.
func seedSession(t *testing.T, baseURL string) {
	t.Helper()
	t.Setenv("JENTIC_BASE_URL", baseURL)
	t.Setenv("JENTIC_BEARER_TOKEN", "tok_abc")
}

// assertGolden compares (or, under -update, writes) the recorded contract.
func assertGolden(t *testing.T, name string, got result) {
	t.Helper()
	path := filepath.Join("testdata", "golden", "v2", name+".txt")
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
