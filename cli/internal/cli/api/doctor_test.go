package api

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"io/fs"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
	"time"

	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
)

// meServer returns an httptest server answering GET /me with a minimal agent
// identity, so the doctor's reachability + identity checks pass without a real
// control plane.
func meServer(t *testing.T) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/me" {
			http.NotFound(w, r)
			return
		}
		_, _ = w.Write([]byte(`{"type":"agent","id":"agnt_test","status":"active","scopes":["apis:read"]}`))
	}))
}

// TestDoctorReportsReachableIdentity: with an active context + reachable /me,
// the report includes a passing reachability and identity row.
func TestDoctorReportsReachableIdentity(t *testing.T) {
	srv := meServer(t)
	defer srv.Close()

	app := testApp(t)

	// The doctor inspects the ACTIVE context from the command context.
	cmd := newDoctorCmd(app)
	cmd.SetContext(v2Ctx(srv.URL))
	_ = cmd.Flags().Set("json", "true")
	if err := cmd.RunE(cmd, nil); err != nil {
		t.Fatalf("doctor: %v", err)
	}
	out := app.Out.(*bytes.Buffer).String()

	var report struct {
		Checks []struct {
			Section string `json:"section"`
			Name    string `json:"name"`
			Status  string `json:"status"`
		} `json:"checks"`
	}
	if err := json.Unmarshal([]byte(out), &report); err != nil {
		t.Fatalf("doctor JSON did not parse: %v\n%s", err, out)
	}

	want := map[string]string{
		"Control plane/reachability": "pass",
		"Control plane/identity":     "pass",
	}
	for _, c := range report.Checks {
		key := c.Section + "/" + c.Name
		if exp, ok := want[key]; ok {
			if c.Status != exp {
				t.Errorf("check %q: got status %q, want %q", key, c.Status, exp)
			}
			delete(want, key)
		}
	}
	for key := range want {
		t.Errorf("expected doctor check %q not found in report:\n%s", key, out)
	}
}

// TestDoctorWarnsOnClockSkew: a token whose iat is far in the past trips the
// clock-skew warning (F8-9).
func TestDoctorWarnsOnClockSkew(t *testing.T) {
	d := &agentDoctor{app: testApp(t)}
	// Forge a JWT with an iat two hours ago (unsigned — doctor doesn't verify).
	stale := forgeJWTWithIat(t, time.Now().Add(-2*time.Hour))
	d.checkClockSkew(stale)

	var found bool
	for _, c := range d.checks {
		if c.Section == "Clock" && c.Name == "skew" {
			found = true
			if c.Status != agentWarn {
				t.Errorf("expected clock skew WARN, got %s (%s)", c.Status, c.Detail)
			}
		}
	}
	if !found {
		t.Error("expected a Clock/skew check for a JWT with iat")
	}
}

// TestDoctorSkipsSkewForOpaqueToken: an API-key/opaque token has no iat, so the
// skew check emits no row (rather than a spurious failure).
func TestDoctorSkipsSkewForOpaqueToken(t *testing.T) {
	d := &agentDoctor{app: testApp(t)}
	d.checkClockSkew("jak_static_api_key_not_a_jwt")
	for _, c := range d.checks {
		if c.Section == "Clock" {
			t.Errorf("opaque token should produce no Clock row, got %+v", c)
		}
	}
}

// forgeJWTWithIat builds an UNSIGNED three-segment token carrying the given iat,
// for the skew check (which reads iat without verifying the signature).
func forgeJWTWithIat(t *testing.T, iat time.Time) string {
	t.Helper()
	header := base64.RawURLEncoding.EncodeToString([]byte(`{"alg":"none","typ":"JWT"}`))
	payloadJSON, _ := json.Marshal(map[string]any{"iat": iat.Unix()})
	payload := base64.RawURLEncoding.EncodeToString(payloadJSON)
	return strings.Join([]string{header, payload, "sig"}, ".")
}

// TestDoctorIsReadOnlyOnPristineHome is the UX-1 regression: doctor documents
// "read-only (never mints tokens or writes config)", but it used to create
// ~/.jentic/profiles/default/agent.key on a pristine machine via agentauth.Open
// — which then made `jentic migrate` report a phantom migrated context (UX-2).
// After a doctor run against a fresh root, NOTHING may exist on disk.
func TestDoctorIsReadOnlyOnPristineHome(t *testing.T) {
	app := testApp(t)
	root := app.Paths.Root

	cmd := newDoctorCmd(app)
	cmd.SetContext(t.Context())
	_ = cmd.Flags().Set("json", "true")
	if err := cmd.RunE(cmd, nil); err != nil {
		t.Fatalf("doctor on pristine home should not hard-fail: %v", err)
	}

	var created []string
	_ = filepath.WalkDir(root, func(path string, _ fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if path != root {
			created = append(created, path)
		}
		return nil
	})
	if len(created) != 0 {
		t.Errorf("doctor wrote to a pristine home: %v", created)
	}
}

// TestDoctorDoesNotMintTokens: with an active context whose identity has no
// cached token (and is not registered in the XDG store), doctor must warn
// rather than hit the token endpoint — a mint would both violate read-only and
// persist fresh token state.
func TestDoctorDoesNotMintTokens(t *testing.T) {
	var tokenCalls int
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.Contains(r.URL.Path, "token") {
			tokenCalls++
		}
		http.NotFound(w, r)
	}))
	defer srv.Close()

	// Isolate the XDG store so the identity resolves as credential-less.
	t.Setenv("HOME", t.TempDir())
	t.Setenv("XDG_CONFIG_HOME", "")
	t.Setenv("XDG_STATE_HOME", "")

	app := testApp(t)
	// Active context WITHOUT an injected token: doctor must inspect only.
	ctx := clictx.WithActiveState(t.Context(), &clictx.ActiveState{
		ResolvedState: &sdkconfig.ResolvedState{
			IdentityName:    "test-agent",
			EnvironmentName: "test",
			BaseURL:         srv.URL,
		},
		Mode: clictx.ModeHuman,
	})

	cmd := newDoctorCmd(app)
	cmd.SetContext(ctx)
	_ = cmd.Flags().Set("json", "true")
	_ = cmd.RunE(cmd, nil)

	if tokenCalls != 0 {
		t.Errorf("doctor hit the token endpoint %d time(s); it must never mint", tokenCalls)
	}
}

// ensure the doctor command wires cleanly (constructor + flags) — a cheap guard
// that the command stays registerable.
func TestDoctorCommandConstructs(t *testing.T) {
	cmd := newDoctorCmd(testApp(t))
	if cmd.Use != "doctor" {
		t.Fatalf("unexpected Use %q", cmd.Use)
	}
	if cmd.Flags().Lookup("json") == nil {
		t.Error("doctor should expose --json")
	}
}
