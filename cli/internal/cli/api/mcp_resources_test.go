package api

// mcp_resources_test.go exercises the PR 1-D skill:// resources: the hosted
// fetch (backend as the session's source of truth), the offline fallback to
// the bundled embed, the §3.8 provenance metadata (source + version), and the
// §3.3 pre-auth serving invariant. The wire-level resources/list +
// resources/read round trip lives in mcp_session_test.go.

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/skillgen"
)

// readResourceRequest builds the raw request shape the SDK hands a
// ResourceHandler.
func readResourceRequest(uri string) *mcp.ReadResourceRequest {
	return &mcp.ReadResourceRequest{Params: &mcp.ReadResourceParams{URI: uri}}
}

// singleContents asserts the result carries exactly one contents item and
// returns it.
func singleContents(t *testing.T, res *mcp.ReadResourceResult) *mcp.ResourceContents {
	t.Helper()
	if len(res.Contents) != 1 {
		t.Fatalf("contents items = %d, want 1", len(res.Contents))
	}
	return res.Contents[0]
}

const hostedJentic = `---
name: jentic
description: hosted copy from the backend
version: 7
---

# Using Jentic from the CLI (hosted revision)
`

func TestMCPSkillResource_HostedFetchWithProvenance(t *testing.T) {
	var gotPath, gotUA, gotAuth string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath, gotUA, gotAuth = r.URL.Path, r.Header.Get("User-Agent"), r.Header.Get("Authorization")
		w.Header().Set("Content-Type", "text/markdown; charset=utf-8")
		_, _ = w.Write([]byte(hostedJentic))
	}))
	defer srv.Close()

	s := newTestMCPServer(t, nil)
	// The session's attribution hook must ride the skill fetch like every
	// other backend call.
	ctx := clictx.WithTransportHook(activeCtx(srv.URL), s.transportHook())

	res, err := s.handleSkillResource(ctx, readResourceRequest("skill://jentic"))
	if err != nil {
		t.Fatalf("handleSkillResource: %v", err)
	}
	if gotPath != "/skills/jentic.md" {
		t.Errorf("backend path = %q, want the #651 route /skills/jentic.md", gotPath)
	}
	if !strings.HasPrefix(gotUA, "jentic-mcp/") {
		t.Errorf("User-Agent = %q, want the attribution hook composed onto the skill fetch", gotUA)
	}
	// The routes are public and the raw client carries no auth editors: the
	// session's bearer token (activeCtx injects one) must NOT leak onto the
	// skill fetch.
	if gotAuth != "" {
		t.Errorf("Authorization = %q, want no auth header on the public skills route", gotAuth)
	}

	c := singleContents(t, res)
	if c.Text != hostedJentic {
		t.Errorf("text = %q, want the hosted bytes verbatim", c.Text)
	}
	if c.MIMEType != skillMarkdownMIME {
		t.Errorf("mimeType = %q, want %q", c.MIMEType, skillMarkdownMIME)
	}
	if c.Meta[skillMetaSource] != string(skillgen.SourceHosted) {
		t.Errorf("meta source = %v, want hosted", c.Meta[skillMetaSource])
	}
	if c.Meta[skillMetaVersion] != "7" {
		t.Errorf("meta version = %v, want the HOSTED document's version 7 (not the bundled one)", c.Meta[skillMetaVersion])
	}
}

func TestMCPSkillResource_OfflineFallsBackToBundled(t *testing.T) {
	// A configured but unreachable backend: bind a listener, note the
	// address, close it — the dial fails fast.
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusOK) }))
	deadURL := srv.URL
	srv.Close()

	s := newTestMCPServer(t, nil)
	res, err := s.handleSkillResource(activeCtx(deadURL), readResourceRequest("skill://jentic"))
	if err != nil {
		t.Fatalf("offline must fall back, not fail: %v", err)
	}
	assertBundledJentic(t, res)
}

func TestMCPSkillResource_PreAuthNoConfigServesBundled(t *testing.T) {
	// The §3.3 invariant: no ActiveState at all (the interceptor's degraded
	// no-config resolution) still serves the skill — from the embed.
	s := newTestMCPServer(t, nil)
	res, err := s.handleSkillResource(context.Background(), readResourceRequest("skill://jentic"))
	if err != nil {
		t.Fatalf("pre-auth read must serve the bundled copy: %v", err)
	}
	assertBundledJentic(t, res)
}

func TestMCPSkillResource_BackendErrorFallsBackToBundled(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer srv.Close()

	s := newTestMCPServer(t, nil)
	res, err := s.handleSkillResource(activeCtx(srv.URL), readResourceRequest("skill://jentic"))
	if err != nil {
		t.Fatalf("a backend error must fall back, not fail: %v", err)
	}
	assertBundledJentic(t, res)
}

func TestMCPSkillResource_OversizedHostedFallsBackToBundled(t *testing.T) {
	// A hosted document over the result cap is a bad response (§3.7 posture):
	// never truncated mid-document, the bundled copy serves instead.
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(strings.Repeat("x", 4096)))
	}))
	defer srv.Close()

	s := newTestMCPServer(t, nil)
	s.maxResultBytes = 1024
	res, err := s.handleSkillResource(activeCtx(srv.URL), readResourceRequest("skill://jentic"))
	if err != nil {
		t.Fatalf("an oversized hosted body must fall back, not fail: %v", err)
	}
	assertBundledJentic(t, res)
}

// TestMCPSkillResource_MalformedHostedFallsBackToBundled pins the MAJOR-1
// shape gate: a 200 body under the cap is only served as hosted when its
// frontmatter parses AND names the requested skill. A captive portal's HTML
// or the wrong document must degrade to the bundled copy (source bundled),
// never be labeled hosted at a fabricated version.
func TestMCPSkillResource_MalformedHostedFallsBackToBundled(t *testing.T) {
	for _, tc := range []struct {
		name string
		body string
	}{
		{"html error page", "<html><body><h1>503 Service Unavailable</h1></body></html>"},
		{"frontmatter names another skill", "---\nname: other\ndescription: not jentic\nversion: 9\n---\n\n# other\n"},
		{"no frontmatter at all", "# Using Jentic from the CLI\n"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				_, _ = w.Write([]byte(tc.body))
			}))
			defer srv.Close()

			s := newTestMCPServer(t, nil)
			res, err := s.handleSkillResource(activeCtx(srv.URL), readResourceRequest("skill://jentic"))
			if err != nil {
				t.Fatalf("a malformed hosted body must fall back, not fail: %v", err)
			}
			assertBundledJentic(t, res)
		})
	}
}

// TestMCPSkillResource_RedirectNotFollowed pins the MAJOR-2 posture: the
// skills fetch never follows a redirect. A 3xx from the backend is a bad
// response (bundled fallback) — never a license to fetch an attacker-chosen
// location with the session's attribution headers and label the bytes hosted.
func TestMCPSkillResource_RedirectNotFollowed(t *testing.T) {
	var paths []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		paths = append(paths, r.URL.Path)
		if r.URL.Path == "/redirected" {
			_, _ = w.Write([]byte(hostedJentic))
			return
		}
		http.Redirect(w, r, "/redirected", http.StatusFound)
	}))
	defer srv.Close()

	s := newTestMCPServer(t, nil)
	res, err := s.handleSkillResource(activeCtx(srv.URL), readResourceRequest("skill://jentic"))
	if err != nil {
		t.Fatalf("a redirecting backend must fall back, not fail: %v", err)
	}
	assertBundledJentic(t, res)
	if len(paths) != 1 || paths[0] != "/skills/jentic.md" {
		t.Errorf("requested paths = %v, want only the skills route (the redirect target must never be fetched)", paths)
	}
}

// TestMCPSkillResource_HostedHonorsCAPin pins the skills-leg transport
// posture (modeled on TestMCPExecute_BrokerLegHonorsCAPinAndHook): the fetch
// rides the SEC-20 CA-pinned client, so it succeeds against a backend whose
// cert chains to the environment's ca_cert_path — and when the bundle is set
// but broken, the read SILENTLY DEGRADES to the bundled copy. That is the
// deliberate posture for this surface: unlike the execute broker leg (which
// fails closed with an error), a resource read never errors on transport
// trouble — offline is a supported state — so a broken pin costs hosted
// freshness, never correctness (the embed serves, labeled bundled).
func TestMCPSkillResource_HostedHonorsCAPin(t *testing.T) {
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(hostedJentic))
	}))
	defer srv.Close()

	// Write the test server's own CA cert as the environment's bundle.
	pemPath := filepath.Join(t.TempDir(), "ca.pem")
	pemBytes := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: srv.Certificate().Raw})
	if err := os.WriteFile(pemPath, pemBytes, 0o600); err != nil {
		t.Fatalf("write ca bundle: %v", err)
	}

	s := newTestMCPServer(t, nil)
	state := &clictx.ActiveState{
		ResolvedState: &sdkconfig.ResolvedState{
			IdentityName:    "test-agent",
			EnvironmentName: "test",
			BaseURL:         srv.URL,
			CACertPath:      pemPath,
		},
		Mode: clictx.ModeHuman,
	}
	ctx := clictx.WithActiveState(context.Background(), state)

	res, err := s.handleSkillResource(ctx, readResourceRequest("skill://jentic"))
	if err != nil {
		t.Fatalf("CA-pinned hosted fetch must succeed against the pinned cert: %v", err)
	}
	c := singleContents(t, res)
	if c.Meta[skillMetaSource] != string(skillgen.SourceHosted) {
		t.Errorf("meta source = %v, want hosted (the pinned TLS fetch must have served)", c.Meta[skillMetaSource])
	}
	if c.Text != hostedJentic {
		t.Errorf("text = %q, want the hosted bytes over the pinned transport", c.Text)
	}

	// A set-but-broken bundle silently degrades to bundled (see the test
	// comment above for why this differs from the execute leg's fail-closed).
	state.CACertPath = filepath.Join(t.TempDir(), "missing.pem")
	res, err = s.handleSkillResource(ctx, readResourceRequest("skill://jentic"))
	if err != nil {
		t.Fatalf("a broken ca_cert_path must degrade to bundled, not fail: %v", err)
	}
	assertBundledJentic(t, res)
}

// TestMCPSkillResource_HungBackendFallsBackWithinProbeBudget pins the
// MINOR-6 sub-budget: a backend that accepts the connection and then hangs
// must cost the hosted probe's budget, not the full 30s call deadline, before
// the bundled copy serves.
func TestMCPSkillResource_HungBackendFallsBackWithinProbeBudget(t *testing.T) {
	release := make(chan struct{})
	srv := httptest.NewServer(http.HandlerFunc(func(_ http.ResponseWriter, _ *http.Request) { <-release }))
	defer srv.Close()
	defer close(release) // unblock the handler before srv.Close waits on it

	s := newTestMCPServer(t, nil)
	s.hostedFetchTimeout = 100 * time.Millisecond
	start := time.Now()
	res, err := s.handleSkillResource(activeCtx(srv.URL), readResourceRequest("skill://jentic"))
	if err != nil {
		t.Fatalf("a hung backend must fall back, not fail: %v", err)
	}
	if elapsed := time.Since(start); elapsed > 3*time.Second {
		t.Errorf("fallback took %v, want the probe sub-budget (~100ms), not the call deadline", elapsed)
	}
	assertBundledJentic(t, res)
}

// assertBundledJentic pins the bundled-fallback contract: the embedded bytes
// verbatim, source bundled, the bundled document's own version.
func assertBundledJentic(t *testing.T, res *mcp.ReadResourceResult) {
	t.Helper()
	want, err := skillgen.RawBundled("jentic")
	if err != nil {
		t.Fatalf("RawBundled: %v", err)
	}
	c := singleContents(t, res)
	if c.Text != string(want) {
		t.Errorf("text does not match the bundled bytes (len %d vs %d)", len(c.Text), len(want))
	}
	if c.Meta[skillMetaSource] != string(skillgen.SourceBundled) {
		t.Errorf("meta source = %v, want bundled", c.Meta[skillMetaSource])
	}
	if c.Meta[skillMetaVersion] != skillgen.ParseDocMeta(want).Version {
		t.Errorf("meta version = %v, want the bundled document's version", c.Meta[skillMetaVersion])
	}
}

func TestMCPSkillIndex_HostedVerbatim(t *testing.T) {
	hosted := `[{"name":"jentic","description":"d","version":"7","sha256":"abc","url":"https://cp.example.com/skills/jentic.md"}]`
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/skills/index.json" {
			t.Errorf("path = %q, want /skills/index.json", r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(hosted))
	}))
	defer srv.Close()

	s := newTestMCPServer(t, nil)
	res, err := s.handleSkillIndexResource(activeCtx(srv.URL), readResourceRequest(skillIndexURI))
	if err != nil {
		t.Fatalf("handleSkillIndexResource: %v", err)
	}
	c := singleContents(t, res)
	if c.Text != hosted {
		t.Errorf("text = %q, want the backend manifest verbatim", c.Text)
	}
	if c.MIMEType != skillIndexMIME {
		t.Errorf("mimeType = %q, want %q", c.MIMEType, skillIndexMIME)
	}
	if c.Meta[skillMetaSource] != string(skillgen.SourceHosted) {
		t.Errorf("meta source = %v, want hosted", c.Meta[skillMetaSource])
	}
}

// TestMCPSkillIndex_MalformedHostedFallsBackToBundled pins the MAJOR-1 shape
// gate on the manifest: a hosted 200 body only serves when it is well-formed
// JSON whose top level is an array. Anything else degrades to the locally
// composed bundled manifest.
func TestMCPSkillIndex_MalformedHostedFallsBackToBundled(t *testing.T) {
	for _, tc := range []struct {
		name string
		body string
	}{
		{"html error page", "<html><body>oops</body></html>"},
		{"truncated json", `[{"name":"jentic"`},
		{"json object not array", `{"skills":[]}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				_, _ = w.Write([]byte(tc.body))
			}))
			defer srv.Close()

			s := newTestMCPServer(t, nil)
			res, err := s.handleSkillIndexResource(activeCtx(srv.URL), readResourceRequest(skillIndexURI))
			if err != nil {
				t.Fatalf("a malformed hosted manifest must fall back, not fail: %v", err)
			}
			c := singleContents(t, res)
			if c.Meta[skillMetaSource] != string(skillgen.SourceBundled) {
				t.Errorf("meta source = %v, want bundled", c.Meta[skillMetaSource])
			}
			var rows []map[string]string
			if err := json.Unmarshal([]byte(c.Text), &rows); err != nil {
				t.Fatalf("fallback must be the bundled manifest, got: %v\n%s", err, c.Text)
			}
		})
	}
}

func TestMCPSkillIndex_BundledManifestPreAuth(t *testing.T) {
	s := newTestMCPServer(t, nil)
	res, err := s.handleSkillIndexResource(context.Background(), readResourceRequest(skillIndexURI))
	if err != nil {
		t.Fatalf("pre-auth index read must serve the bundled manifest: %v", err)
	}
	c := singleContents(t, res)
	if c.Meta[skillMetaSource] != string(skillgen.SourceBundled) {
		t.Errorf("meta source = %v, want bundled", c.Meta[skillMetaSource])
	}

	var rows []map[string]string
	if err := json.Unmarshal([]byte(c.Text), &rows); err != nil {
		t.Fatalf("bundled manifest is not JSON: %v\n%s", err, c.Text)
	}
	names := skillgen.BundledNames()
	if len(rows) != len(names) {
		t.Fatalf("manifest rows = %d, want the full bundled set (%d)", len(rows), len(names))
	}
	for i, row := range rows {
		name := names[i]
		if row["name"] != name {
			t.Errorf("row %d name = %q, want %q (BundledNames order)", i, row["name"], name)
		}
		if row["uri"] != skillURIScheme+name {
			t.Errorf("row %d uri = %q, want the skill:// resource URI", i, row["uri"])
		}
		// `url` is a hosted-only key (the backend's base-stamped absolute
		// location); the offline manifest has no backend to stamp, so it
		// always carries `uri` and never `url`.
		if _, hasURL := row["url"]; hasURL {
			t.Errorf("row %d carries %q — the offline manifest must emit uri, never url", i, row["url"])
		}
		raw, err := skillgen.RawBundled(name)
		if err != nil {
			t.Fatalf("RawBundled(%s): %v", name, err)
		}
		sum := sha256.Sum256(raw)
		if row["sha256"] != hex.EncodeToString(sum[:]) {
			t.Errorf("row %d sha256 = %q, want the digest of the raw served bytes", i, row["sha256"])
		}
		if row["version"] == "" || row["description"] == "" {
			t.Errorf("row %d must carry version and description: %v", i, row)
		}
	}
}
