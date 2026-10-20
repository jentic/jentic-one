//go:build !windows

package api

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
	"github.com/jentic/jentic-one/cli/pkg/core"
)

// fakeCF is a stand-in `cf`: it writes its argv and environment to
// $FAKE_CF_OUT, optionally sleeps $FAKE_CF_SLEEP seconds, and exits
// $FAKE_CF_EXIT.
const fakeCF = `#!/bin/sh
{
  for a in "$@"; do printf 'arg=%s\n' "$a"; done
  env | sed 's/^/env=/'
} > "$FAKE_CF_OUT"
echo "cf stdout"
echo "cf stderr" >&2
if [ -n "$FAKE_CF_SLEEP" ]; then sleep "$FAKE_CF_SLEEP"; fi
exit "${FAKE_CF_EXIT:-0}"
`

const (
	testInvocationID = "cli_test1"
	testTraceID      = "4bf92f3577b34da6a3ce929d0e0e4736"
	testCitToken     = "cit_testinvocationtokenvalue0123456789abcdefghij"
)

// fakeControl serves the routes `jentic cf` calls and records what it saw.
type fakeControl struct {
	mu          sync.Mutex
	facades     string // GET /cli-facades body
	mintStatus  int
	expiresIn   time.Duration
	mintBody    map[string]any
	mintAuth    string
	renews      int
	revokes     int
	events      string // GET /events body
	executions  string // GET /executions body
	unknownPath []string
}

func newFakeControl(t *testing.T, address string) (*fakeControl, *httptest.Server) {
	t.Helper()
	fc := &fakeControl{
		facades:    `{"data":[{"vendor":"cloudflare","cli":"cf","mode":"path","address":"` + address + `"}]}`,
		mintStatus: http.StatusCreated,
		expiresIn:  10 * time.Minute,
		events:     `{"data":[],"has_more":false}`,
		executions: `{"data":[],"has_more":false}`,
	}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fc.mu.Lock()
		defer fc.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.Method == http.MethodGet && r.URL.Path == "/cli-facades":
			_, _ = io.WriteString(w, fc.facades)
		case r.Method == http.MethodPost && r.URL.Path == "/cli-invocations":
			fc.mintAuth = r.Header.Get("Authorization")
			body, _ := io.ReadAll(r.Body)
			_ = json.Unmarshal(body, &fc.mintBody)
			if fc.mintStatus != http.StatusCreated {
				w.Header().Set("Content-Type", "application/problem+json")
				w.WriteHeader(fc.mintStatus)
				_, _ = io.WriteString(w, `{"status":403,"title":"Forbidden","detail":"not an at_ token"}`)
				return
			}
			w.WriteHeader(http.StatusCreated)
			_ = json.NewEncoder(w).Encode(map[string]any{
				"id": testInvocationID, "vendor": "cloudflare", "trace_id": testTraceID,
				"token": testCitToken, "expires_at": time.Now().Add(fc.expiresIn).UTC().Format(time.RFC3339Nano),
				"hard_expires_at": time.Now().Add(time.Hour).UTC().Format(time.RFC3339Nano),
			})
		case r.Method == http.MethodPost && r.URL.Path == "/cli-invocations/"+testInvocationID+":renew":
			fc.renews++
			_ = json.NewEncoder(w).Encode(map[string]any{
				"id": testInvocationID, "vendor": "cloudflare", "trace_id": testTraceID,
				"expires_at":      time.Now().Add(fc.expiresIn).UTC().Format(time.RFC3339Nano),
				"hard_expires_at": time.Now().Add(time.Hour).UTC().Format(time.RFC3339Nano),
			})
		case r.Method == http.MethodPost && r.URL.Path == "/cli-invocations/"+testInvocationID+":revoke":
			fc.revokes++
			w.WriteHeader(http.StatusNoContent)
		case r.Method == http.MethodGet && r.URL.Path == "/events":
			if r.URL.Query().Get("trace_id") != testTraceID {
				t.Errorf("events read for trace %q", r.URL.Query().Get("trace_id"))
			}
			_, _ = io.WriteString(w, fc.events)
		case r.Method == http.MethodGet && r.URL.Path == "/executions":
			if r.URL.Query().Get("trace_id") != testTraceID {
				t.Errorf("executions read for trace %q", r.URL.Query().Get("trace_id"))
			}
			_, _ = io.WriteString(w, fc.executions)
		default:
			fc.unknownPath = append(fc.unknownPath, r.Method+" "+r.URL.Path)
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	t.Cleanup(srv.Close)
	return fc, srv
}

// cfRun is one `jentic cf` run's observable outcome.
type cfRun struct {
	code   int
	stdout string
	stderr string
	args   []string
	env    map[string]string
	ran    bool
}

// installFakeCF puts the fake `cf` first on PATH and returns where it reports.
func installFakeCF(t *testing.T) string {
	t.Helper()
	bin := t.TempDir()
	if err := os.WriteFile(filepath.Join(bin, "cf"), []byte(fakeCF), 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", bin+string(os.PathListSeparator)+os.Getenv("PATH"))
	out := filepath.Join(t.TempDir(), "cf.out")
	t.Setenv("FAKE_CF_OUT", out)
	return out
}

// runCf runs the full `jentic` tree through core.RunTree, as the binary does,
// so the exit code is the process's.
func runCf(t *testing.T, srv *httptest.Server, report string, args ...string) cfRun {
	t.Helper()
	app := testApp(t)
	seedRegistered(t, app, "default", srv.URL)
	t.Setenv("JENTIC_BEARER_TOKEN", "at_parentaccesstoken")
	t.Setenv("JENTIC_MODE", "agent")
	var stdout, stderr bytes.Buffer
	app.Out, app.Err = &stdout, &stderr
	root := core.NewRootCmd(&core.AppContainer{In: strings.NewReader(""), Out: &stdout, Err: &stderr},
		func(*core.AppContainer) *cobra.Command { return newAPIRootCmd(app.App) })
	root.SetArgs(args)
	var code int
	rendered := captureStdout(t, func() { code = core.RunTree(root, cmdcore.InvocationErrorMapper) })

	run := cfRun{code: code, stdout: stdout.String() + rendered, stderr: stderr.String(), env: map[string]string{}}
	data, err := os.ReadFile(report)
	if err == nil {
		run.ran = true
		for _, line := range strings.Split(string(data), "\n") {
			if a, ok := strings.CutPrefix(line, "arg="); ok {
				run.args = append(run.args, a)
			} else if kv, ok := strings.CutPrefix(line, "env="); ok {
				k, v, _ := strings.Cut(kv, "=")
				run.env[k] = v
			}
		}
	}
	return run
}

// directiveLine parses the single `jentic: {…}` stderr line.
func directiveLine(t *testing.T, stderr string) ux.VendorCLIDirective {
	t.Helper()
	var found []string
	for _, line := range strings.Split(stderr, "\n") {
		if rest, ok := strings.CutPrefix(line, ux.VendorCLIDirectivePrefix); ok {
			found = append(found, rest)
		}
	}
	if len(found) != 1 {
		t.Fatalf("want exactly one directive line on stderr, got %d:\n%s", len(found), stderr)
	}
	var d ux.VendorCLIDirective
	if err := json.Unmarshal([]byte(found[0]), &d); err != nil {
		t.Fatalf("directive line is not JSON: %v\n%s", err, found[0])
	}
	return d
}

func TestCf_PassesArgsVerbatimAndSetsTheFacadeEnv(t *testing.T) {
	withXDG(t)
	report := installFakeCF(t)
	fc, srv := newFakeControl(t, "http://broker.test/_facade/cloudflare")
	t.Setenv("CLOUDFLARE_API_KEY", "legacy-key")
	t.Setenv("CLOUDFLARE_EMAIL", "me@example.com")
	t.Setenv("CF_API_TOKEN", "old-token")
	t.Setenv("CLOUDFLARE_API_TOKEN", "users-own-token")
	t.Setenv("CLOUDFLARE_ACCOUNT_ID", "acc_1")
	t.Setenv("SOME_OTHER", "at_lookslikeajentictoken")

	args := []string{"zones", "list", "--name", "a b.com", "--json", "--", "-x", "--mode=agent"}
	run := runCf(t, srv, report, append([]string{"cf"}, args...)...)

	if run.code != 0 {
		t.Fatalf("exit = %d, stderr:\n%s", run.code, run.stderr)
	}
	if strings.Join(run.args, "\x00") != strings.Join(args, "\x00") {
		t.Errorf("cf argv = %q, want %q", run.args, args)
	}
	if got := run.env["CLOUDFLARE_API_BASE_URL"]; got != "http://broker.test/_facade/cloudflare" {
		t.Errorf("CLOUDFLARE_API_BASE_URL = %q", got)
	}
	if got := run.env["CLOUDFLARE_API_TOKEN"]; got != testCitToken {
		t.Errorf("CLOUDFLARE_API_TOKEN = %q, want the invocation token", got)
	}
	for _, k := range []string{"CLOUDFLARE_API_KEY", "CLOUDFLARE_EMAIL", "CF_API_TOKEN", "JENTIC_BEARER_TOKEN", "SOME_OTHER"} {
		if v, ok := run.env[k]; ok {
			t.Errorf("%s reached cf (%q)", k, v)
		}
	}
	for k, v := range run.env {
		if strings.HasPrefix(v, "at_") || strings.Contains(v, "at_parentaccesstoken") {
			t.Errorf("an access token reached cf in %s", k)
		}
	}
	if run.env["CLOUDFLARE_ACCOUNT_ID"] != "acc_1" {
		t.Error("non-credential env must pass through")
	}
	if !strings.Contains(run.stdout, "cf stdout") || !strings.Contains(run.stderr, "cf stderr") {
		t.Errorf("cf's streams must pass through: stdout=%q stderr=%q", run.stdout, run.stderr)
	}
	if strings.Contains(run.stderr, ux.VendorCLIDirectivePrefix) {
		t.Errorf("a successful run prints no directive:\n%s", run.stderr)
	}

	fc.mu.Lock()
	defer fc.mu.Unlock()
	if fc.mintBody["vendor"] != "cloudflare" || fc.mintBody["command_title"] != "zones list" {
		t.Errorf("mint body = %v", fc.mintBody)
	}
	if _, ok := fc.mintBody["credential_name"]; ok {
		t.Errorf("no JENTIC_CREDENTIAL, so no credential_name: %v", fc.mintBody)
	}
	if fc.mintAuth != "Bearer at_parentaccesstoken" {
		t.Errorf("mint must use the agent's own access token, got %q", fc.mintAuth)
	}
	if fc.revokes != 1 {
		t.Errorf("revokes = %d, want 1", fc.revokes)
	}
	if len(fc.unknownPath) > 0 {
		t.Errorf("unexpected calls: %v", fc.unknownPath)
	}
}

func TestCf_ForwardsJenticCredential(t *testing.T) {
	withXDG(t)
	report := installFakeCF(t)
	fc, srv := newFakeControl(t, "http://broker.test/_facade/cloudflare")
	t.Setenv("JENTIC_CREDENTIAL", "Cloudflare prod")

	run := runCf(t, srv, report, "cf", "dns", "records", "list")

	if run.code != 0 {
		t.Fatalf("exit = %d:\n%s", run.code, run.stderr)
	}
	fc.mu.Lock()
	defer fc.mu.Unlock()
	if fc.mintBody["credential_name"] != "Cloudflare prod" || fc.mintBody["command_title"] != "dns records list" {
		t.Errorf("mint body = %v", fc.mintBody)
	}
}

func TestCf_ExitCodePassesThroughWithATraceDirective(t *testing.T) {
	withXDG(t)
	report := installFakeCF(t)
	fc, srv := newFakeControl(t, "http://broker.test/_facade/cloudflare")
	fc.events = `{"data":[{"event_id":"evt_1","type":"broker.pbac_denied","severity":"warning",
		"summary":"Denied DELETE /zones/{zone_id}","requires_action":false,"created_at":"2026-10-20T10:00:00Z",
		"trace_id":"` + testTraceID + `","data":{"reason":"action_denied","operation_id":"deleteZone"},
		"_links":{"self":"/events/evt_1"}}],"has_more":false}`
	t.Setenv("FAKE_CF_EXIT", "42")

	run := runCf(t, srv, report, "cf", "zones", "delete", "z1")

	if run.code != 42 {
		t.Fatalf("exit = %d, want cf's 42; stderr:\n%s", run.code, run.stderr)
	}
	d := directiveLine(t, run.stderr)
	if d.TraceID != testTraceID {
		t.Errorf("trace_id = %q", d.TraceID)
	}
	if d.Directive.Strategy != "prompt_human" || !strings.Contains(d.Directive.Instruction, "denied") {
		t.Errorf("directive = %+v", d.Directive)
	}
	if d.Directive.Parameters["operation_id"] != "deleteZone" {
		t.Errorf("parameters = %v", d.Directive.Parameters)
	}
	if strings.Contains(run.stderr, `"error_code"`) {
		t.Errorf("a vendor failure must not add a Jentic error envelope:\n%s", run.stderr)
	}
	fc.mu.Lock()
	defer fc.mu.Unlock()
	if fc.revokes != 1 {
		t.Errorf("revokes = %d, want 1 (revoke on a failed exit too)", fc.revokes)
	}
}

func TestCf_FailedExecutionDirectiveAndNoRecordDirective(t *testing.T) {
	withXDG(t)
	report := installFakeCF(t)
	fc, srv := newFakeControl(t, "http://broker.test/_facade/cloudflare")
	t.Setenv("FAKE_CF_EXIT", "1")

	run := runCf(t, srv, report, "cf", "zones", "list")
	if run.code != 1 {
		t.Fatalf("exit = %d", run.code)
	}
	if d := directiveLine(t, run.stderr); !strings.Contains(d.Directive.Instruction, "recorded no call") {
		t.Errorf("no records → %+v", d.Directive)
	}

	fc.mu.Lock()
	fc.executions = `{"data":[{"execution_id":"exe_1","trace_id":"` + testTraceID + `","status":"completed",
		"http_status":403,"operation_id":"listZones","actor_id":"agt_1","actor_type":"agent",
		"started_at":"2026-10-20T10:00:00Z","created_at":"2026-10-20T10:00:00Z",
		"_links":{"self":"/executions/exe_1"}}],"has_more":false}`
	fc.mu.Unlock()
	run = runCf(t, srv, report, "cf", "zones", "list")
	d := directiveLine(t, run.stderr)
	if d.Directive.Strategy != "fatal" || d.Directive.Parameters["execution_id"] != "exe_1" ||
		!strings.Contains(d.Directive.Instruction, "HTTP 403") {
		t.Errorf("failed execution → %+v", d.Directive)
	}
}

func TestCf_RenewsOnAHeartbeatWhileCfRuns(t *testing.T) {
	withXDG(t)
	report := installFakeCF(t)
	fc, srv := newFakeControl(t, "http://broker.test/_facade/cloudflare")
	fc.expiresIn = 300 * time.Millisecond // heartbeat every ~100ms
	t.Setenv("FAKE_CF_SLEEP", "1")

	run := runCf(t, srv, report, "cf", "zones", "list")

	if run.code != 0 {
		t.Fatalf("exit = %d:\n%s", run.code, run.stderr)
	}
	fc.mu.Lock()
	defer fc.mu.Unlock()
	if fc.renews < 3 {
		t.Errorf("renews = %d over a 1s run with a 300ms TTL, want several", fc.renews)
	}
	if fc.revokes != 1 {
		t.Errorf("revokes = %d", fc.revokes)
	}
}

func TestCf_FacadeNotAvailableDoesNotRunCf(t *testing.T) {
	withXDG(t)
	report := installFakeCF(t)
	fc, srv := newFakeControl(t, "")
	fc.facades = `{"data":[]}`

	run := runCf(t, srv, report, "cf", "zones", "list")

	if run.ran {
		t.Fatal("cf ran although no Cloudflare facade is served")
	}
	if run.code != ux.ExitError {
		t.Errorf("exit = %d, want %d", run.code, ux.ExitError)
	}
	d := directiveLine(t, run.stderr)
	if d.Directive.Strategy != "prompt_human" || !strings.Contains(d.Directive.Instruction, "not enabled") {
		t.Errorf("directive = %+v", d.Directive)
	}
	fc.mu.Lock()
	defer fc.mu.Unlock()
	if fc.mintBody != nil {
		t.Error("no token may be minted when the facade is not available")
	}
}

func TestCf_MintRefusedDoesNotRunCf(t *testing.T) {
	withXDG(t)
	report := installFakeCF(t)
	fc, srv := newFakeControl(t, "http://broker.test/_facade/cloudflare")
	fc.mintStatus = http.StatusForbidden

	run := runCf(t, srv, report, "cf", "zones", "list")

	if run.ran {
		t.Fatal("cf ran without an invocation token")
	}
	d := directiveLine(t, run.stderr)
	if !strings.Contains(d.Directive.Instruction, "not an API key") {
		t.Errorf("directive = %+v", d.Directive)
	}
}

func TestCf_MissingBinary(t *testing.T) {
	withXDG(t)
	_, srv := newFakeControl(t, "http://broker.test/_facade/cloudflare")
	t.Setenv("PATH", t.TempDir())

	run := runCf(t, srv, filepath.Join(t.TempDir(), "none"), "cf", "zones", "list")

	if run.code != ux.ExitError {
		t.Errorf("exit = %d", run.code)
	}
	if d := directiveLine(t, run.stderr); !strings.Contains(d.Directive.Instruction, "not installed") {
		t.Errorf("directive = %+v", d.Directive)
	}
}

func TestCliRun_IsTheGenericFormOfCf(t *testing.T) {
	withXDG(t)
	report := installFakeCF(t)
	_, srv := newFakeControl(t, "http://broker.test/_facade/cloudflare")

	run := runCf(t, srv, report, "cli", "run", "cf", "--", "zones", "list", "--json")

	if run.code != 0 || strings.Join(run.args, " ") != "zones list --json" {
		t.Errorf("exit = %d, args = %q\n%s", run.code, run.args, run.stderr)
	}
}

func TestCliList_RendersTheFacades(t *testing.T) {
	withXDG(t)
	_, srv := newFakeControl(t, "http://broker.test/_facade/cloudflare")

	run := runCf(t, srv, filepath.Join(t.TempDir(), "none"), "cli", "list")

	if run.code != 0 {
		t.Fatalf("exit = %d:\n%s", run.code, run.stderr)
	}
	var got struct {
		Data []map[string]any `json:"data"`
	}
	if err := json.Unmarshal([]byte(run.stdout), &got); err != nil {
		t.Fatalf("stdout is not JSON: %v\n%s", err, run.stdout)
	}
	if len(got.Data) != 1 || got.Data[0]["cli"] != "cf" || got.Data[0]["address"] != "http://broker.test/_facade/cloudflare" {
		t.Errorf("list = %v", got.Data)
	}
}

func TestFindVendorBinary_SkipsTheShimDir(t *testing.T) {
	shim, realDir := t.TempDir(), t.TempDir()
	for _, dir := range []string{shim, realDir} {
		if err := os.WriteFile(filepath.Join(dir, "cf"), []byte("#!/bin/sh\n"), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	t.Setenv("PATH", shim+string(os.PathListSeparator)+realDir)
	t.Setenv(envShimDir, shim)

	got, err := findVendorBinary("cf")
	if err != nil {
		t.Fatal(err)
	}
	if want := filepath.Join(realDir, "cf"); cleanDir(filepath.Dir(got)) != cleanDir(filepath.Dir(want)) {
		t.Errorf("found %q, want the one in %q", got, realDir)
	}
}

func TestCommandTitle_StopsAtTheFirstFlagOrValue(t *testing.T) {
	cases := map[string][]string{
		"zones list":       {"zones", "list", "--name", "x"},
		"dns records list": {"dns", "records", "list"},
		"":                 {"--json", "zones"},
		"zones":            {"zones", "a b.com"},
		"zones delete":     {"zones", "delete", "z1"},
		"zones get":        {"zones", "get", "example.com"},
		"a b c d":          {"a", "b", "c", "d", "e"},
	}
	for want, args := range cases {
		if got := commandTitle(args); got != want {
			t.Errorf("commandTitle(%q) = %q, want %q", args, got, want)
		}
	}
}

func TestForwardSignals_RelaysTerminationToTheChild(t *testing.T) {
	child := exec.Command("sleep", "30")
	if err := child.Start(); err != nil {
		t.Fatal(err)
	}
	stop := forwardSignals(child.Process, false)
	defer stop()

	if err := syscall.Kill(os.Getpid(), syscall.SIGHUP); err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() { done <- child.Wait() }()
	select {
	case err := <-done:
		code, cerr := childExitCode(err)
		if cerr != nil || code != 128+int(syscall.SIGHUP) {
			t.Errorf("child exit = %d (%v), want %d", code, cerr, 128+int(syscall.SIGHUP))
		}
	case <-time.After(5 * time.Second):
		_ = child.Process.Kill()
		t.Fatal("SIGHUP was not forwarded to the child")
	}
}
