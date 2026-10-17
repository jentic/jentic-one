package api

import (
	"context"
	"fmt"
	"net/http"
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
// up front instead of failing with a confusing 404 or empty ids.
const minCatalogServerVersion = "0.44.0"

// catalogGateEnabled reports whether the server-version gate runs. A source
// build of the CLI (version "dev") is paired with a source build of the
// server, which reports the last released version until the next release is
// cut, so the gate only runs in stamped release builds. A var so tests can
// exercise the gate.
var catalogGateEnabled = func() bool {
	v := cmdcore.Version()
	return v != "" && v != "dev"
}

// catalogServerOK caches the base URLs whose server passed the gate, so a
// long-lived `jentic mcp` process probes once per instance rather than on
// every tool call. Refusals are not cached: an upgraded server passes on the
// next call.
var catalogServerOK sync.Map

// checkCatalogServer probes GET /health through the same generated client the
// catalog calls use (so a custom CA, proxy or transport hook applies to the
// probe too) and refuses a server below minCatalogServerVersion, or one that
// reports no version, with an upgrade hint. A transport failure is returned
// as-is: the server isn't known to be old, it is unreachable.
func checkCatalogServer(ctx context.Context, baseURL string, sdk *control.ClientWithResponses) error {
	if !catalogGateEnabled() {
		return nil
	}
	if _, ok := catalogServerOK.Load(baseURL); ok {
		return nil
	}
	resp, err := sdk.GetHealthWithResponse(ctx)
	if err != nil {
		return err
	}
	version := ""
	if resp.StatusCode() == http.StatusOK && resp.JSON200 != nil {
		version = deref(resp.JSON200.Version)
	}
	if serverinfo.AtLeast(version, minCatalogServerVersion) {
		catalogServerOK.Store(baseURL, true)
		return nil
	}
	return catalogServerTooOld(version, resp.StatusCode())
}

// catalogServerTooOld is the refusal: SERVER_INCOMPATIBLE (the CLI and server
// are on different releases), naming both versions so the operator knows what
// to upgrade.
func catalogServerTooOld(version string, status int) *ux.CodedError {
	reported := "v" + version
	switch {
	case version == "" && status != http.StatusOK:
		reported = fmt.Sprintf("an unknown version (GET /health answered HTTP %d)", status)
	case version == "":
		reported = "no version"
	}
	return &ux.CodedError{
		Code: ux.CodeServerIncompatible,
		Msg: fmt.Sprintf("the connected server reports %s; this CLI needs Jentic One v%s or later for the catalog "+
			"(catalog entries are addressed by entry_id)", reported, minCatalogServerVersion),
		Actionable: serverIncompatibleRemedy,
		Details: map[string]any{
			"server_version":     version,
			"min_server_version": minCatalogServerVersion,
		},
	}
}
