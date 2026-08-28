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
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/modelcontextprotocol/go-sdk/mcp"

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
	var gotPath, gotUA string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath, gotUA = r.URL.Path, r.Header.Get("User-Agent")
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
