// Package golden holds the characterization (golden) tests for the CLI's
// agent-facing machine contract.
//
// What is frozen here is the V2 contract (14 BC-1: context-only resolution, no
// legacy ~/.jentic reads, no --profile/--base-url data-plane flags): the
// off-TTY JSON envelope shapes,
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
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/cli/api"
	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/pkg/core"
)

// update regenerates the golden files instead of comparing (`-update`).
var update = flag.Bool("update", false, "regenerate golden files under testdata/golden/v2")

// seenGoldens records every case name that assertGolden touched during the run
// (QA-22). TestMain walks the golden dir afterwards and fails on any .txt with
// no owning case — an orphan-guard so deleting a case under `-update` can't
// silently strand a stale golden. Guarded by a mutex because subtests can run
// in parallel.
var (
	seenMu     sync.Mutex
	seenGolden = map[string]bool{}
)

// TestMain runs the suite, then (unless -update) fails if any file under
// testdata/golden/v2 was not claimed by an assertGolden call. Under -update the
// on-disk set is authoritative (we are rewriting it), so the guard is skipped.
func TestMain(m *testing.M) {
	flag.Parse()
	code := m.Run()
	if code == 0 && !*update {
		if err := checkNoOrphanGoldens(); err != nil {
			fmt.Fprintln(os.Stderr, err)
			code = 1
		}
	}
	os.Exit(code)
}

// checkNoOrphanGoldens lists every *.txt under the golden dir and reports any
// whose case name was never exercised by assertGolden this run.
func checkNoOrphanGoldens() error {
	dir := filepath.Join("testdata", "golden", "v2")
	entries, err := os.ReadDir(dir)
	if err != nil {
		if os.IsNotExist(err) {
			return nil // no goldens yet is not an orphan condition
		}
		return fmt.Errorf("orphan-guard: read golden dir: %w", err)
	}
	var orphans []string
	seenMu.Lock()
	defer seenMu.Unlock()
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".txt") {
			continue
		}
		name := strings.TrimSuffix(e.Name(), ".txt")
		if !seenGolden[name] {
			orphans = append(orphans, e.Name())
		}
	}
	if len(orphans) > 0 {
		sort.Strings(orphans)
		return fmt.Errorf("orphan golden file(s) under %s with no owning test case: %s\n"+
			"Delete the stale file(s) or restore the case, then run `go test ./tests/golden -update`.",
			dir, strings.Join(orphans, ", "))
	}
	return nil
}

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

	// Capture the PROCESS streams, not just the AppContainer buffers. The UX
	// render layer (ux.Render / ux.ReportError) writes to os.Stdout/os.Stderr
	// directly by design — that confinement is enforced by the arch boundary
	// test (1F). So the agent contract an agent actually observes lands on the
	// process streams; capturing only deps.Out/Err would silently miss every
	// error envelope (and is exactly why the no-context golden looked empty).
	outR, outW, err := os.Pipe()
	if err != nil {
		t.Fatalf("stdout pipe: %v", err)
	}
	errR, errW, err := os.Pipe()
	if err != nil {
		t.Fatalf("stderr pipe: %v", err)
	}
	oldOut, oldErr := os.Stdout, os.Stderr
	os.Stdout, os.Stderr = outW, errW
	defer func() { os.Stdout, os.Stderr = oldOut, oldErr }()

	deps := &core.AppContainer{
		In:  strings.NewReader(""),
		Out: outW,
		Err: errW,
	}
	root := core.NewRootCmd(deps, api.TreeBuilder())
	root.SetArgs(args)
	// Thread the same invocation-error mapper the shipped binary uses (RunRoot →
	// core.RunTree), so cobra-native parse errors are recorded as the coded
	// envelope an agent actually sees, not the raw cobra text (AGT-20).
	code := core.RunTree(root, cmdcore.InvocationErrorMapper)

	_ = outW.Close()
	_ = errW.Close()
	var outBuf, errBuf bytes.Buffer
	_, _ = io.Copy(&outBuf, outR)
	_, _ = io.Copy(&errBuf, errR)
	return result{stdout: outBuf.String(), stderr: errBuf.String(), exitCode: code}
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
	seenMu.Lock()
	seenGolden[name] = true
	seenMu.Unlock()
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
