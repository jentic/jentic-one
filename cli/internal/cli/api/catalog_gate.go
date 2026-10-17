package api

import (
	"context"
	"fmt"
	"net/http"
	"regexp"
	"runtime/debug"
	"strings"
	"sync"

	"github.com/jentic/jentic-one/cli/client/generated/control"
	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
	"github.com/jentic/jentic-one/cli/internal/serverinfo"
)

// minCatalogServerVersion is the first server release that serves the catalog
// by entry_id ("public/stripe.com"). An older server routes /catalog/{api_id}
// and answers with api_id/spec_url entries, which this CLI can neither address
// nor decode, so the catalog commands and the stdio catalog tools refuse it
// up front instead of failing with a confusing 404 or empty ids. The release
// checklist (docs/development/releasing.md) confirms it names the release
// that ships the entry_id catalog.
const minCatalogServerVersion = "0.44.0"

// pseudoVersion matches a Go module pseudo-version's timestamp-commit tail
// ("v0.0.0-20261017120000-abcdef123456"): a `go install …@main` build, which
// is a source build like "dev", not a release.
var pseudoVersion = regexp.MustCompile(`\d{14}-[0-9a-f]{12}$`)

// readBuildInfo is debug.ReadBuildInfo, a var so tests can stand in for a
// `go install …@vX` build.
var readBuildInfo = debug.ReadBuildInfo

// cliReleaseVersion is the CLI's release version, or "" for a source build.
// A goreleaser build stamps it via -ldflags; a `go install …@vX.Y.Z` build
// carries no ldflags but records the module version in its build info.
func cliReleaseVersion() string {
	if v := cmdcore.Version(); v != "" && v != "dev" {
		return v
	}
	info, ok := readBuildInfo()
	if !ok {
		return ""
	}
	v := info.Main.Version
	if v == "" || v == "(devel)" || pseudoVersion.MatchString(v) {
		return ""
	}
	return v
}

// catalogGateEnabled reports whether the server-version gate runs. A source
// build of the CLI is paired with a source build of the server, which reports
// the last released version until the next release is cut, so the gate only
// runs in release builds. A var so tests can exercise the gate.
var catalogGateEnabled = func() bool { return cliReleaseVersion() != "" }

// catalogServerOK caches the base URLs whose server passed the gate (a
// reported version at or above the minimum, or a catalog response that proved
// entry_id addressing), so a long-lived `jentic mcp` process probes once per
// instance rather than on every tool call. Refusals and unknown verdicts are
// not cached: an upgraded server passes on the next call.
var catalogServerOK sync.Map

// catalogVerdict is what the /health probe established.
type catalogVerdict int

const (
	// catalogServerCurrent: the server reports a version at or above the
	// minimum (or the gate is off).
	catalogServerCurrent catalogVerdict = iota
	// catalogServerUnknown: the server reports no parseable version, or
	// /health isn't reachable as such (a proxy answering 401/404). The caller
	// verifies the shape of the catalog response instead (verifyCatalogShape).
	catalogServerUnknown
)

// checkCatalogServer probes GET /health through the same generated client the
// catalog calls use (so a custom CA, proxy or transport hook applies to the
// probe too).
//
//   - A transport failure is returned as-is, and a 5xx or 429 is a retryable
//     TRANSPORT_ERROR: the server is unavailable, not known to be old.
//   - A parsed version below minCatalogServerVersion is SERVER_INCOMPATIBLE.
//   - No parseable version, or any other non-200 answer, is unknown: the
//     caller checks the catalog response for entry_id instead of failing
//     blind.
func checkCatalogServer(ctx context.Context, baseURL string, sdk *control.ClientWithResponses) (catalogVerdict, error) {
	if !catalogGateEnabled() {
		return catalogServerCurrent, nil
	}
	if _, ok := catalogServerOK.Load(baseURL); ok {
		return catalogServerCurrent, nil
	}
	resp, err := sdk.GetHealthWithResponse(ctx)
	if err != nil {
		return catalogServerUnknown, err
	}
	status := resp.StatusCode()
	if status >= http.StatusInternalServerError || status == http.StatusTooManyRequests {
		return catalogServerUnknown, catalogHealthUnavailable(status)
	}
	version := ""
	if status == http.StatusOK && resp.JSON200 != nil {
		version = deref(resp.JSON200.Version)
	}
	if !serverinfo.Parses(version) {
		return catalogServerUnknown, nil
	}
	if !serverinfo.AtLeast(version, minCatalogServerVersion) {
		return catalogServerUnknown, catalogServerTooOld(version)
	}
	catalogServerOK.Store(baseURL, true)
	return catalogServerCurrent, nil
}

// verifyCatalogShape is the fallback when /health didn't say what the server
// runs: a catalog entry without an entry_id came from a server that predates
// entry_id addressing. Entries that carry one prove the server current, so
// the pass is cached; an empty page proves nothing either way.
func verifyCatalogShape(baseURL string, entryIDs ...string) error {
	if len(entryIDs) == 0 {
		return nil
	}
	for _, id := range entryIDs {
		if id == "" {
			return catalogServerTooOld("")
		}
	}
	catalogServerOK.Store(baseURL, true)
	return nil
}

// catalogHealthUnavailable is a /health 5xx or 429: the server is up but
// can't answer right now, so the agent should retry rather than upgrade.
func catalogHealthUnavailable(status int) *ux.CodedError {
	return &ux.CodedError{
		Code:       ux.CodeTransportError,
		Msg:        fmt.Sprintf("the server's health check answered HTTP %d; it is temporarily unavailable", status),
		Actionable: "Retry shortly; if it persists, check the server is running (jentic env list).",
		Details:    map[string]any{"http_status": status, "retryable": true},
	}
}

// catalogServerTooOld is the refusal: SERVER_INCOMPATIBLE (the CLI and server
// are on different releases), naming both versions so the operator knows what
// to upgrade. An empty version means the catalog response itself showed the
// server predates entry_id addressing.
func catalogServerTooOld(version string) *ux.CodedError {
	reported := "the connected server reports v" + strings.TrimPrefix(version, "v")
	if version == "" {
		reported = "the connected server answers catalog entries without an entry_id"
	}
	return &ux.CodedError{
		Code: ux.CodeServerIncompatible,
		Msg: fmt.Sprintf("%s; this CLI needs Jentic One v%s or later for the catalog "+
			"(catalog entries are addressed by entry_id)", reported, minCatalogServerVersion),
		Actionable: serverIncompatibleRemedy,
		Details: map[string]any{
			"server_version":     version,
			"min_server_version": minCatalogServerVersion,
		},
	}
}
