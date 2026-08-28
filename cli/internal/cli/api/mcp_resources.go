package api

// mcp_resources.go serves the shipped skill set as MCP resources (PR 1-D,
// §3.8): skill://<name> for every bundled skill, plus skill://index (the set
// manifest). Content is fetched from the connected backend when reachable —
// `GET /skills/<name>.md` / `/skills/index.json` (#651), the session's source
// of truth, so a brew-updated CLI against an older backend serves the
// BACKEND's bytes — with the embedded bundled copy as the offline fallback.
// Every read stamps provenance into the resource contents' _meta: source
// (hosted|bundled — skillgen's Source model, never a parallel one) and the
// document's content version. Reads work pre-auth (the §3.3 always-boots
// invariant): with no configuration at all, the bundled copy is served with
// source "bundled".
//
// The hosted routes are public and schema-hidden (no generated client method
// exists), so the fetch is a raw GET through clictx.ControlHTTPClient — the
// SEC-20 CA-pinned transport with the session's attribution hook composed
// over it, and no auth editors (the documents must be fetchable while a
// registration is still pending approval).

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strings"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	sdkclient "github.com/jentic/jentic-one/cli/client"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/skillgen"
)

// Resource URI scheme. skill://<name> serves one skill document;
// skill://index serves the set manifest.
const (
	skillURIScheme = "skill://"
	skillIndexURI  = skillURIScheme + "index"
)

// _meta keys carrying the §3.8 provenance on every read result. Namespaced
// per the MCP general-fields guidance so they can never collide with another
// party's metadata.
const (
	skillMetaSource  = "one.jentic/source"
	skillMetaVersion = "one.jentic/version"
)

// MIME types mirror what the backend serves for the same documents.
const (
	skillMarkdownMIME = "text/markdown; charset=utf-8"
	skillIndexMIME    = "application/json"
)

// skillProvenanceNote is appended to every skill resource description so a
// client (and its model) knows where the bytes come from and how to read the
// provenance stamp.
const skillProvenanceNote = " Served from the connected Jentic One backend when reachable " +
	"(the session's source of truth), falling back to the copy embedded in this binary offline; " +
	"the read result's _meta carries " + skillMetaSource + " (hosted|bundled) and " + skillMetaVersion + "."

// registerResources declares the skill:// resource surface: one resource per
// bundled skill (the shipped set is the stable, pre-auth-listable surface —
// BundledNames is its single source of truth) plus the index manifest. The
// URIs stay valid whatever the backend serves; a hosted set that has drifted
// from this binary shows up in the index read, not in resources/list.
func (s *mcpServer) registerResources() {
	for _, name := range skillgen.BundledNames() {
		raw, err := skillgen.RawBundled(name)
		if err != nil {
			// The content is compiled in via go:embed; a read failure is a
			// build-time programming error. Skip rather than panic so the
			// session still boots.
			s.logger.Error("skill resource skipped: bundled read failed", "skill", name, "error", err)
			continue
		}
		meta := skillgen.ParseDocMeta(raw)
		s.server.AddResource(&mcp.Resource{
			URI:         skillURIScheme + name,
			Name:        name,
			Title:       "Jentic skill: " + name,
			Description: meta.Description + skillProvenanceNote,
			MIMEType:    skillMarkdownMIME,
		}, s.handleSkillResource)
	}
	s.server.AddResource(&mcp.Resource{
		URI:   skillIndexURI,
		Name:  "index",
		Title: "Jentic skill index",
		Description: "Manifest of the served skill set: name, description, version, and the sha256 of each " +
			"document's raw bytes, so a client can pick and verify skills without reading them all." +
			skillProvenanceNote,
		MIMEType: skillIndexMIME,
	}, s.handleSkillIndexResource)
}

// skillDoc is one resolved skill document: the exact bytes to serve plus the
// §3.8 provenance (where they came from, which content version they are).
type skillDoc struct {
	raw     []byte
	source  skillgen.Source
	version string
}

// handleSkillResource reads one skill://<name> document. Only registered URIs
// reach here (the SDK answers unknown URIs with resource-not-found itself), so
// name is always a bundled skill and the fallback can never miss.
func (s *mcpServer) handleSkillResource(ctx context.Context, req *mcp.ReadResourceRequest) (*mcp.ReadResourceResult, error) {
	name := strings.TrimPrefix(req.Params.URI, skillURIScheme)
	cctx, cancel := s.callContext(ctx)
	defer cancel()

	doc, err := s.skillDocFor(cctx, name)
	if err != nil {
		return nil, err
	}
	s.logger.Info("skill resource read", "skill", name, "source", doc.source, "version", doc.version)
	return skillReadResult(req.Params.URI, skillMarkdownMIME, doc), nil
}

// skillDocFor resolves one skill's bytes hosted-first: the connected backend's
// copy when it answers (source hosted, version from ITS frontmatter — the
// hosted document may be newer or older than this binary), the bundled embed
// otherwise (source bundled). A hosted failure is never an error — offline is
// a supported state — but it is logged so version-skew debugging has a trail.
func (s *mcpServer) skillDocFor(ctx context.Context, name string) (skillDoc, error) {
	if raw, err := s.fetchHostedSkill(ctx, "/skills/"+name+".md"); err == nil {
		return skillDoc{raw: raw, source: skillgen.SourceHosted, version: skillgen.ParseDocMeta(raw).Version}, nil
	} else if !errors.Is(err, errNoBackend) {
		s.logger.Info("skill resource: hosted fetch failed, serving bundled", "skill", name, "error", err)
	}
	raw, err := skillgen.RawBundled(name)
	if err != nil {
		return skillDoc{}, fmt.Errorf("bundled skill %q: %w", name, err)
	}
	return skillDoc{raw: raw, source: skillgen.SourceBundled, version: skillgen.ParseDocMeta(raw).Version}, nil
}

// handleSkillIndexResource reads skill://index: the backend's own manifest
// verbatim when reachable (it describes exactly what `GET /skills/*` serves,
// including per-row versions and sha256 digests), else a locally composed
// manifest of the bundled set in the same row shape, with each row's url
// replaced by the skill:// URI this server actually serves.
func (s *mcpServer) handleSkillIndexResource(ctx context.Context, req *mcp.ReadResourceRequest) (*mcp.ReadResourceResult, error) {
	cctx, cancel := s.callContext(ctx)
	defer cancel()

	if raw, err := s.fetchHostedSkill(cctx, "/skills/index.json"); err == nil {
		s.logger.Info("skill index read", "source", skillgen.SourceHosted)
		return skillReadResult(req.Params.URI, skillIndexMIME, skillDoc{raw: raw, source: skillgen.SourceHosted}), nil
	} else if !errors.Is(err, errNoBackend) {
		s.logger.Info("skill index: hosted fetch failed, serving bundled manifest", "error", err)
	}

	raw, err := bundledSkillIndex()
	if err != nil {
		return nil, err
	}
	s.logger.Info("skill index read", "source", skillgen.SourceBundled)
	return skillReadResult(req.Params.URI, skillIndexMIME, skillDoc{raw: raw, source: skillgen.SourceBundled}), nil
}

// bundledSkillIndex composes the offline manifest from the embedded set: the
// backend's row shape (name, description, version, sha256 of the raw served
// bytes) with `uri` pointing at the skill:// resource instead of the
// backend's base-stamped absolute URL (there is no backend to stamp).
func bundledSkillIndex() ([]byte, error) {
	names := skillgen.BundledNames()
	rows := make([]map[string]string, 0, len(names))
	for _, name := range names {
		raw, err := skillgen.RawBundled(name)
		if err != nil {
			return nil, fmt.Errorf("bundled skill %q: %w", name, err)
		}
		meta := skillgen.ParseDocMeta(raw)
		sum := sha256.Sum256(raw)
		rows = append(rows, map[string]string{
			"name":        name,
			"description": meta.Description,
			"version":     meta.Version,
			"sha256":      hex.EncodeToString(sum[:]),
			"uri":         skillURIScheme + name,
		})
	}
	return json.Marshal(rows)
}

// skillReadResult builds the one read-result shape both handlers return: the
// document bytes VERBATIM with the provenance stamped into the contents'
// _meta. Deliberately not routed through ux.RedactBytes: these are static,
// public, version-stamped documents (never relayed API responses carrying
// session data), the index manifest pins each document's sha256 over its raw
// served bytes so any rewrite would break client-side verification — and the
// pattern-based redactor mangles innocent prose ("bearer token" →
// "bearer [REDACTED]"). version is omitted when the document has no single
// version (the index manifest carries its versions per row).
func skillReadResult(uri, mimeType string, doc skillDoc) *mcp.ReadResourceResult {
	meta := mcp.Meta{skillMetaSource: string(doc.source)}
	if doc.version != "" {
		meta[skillMetaVersion] = doc.version
	}
	return &mcp.ReadResourceResult{
		Contents: []*mcp.ResourceContents{{
			URI:      uri,
			MIMEType: mimeType,
			Text:     string(doc.raw),
			Meta:     meta,
		}},
	}
}

// errNoBackend marks the deliberate pre-auth path: no active state or no
// control-plane URL means there is no backend to prefer — serve the bundled
// copy without logging a failure (nothing failed).
var errNoBackend = errors.New("no backend configured")

// fetchHostedSkill GETs one agent-discovery document from the connected
// backend. The read is bounded by the session's result cap (§3.7 posture): a
// document larger than that cannot be served whole into a model's context, so
// an oversized response is treated as a bad response — the caller falls back
// to the bundled copy — never truncated mid-document.
func (s *mcpServer) fetchHostedSkill(ctx context.Context, path string) ([]byte, error) {
	st := clictx.ActiveContext(ctx)
	if st == nil || st.BaseURL == "" {
		return nil, errNoBackend
	}
	hc, err := clictx.ControlHTTPClient(ctx)
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, strings.TrimRight(st.BaseURL, "/")+path, nil)
	if err != nil {
		return nil, err
	}
	resp, err := hc.Do(req)
	if err != nil {
		return nil, err
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("backend answered %d for %s", resp.StatusCode, path)
	}
	return sdkclient.ReadAllBounded(resp.Body, s.maxResultBytes)
}
