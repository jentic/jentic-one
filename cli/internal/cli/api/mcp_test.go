package api

import (
	"context"
	"errors"
	"net/http"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/jentic/jentic-one/cli/client/generated/control"
	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// newTestMCPServer builds an mcpServer with the network seam stubbed: fetch
// answers GET /instance from memory, so no test dials anything.
func newTestMCPServer(t *testing.T, opts *mcpOptions) *mcpServer {
	t.Helper()
	if opts == nil {
		opts = &mcpOptions{}
	}
	s := newMCPServer(&app{App: &cmdcore.App{}}, "0.0.0-test", opts, discardLogger())
	s.instances.fetch = func(context.Context) (*control.InstanceIdentityResponse, error) {
		return nil, errors.New("no instance in this test")
	}
	return s
}

// --- diagnoseSetup branch selection ------------------------------------------

func TestDiagnoseSetup_Branches(t *testing.T) {
	probeFail := errors.New("dial tcp 127.0.0.1:8000: connect: connection refused")
	cases := []struct {
		name            string
		probe           setupProbe
		wantState       string
		wantInstruction []string // substrings the instruction must carry
	}{
		{
			name:      "no context at all",
			probe:     setupProbe{},
			wantState: setupNoConfig,
			// Mirrors the V2 no-config error: register command or the env-var pair.
			wantInstruction: []string{"jentic register --url", "JENTIC_BASE_URL", "JENTIC_BEARER_TOKEN"},
		},
		{
			name:            "context without base_url",
			probe:           setupProbe{hasContext: true, environment: "prod"},
			wantState:       setupNoConfig,
			wantInstruction: []string{"base_url", "jentic env add"},
		},
		{
			name:      "not registered",
			probe:     setupProbe{hasContext: true, baseURL: "http://127.0.0.1:8000", identity: "dev", environment: "local"},
			wantState: setupNotRegistered,
			// Mirrors the skill's step-1 branch, including the 127.0.0.1 audience note.
			wantInstruction: []string{"jentic register --url", "approve the agent", "http://127.0.0.1:8000"},
		},
		{
			name: "config read error surfaces its cause, not a re-register",
			probe: setupProbe{
				hasContext: true, baseURL: "http://127.0.0.1:8000", identity: "dev", environment: "local",
				configErr: errors.New("open config.yaml: permission denied"),
			},
			wantState: setupNotRegistered,
			// The cause is surfaced and the instruction routes to fixing the
			// file — an unreadable config must not prescribe `jentic register`.
			wantInstruction: []string{"could not be read", "permission denied", "Re-registering will not help"},
		},
		{
			name: "registered but pending",
			probe: setupProbe{
				hasContext: true, baseURL: "http://127.0.0.1:8000", identity: "dev", environment: "local",
				registered: true, regStatus: "pending",
			},
			wantState:       setupPendingApproval,
			wantInstruction: []string{"approve"},
		},
		{
			name: "approved but local instance down names jenticctl start",
			probe: setupProbe{
				hasContext: true, baseURL: "http://127.0.0.1:8000", identity: "dev", environment: "local",
				registered: true, regStatus: "approved", probed: true, probeErr: probeFail,
			},
			wantState: setupInstanceUnreachable,
			// The same-host fix is named verbatim (never auto-started).
			wantInstruction: []string{"jenticctl start", "jenticctl status"},
		},
		{
			name: "approved but remote instance down points at the operator runbook",
			probe: setupProbe{
				hasContext: true, baseURL: "https://jentic.example.com", identity: "dev", environment: "prod",
				registered: true, regStatus: "approved", probed: true, probeErr: probeFail,
			},
			wantState:       setupInstanceUnreachable,
			wantInstruction: []string{"https://jentic.example.com", "operator"},
		},
		{
			name: "file-less token skips registration and reaches ready",
			probe: setupProbe{
				hasContext: true, baseURL: "http://127.0.0.1:8000", identity: "env", environment: "env",
				fileless: true, probed: true,
			},
			wantState:       setupReady,
			wantInstruction: []string{"whoami"},
		},
		{
			name: "api key skips registration and reaches ready",
			probe: setupProbe{
				hasContext: true, baseURL: "http://127.0.0.1:8000", identity: "dev", environment: "local",
				hasAPIKey: true, probed: true,
			},
			wantState: setupReady,
		},
		{
			name: "approved and reachable is ready",
			probe: setupProbe{
				hasContext: true, baseURL: "http://127.0.0.1:8000", identity: "dev", environment: "local",
				registered: true, regStatus: "approved", probed: true,
			},
			wantState: setupReady,
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			d := diagnoseSetup(tc.probe)
			if d.State != tc.wantState {
				t.Fatalf("state = %q, want %q (summary %q)", d.State, tc.wantState, d.Summary)
			}
			for _, want := range tc.wantInstruction {
				if !strings.Contains(d.Instruction, want) {
					t.Errorf("instruction %q missing %q", d.Instruction, want)
				}
			}
			if d.Instruction == "" || d.Summary == "" {
				t.Errorf("branch %q must carry a summary and an instruction", tc.wantState)
			}
		})
	}
}

// TestDiagnoseSetup_NeverStartsInstance pins the §3.3 decision at the wording
// level: no branch's instruction tells the MODEL to start the instance itself —
// the unreachable branch instructs the OPERATOR (via `jenticctl start`).
func TestDiagnoseSetup_UnreachableInstructsOperatorNotAgent(t *testing.T) {
	d := diagnoseSetup(setupProbe{
		hasContext: true, baseURL: "http://localhost:8000", identity: "dev", environment: "local",
		registered: true, regStatus: "approved", probed: true, probeErr: errors.New("connection refused"),
	})
	if !strings.Contains(d.Instruction, "Ask your operator") {
		t.Errorf("unreachable instruction must route through the operator, got %q", d.Instruction)
	}
	if !strings.Contains(d.Instruction, "never starts or stops the instance") {
		t.Errorf("unreachable instruction must state the no-auto-start decision, got %q", d.Instruction)
	}
}

func TestLoopbackURL(t *testing.T) {
	cases := map[string]bool{
		"http://127.0.0.1:8000":      true,
		"http://localhost:8000":      true,
		"http://[::1]:8000":          true,
		"https://jentic.example.com": false,
		"https://10.1.2.3":           false,
		"not a url ://":              false,
	}
	for in, want := range cases {
		if got := loopbackURL(in); got != want {
			t.Errorf("loopbackURL(%q) = %v, want %v", in, got, want)
		}
	}
}

// --- instance stamp (fresh / degraded) ---------------------------------------

func TestInstanceStamp_FreshAndTTL(t *testing.T) {
	now := time.Date(2026, 8, 28, 12, 0, 0, 0, time.UTC)
	id := "digest-1"
	fetches := 0
	c := &instanceCache{
		ttl: time.Minute,
		now: func() time.Time { return now },
		fetch: func(context.Context) (*control.InstanceIdentityResponse, error) {
			fetches++
			return &control.InstanceIdentityResponse{Backend: "local", Host: "127.0.0.1:8000", InstanceId: &id}, nil
		},
	}

	stamp := c.stamp(context.Background())
	if stamp["backend"] != "local" || stamp["host"] != "127.0.0.1:8000" || stamp["instance_id"] != "digest-1" {
		t.Fatalf("fresh stamp = %#v", stamp)
	}
	if stamp["fetched_at"] != now.Format(time.RFC3339) {
		t.Errorf("fetched_at = %v, want the real fetch time %s", stamp["fetched_at"], now.Format(time.RFC3339))
	}

	// Within the TTL the cache answers without re-fetching.
	c.stamp(context.Background())
	if fetches != 1 {
		t.Errorf("fetches = %d, want 1 (TTL cache must hold)", fetches)
	}

	// Past the TTL it re-fetches.
	now = now.Add(2 * time.Minute)
	c.stamp(context.Background())
	if fetches != 2 {
		t.Errorf("fetches = %d, want 2 (TTL expiry must re-fetch)", fetches)
	}
}

func TestInstanceStamp_DegradedKeepsLastKnownAndRealFetchedAt(t *testing.T) {
	fetchTime := time.Date(2026, 8, 28, 12, 0, 0, 0, time.UTC)
	now := fetchTime
	id := "digest-1"
	healthy := true
	c := &instanceCache{
		ttl: time.Minute,
		now: func() time.Time { return now },
		fetch: func(context.Context) (*control.InstanceIdentityResponse, error) {
			if !healthy {
				return nil, errors.New("connection refused")
			}
			return &control.InstanceIdentityResponse{Backend: "local", Host: "127.0.0.1:8000", InstanceId: &id}, nil
		},
	}
	c.stamp(context.Background()) // prime the last-known identity

	healthy = false
	now = now.Add(5 * time.Minute) // TTL expired, refresh fails
	stamp := c.stamp(context.Background())
	if stamp["backend"] != backendUnreachable {
		t.Fatalf("backend = %v, want %q", stamp["backend"], backendUnreachable)
	}
	if stamp["host"] != "127.0.0.1:8000" || stamp["instance_id"] != "digest-1" {
		t.Errorf("degraded stamp must keep the last-known identity, got %#v", stamp)
	}
	if stamp["fetched_at"] != fetchTime.Format(time.RFC3339) {
		t.Errorf("fetched_at = %v, want the REAL last-success time %s (staleness is the signal)",
			stamp["fetched_at"], fetchTime.Format(time.RFC3339))
	}
}

func TestInstanceStamp_DegradedWithNoHistory(t *testing.T) {
	c := &instanceCache{
		ttl: time.Minute,
		now: time.Now,
		fetch: func(context.Context) (*control.InstanceIdentityResponse, error) {
			return nil, errors.New("no config")
		},
	}
	stamp := c.stamp(context.Background())
	if stamp["backend"] != backendUnreachable {
		t.Fatalf("backend = %v, want %q", stamp["backend"], backendUnreachable)
	}
	if stamp["host"] != "" || stamp["instance_id"] != nil || stamp["fetched_at"] != nil {
		t.Errorf("never-seen degraded stamp must be empty/null, got %#v", stamp)
	}
}

func TestInstanceProbe_ForcesRefresh(t *testing.T) {
	fetches := 0
	c := &instanceCache{
		ttl: time.Hour,
		now: time.Now,
		fetch: func(context.Context) (*control.InstanceIdentityResponse, error) {
			fetches++
			return &control.InstanceIdentityResponse{Backend: "local", Host: "h"}, nil
		},
	}
	if err := c.probe(context.Background()); err != nil {
		t.Fatalf("probe: %v", err)
	}
	if err := c.probe(context.Background()); err != nil {
		t.Fatalf("probe: %v", err)
	}
	if fetches != 2 {
		t.Errorf("fetches = %d, want 2 (probe must ignore the TTL — it IS the reachability check)", fetches)
	}
}

// --- instance cache concurrency (fetch must run OUTSIDE the mutex) -----------

// TestInstanceStamp_ConcurrentRefreshesShareOneFetch pins the singleflight
// contract: N tool calls arriving on an expired cache produce ONE wire call,
// and every caller gets the winner's result.
func TestInstanceStamp_ConcurrentRefreshesShareOneFetch(t *testing.T) {
	var fetches atomic.Int32
	release := make(chan struct{})
	c := &instanceCache{
		ttl:        time.Minute,
		failureTTL: time.Second,
		now:        time.Now,
		fetch: func(context.Context) (*control.InstanceIdentityResponse, error) {
			fetches.Add(1)
			<-release
			return &control.InstanceIdentityResponse{Backend: "local", Host: "127.0.0.1:8000"}, nil
		},
	}

	const callers = 16
	var wg sync.WaitGroup
	results := make([]map[string]any, callers)
	for i := range callers {
		wg.Add(1)
		go func() {
			defer wg.Done()
			results[i] = c.stamp(context.Background())
		}()
	}
	// Let callers pile up on the single in-flight fetch, then let it finish.
	// (Callers arriving after the release hit the fresh cache — same result,
	// still one fetch.)
	time.Sleep(20 * time.Millisecond)
	close(release)
	wg.Wait()

	if got := fetches.Load(); got != 1 {
		t.Errorf("fetches = %d, want 1 (concurrent refreshes must share one wire call)", got)
	}
	for i, stamp := range results {
		if stamp["backend"] != "local" {
			t.Errorf("caller %d stamp = %#v, want the shared winner's identity", i, stamp)
		}
	}
}

// TestInstanceStamp_FreshCacheAnswersWhileRefreshHangs pins the head-of-line
// fix: a tool call that can answer from the fresh cache must NOT queue behind
// a hung force-refresh (get_started probing a black-holed control plane).
func TestInstanceStamp_FreshCacheAnswersWhileRefreshHangs(t *testing.T) {
	started := make(chan struct{})
	release := make(chan struct{})
	var primed atomic.Bool
	c := &instanceCache{
		ttl:        time.Hour,
		failureTTL: time.Second,
		now:        time.Now,
		fetch: func(context.Context) (*control.InstanceIdentityResponse, error) {
			if primed.Load() {
				close(started)
				<-release // the hung dial
			}
			return &control.InstanceIdentityResponse{Backend: "local", Host: "h"}, nil
		},
	}
	c.stamp(context.Background()) // prime the cache
	primed.Store(true)

	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		_ = c.probe(context.Background()) // force-refresh: hangs on release
	}()
	<-started

	done := make(chan map[string]any, 1)
	go func() { done <- c.stamp(context.Background()) }()
	select {
	case stamp := <-done:
		if stamp["backend"] != "local" {
			t.Errorf("cache-hit stamp = %#v, want the fresh cached identity", stamp)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("a cache-hit stamp blocked behind an in-flight refresh (mutex held across network I/O)")
	}
	close(release)
	wg.Wait()
}

// TestInstanceStamp_WaiterHonorsItsOwnDeadline pins that a caller sharing an
// in-flight fetch stops waiting when ITS context ends — a hung leader dial
// must cost a waiter at most its own budget, then degrade.
func TestInstanceStamp_WaiterHonorsItsOwnDeadline(t *testing.T) {
	started := make(chan struct{})
	release := make(chan struct{})
	c := &instanceCache{
		ttl:        time.Minute,
		failureTTL: time.Second,
		now:        time.Now,
		fetch: func(context.Context) (*control.InstanceIdentityResponse, error) {
			close(started)
			<-release
			return nil, errors.New("black-holed")
		},
	}
	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		_ = c.stamp(context.Background()) // leader: hangs on release
	}()
	<-started

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Millisecond)
	defer cancel()
	done := make(chan map[string]any, 1)
	go func() { done <- c.stamp(ctx) }()
	select {
	case stamp := <-done:
		if stamp["backend"] != backendUnreachable {
			t.Errorf("expired-waiter stamp backend = %v, want %q (degraded form)", stamp["backend"], backendUnreachable)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("a waiter ignored its own context deadline while sharing an in-flight fetch")
	}
	close(release)
	wg.Wait()
}

// TestInstanceStamp_RecentFailureShortCircuitsRedial pins the negative cache:
// get_started's probe→stamp sequence dials once per call, and a down control
// plane is not re-dialed by every tool call inside the failure window.
func TestInstanceStamp_RecentFailureShortCircuitsRedial(t *testing.T) {
	now := time.Date(2026, 8, 28, 12, 0, 0, 0, time.UTC)
	fetches := 0
	c := &instanceCache{
		ttl:        time.Minute,
		failureTTL: 5 * time.Second,
		now:        func() time.Time { return now },
		fetch: func(context.Context) (*control.InstanceIdentityResponse, error) {
			fetches++
			return nil, errors.New("connection refused")
		},
	}
	if err := c.probe(context.Background()); err == nil {
		t.Fatal("probe must surface the fetch failure")
	}
	stamp := c.stamp(context.Background())
	if fetches != 1 {
		t.Errorf("fetches = %d, want 1 (the stamp after a failed probe must reuse its outcome, not dial again)", fetches)
	}
	if stamp["backend"] != backendUnreachable {
		t.Errorf("backend = %v, want %q", stamp["backend"], backendUnreachable)
	}

	// Past the failure window the stamp re-dials (recovery must be noticed).
	now = now.Add(6 * time.Second)
	c.stamp(context.Background())
	if fetches != 2 {
		t.Errorf("fetches = %d, want 2 (the negative cache must expire)", fetches)
	}
}

// TestInstanceStamp_AnsweredHTTPErrorIsNotUnreachable pins the §3.7.4 honesty
// rule: an instance that ANSWERED GET /instance with an HTTP error is
// reachable — the degraded stamp says "error", never "unreachable", and keeps
// the last-known identity + real fetched_at.
func TestInstanceStamp_AnsweredHTTPErrorIsNotUnreachable(t *testing.T) {
	fetchTime := time.Date(2026, 8, 28, 12, 0, 0, 0, time.UTC)
	now := fetchTime
	healthy := true
	c := &instanceCache{
		ttl:        time.Minute,
		failureTTL: time.Second,
		now:        func() time.Time { return now },
		fetch: func(context.Context) (*control.InstanceIdentityResponse, error) {
			if !healthy {
				return nil, &HTTPError{StatusCode: http.StatusUnauthorized, Body: `{"detail":"revoked"}`}
			}
			return &control.InstanceIdentityResponse{Backend: "local", Host: "127.0.0.1:8000"}, nil
		},
	}
	c.stamp(context.Background()) // prime the last-known identity

	healthy = false
	now = now.Add(5 * time.Minute) // TTL expired; the refresh gets an HTTP 401
	stamp := c.stamp(context.Background())
	if stamp["backend"] != backendError {
		t.Fatalf("backend = %v, want %q (a status response proves reachability)", stamp["backend"], backendError)
	}
	if stamp["host"] != "127.0.0.1:8000" {
		t.Errorf("degraded stamp must keep the last-known identity, got %#v", stamp)
	}
	if stamp["fetched_at"] != fetchTime.Format(time.RFC3339) {
		t.Errorf("fetched_at = %v, want the REAL last-success time", stamp["fetched_at"])
	}
}

// TestInstanceCache_InvalidateForcesRefreshInsideTTL pins §3.7.4's
// refresh-on-auth-error hook: invalidate() makes the next stamp re-fetch even
// though the TTL has not lapsed.
func TestInstanceCache_InvalidateForcesRefreshInsideTTL(t *testing.T) {
	fetches := 0
	c := &instanceCache{
		ttl:        time.Hour,
		failureTTL: time.Second,
		now:        time.Now,
		fetch: func(context.Context) (*control.InstanceIdentityResponse, error) {
			fetches++
			return &control.InstanceIdentityResponse{Backend: "local", Host: "h"}, nil
		},
	}
	c.stamp(context.Background())
	c.stamp(context.Background())
	if fetches != 1 {
		t.Fatalf("fetches = %d, want 1 (TTL cache must hold before invalidate)", fetches)
	}
	c.invalidate()
	c.stamp(context.Background())
	if fetches != 2 {
		t.Errorf("fetches = %d, want 2 (invalidate must force a refresh inside the TTL)", fetches)
	}
}

// --- soft-error taxonomy mapping ----------------------------------------------

// TestMCPCoded_Wire401And403MapToNotAuthenticated pins the §3.7 table's
// revoked-identity row: a control-plane 401/403 that survived the retry
// transport's re-exchange is NOT_AUTHENTICATED with the get_started pointer —
// never the INTERNAL_ERROR "report a CLI bug" catch-all.
func TestMCPCoded_Wire401And403MapToNotAuthenticated(t *testing.T) {
	for _, status := range []int{http.StatusUnauthorized, http.StatusForbidden} {
		coded := mcpCoded(&HTTPError{StatusCode: status, Body: `{"detail":"identity revoked"}`})
		if coded.Code != ux.CodeNotAuthenticated {
			t.Errorf("status %d: code = %q, want %q", status, coded.Code, ux.CodeNotAuthenticated)
		}
		if !strings.Contains(coded.Actionable, "get_started") {
			t.Errorf("status %d: actionable %q must point at get_started", status, coded.Actionable)
		}
	}
	// Any other wire status keeps the fail-toward-generic rule.
	if coded := mcpCoded(&HTTPError{StatusCode: http.StatusInternalServerError}); coded.Code != ux.CodeInternalError {
		t.Errorf("500 code = %q, want %q", coded.Code, ux.CodeInternalError)
	}
}

// TestSoftError_Revoked401CarriesNextToolAndRefreshesStamp exercises the MCP
// soft-error path end to end: the result is isError with the coded envelope +
// next_tool, and the auth failure invalidates the stamp cache (§3.7.4
// refresh-on-auth-error) so the stamp on this very result is re-validated.
func TestSoftError_Revoked401CarriesNextToolAndRefreshesStamp(t *testing.T) {
	s := newTestMCPServer(t, nil)
	fetches := 0
	s.instances.fetch = func(context.Context) (*control.InstanceIdentityResponse, error) {
		fetches++
		return &control.InstanceIdentityResponse{Backend: "local", Host: "h"}, nil
	}
	ctx := context.Background()
	s.instances.stamp(ctx) // prime: the stamp is "fresh" when the 401 arrives
	if fetches != 1 {
		t.Fatalf("fetches = %d, want 1 after priming", fetches)
	}

	res := s.softError(ctx, &HTTPError{StatusCode: http.StatusUnauthorized, Body: `{"detail":"identity revoked"}`})
	if !res.IsError {
		t.Fatal("softError must mark the result isError")
	}
	payload := decodeToolJSON(t, res)
	if payload["error_code"] != ux.CodeNotAuthenticated {
		t.Errorf("error_code = %v, want %q", payload["error_code"], ux.CodeNotAuthenticated)
	}
	if payload["next_tool"] != "get_started" {
		t.Errorf("next_tool = %v, want get_started (the recovery loop pointer)", payload["next_tool"])
	}
	if fetches != 2 {
		t.Errorf("fetches = %d, want 2 (an auth error inside the TTL must re-validate the stamp)", fetches)
	}
}

// --- long-running annotation (interceptor exemption) --------------------------

// TestMCPCommand_CarriesLongRunningAnnotation guards the interceptor
// exemption's command side: without this annotation the agent-mode 60s
// wall-clock deadline would kill every `jentic mcp` session mid-flight (the
// interceptor side is pinned in cmdcore's fencing tests).
func TestMCPCommand_CarriesLongRunningAnnotation(t *testing.T) {
	a := testApp(t)
	root := newAPIRootCmd(a.App)
	cmd, _, err := root.Find([]string{"mcp"})
	if err != nil {
		t.Fatalf("find mcp command: %v", err)
	}
	if cmd.Annotations[cmdcore.LongRunningAnnotation] != "true" {
		t.Errorf("`jentic mcp` must carry the %s annotation", cmdcore.LongRunningAnnotation)
	}
}

// --- attribution RoundTripper (User-Agent + session precedence) ---------------

// recordingRT captures the request the transport actually sends.
type recordingRT struct{ got *http.Request }

func (r *recordingRT) RoundTrip(req *http.Request) (*http.Response, error) {
	r.got = req
	return &http.Response{StatusCode: http.StatusOK, Body: http.NoBody, Request: req}, nil
}

func TestAttributionTransport_UserAgentAndSessionFallback(t *testing.T) {
	s := newTestMCPServer(t, nil)
	rec := &recordingRT{}
	rt := s.transportHook()(rec)

	req, _ := http.NewRequest(http.MethodGet, "http://127.0.0.1:8000/instance", nil)
	resp, err := rt.RoundTrip(req)
	if err != nil {
		t.Fatalf("RoundTrip: %v", err)
	}
	_ = resp.Body.Close()
	if got := rec.got.Header.Get("User-Agent"); got != "jentic-mcp/0.0.0-test" {
		t.Errorf("User-Agent = %q, want jentic-mcp/0.0.0-test", got)
	}
	if got := rec.got.Header.Get("X-Jentic-Session-Id"); got != s.sessionID {
		t.Errorf("X-Jentic-Session-Id = %q, want the per-process fallback %q", got, s.sessionID)
	}
	// The original request must not have been mutated (RoundTripper contract).
	if req.Header.Get("User-Agent") != "" {
		t.Errorf("original request was mutated")
	}
}

func TestAttributionTransport_PresetSessionWins(t *testing.T) {
	s := newTestMCPServer(t, nil)
	rec := &recordingRT{}
	rt := s.transportHook()(rec)

	req, _ := http.NewRequest(http.MethodGet, "http://127.0.0.1:8000/instance", nil)
	// The SDK's session editor runs before the transport: an env-set
	// $JENTIC_SESSION_ID is already on the request. Orchestrator wins.
	req.Header.Set("X-Jentic-Session-Id", "orchestrator-session")
	resp, err := rt.RoundTrip(req)
	if err != nil {
		t.Fatalf("RoundTrip: %v", err)
	}
	_ = resp.Body.Close()
	if got := rec.got.Header.Get("X-Jentic-Session-Id"); got != "orchestrator-session" {
		t.Errorf("X-Jentic-Session-Id = %q, want the pre-set orchestrator value", got)
	}
}

func TestAttributionTransport_ClientInfoUpgradesUserAgent(t *testing.T) {
	s := newTestMCPServer(t, nil)
	rec := &recordingRT{}
	rt := s.transportHook()(rec)

	s.noteClient(&mcp.Implementation{Name: "claude code", Version: "2.0"})
	req, _ := http.NewRequest(http.MethodGet, "http://127.0.0.1:8000/instance", nil)
	resp, err := rt.RoundTrip(req)
	if err != nil {
		t.Fatalf("RoundTrip: %v", err)
	}
	_ = resp.Body.Close()
	// The clientInfo fragments are sanitized (space dropped), the prefix stays
	// jentic-mcp/ so the backend's derive_origin is unaffected.
	if got := rec.got.Header.Get("User-Agent"); got != "jentic-mcp/0.0.0-test (claudecode/2.0)" {
		t.Errorf("User-Agent = %q, want the client-upgraded form", got)
	}
}

func TestNoteClient_IgnoresEmpty(t *testing.T) {
	s := newTestMCPServer(t, nil)
	s.noteClient(&mcp.Implementation{Name: "cursor", Version: "1"})
	s.noteClient(nil)
	s.noteClient(&mcp.Implementation{})
	if ua := s.userAgent(); !strings.Contains(ua, "cursor/1") {
		t.Errorf("userAgent = %q, want the last real clientInfo retained", ua)
	}
}

func TestUAToken_SanitizesHostileValues(t *testing.T) {
	cases := map[string]string{
		"claude code":     "claudecode",
		"cursor/1.2\r\nX": "cursor1.2X",
		"":                "unknown",
		"???":             "unknown",
	}
	for in, want := range cases {
		if got := uaToken(in); got != want {
			t.Errorf("uaToken(%q) = %q, want %q", in, got, want)
		}
	}
}
