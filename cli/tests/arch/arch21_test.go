package arch

import (
	"strings"
	"testing"
)

// ARCH-21: SDK consolidation. Part A migrated every data-plane command group off
// the hand-written internal/*client packages (and the internal/httpx transport
// they shared) and onto the single generated control SDK (client/generated/control,
// reached through internal/cli/clictx). This gate keeps that consolidation from
// eroding: it proves — by import-graph analysis, not prose — that the retired
// packages are gone and stay gone, and that the command layer that replaced them
// still routes through the generated SDK (so the ban can't pass vacuously by the
// whole feature having been deleted).

// arch21RetiredPackages are the hand-written per-command HTTP clients and the
// shared transport that ARCH-21 A1–A6 deleted. No in-module package may import
// any of them again; re-introducing one would fork the control-plane client
// surface the migration exists to unify. Compared as exact paths or path
// prefixes (rel-relative).
var arch21RetiredPackages = []struct {
	pkg, why string
}{
	{"internal/adminclient", "admin_providers migrated onto the generated control SDK (ARCH-21 A1)"},
	{"internal/searchclient", "search migrated onto the generated control SDK (ARCH-21 A2)"},
	{"internal/accessclient", "access/doctor migrated onto the generated control SDK (ARCH-21 A3)"},
	{"internal/catalogclient", "catalog migrated onto the generated control SDK (ARCH-21 A4)"},
	{"internal/apiclient", "apis/inspect/execute/endpoints migrated onto the generated control SDK (ARCH-21 A5)"},
	{"internal/httpx", "the hand-written transport is gone; the generated SDK owns HTTP (ARCH-21 A6)"},
}

// TestARCH21_RetiredClientsStayGone asserts no in-module package imports any of
// the retired hand-written client/transport packages (arch21RetiredPackages).
// The generated SDK is now the single control-plane client; a new import of one
// of these paths re-forks that surface.
func TestARCH21_RetiredClientsStayGone(t *testing.T) {
	pkgs := loadCLI(t)

	for _, p := range pkgs {
		if !strings.HasPrefix(p.PkgPath, modulePath) {
			continue
		}
		for imp := range p.Imports {
			for _, r := range arch21RetiredPackages {
				if underPrefixes(imp, r.pkg) {
					t.Errorf("ARCH-21: %s imports retired package %q — %s; use the generated control SDK "+
						"via internal/cli/clictx instead", rel(p.PkgPath), rel(imp), r.why)
				}
			}
		}
	}
}

// TestARCH21_DataPlaneUsesGeneratedSDK is the non-vacuity guard for the ban
// above: it proves the command layer that replaced the retired clients actually
// depends on the generated control SDK. If a future refactor deletes that
// dependency (or moves clictx), this fails rather than letting the retired-client
// ban pass trivially because nothing routes through the SDK anymore.
func TestARCH21_DataPlaneUsesGeneratedSDK(t *testing.T) {
	pkgs := loadCLI(t)

	const generatedControl = modulePath + "/client/generated/control"

	var found, usesSDK bool
	for _, p := range pkgs {
		if rel(p.PkgPath) != "internal/cli/api" {
			continue
		}
		found = true
		if _, ok := p.Imports[generatedControl]; ok {
			usesSDK = true
		}
		break
	}
	if !found {
		t.Fatal("ARCH-21: internal/cli/api not found — the data-plane command tree moved; " +
			"this non-vacuity guard must not pass silently")
	}
	if !usesSDK {
		t.Errorf("ARCH-21: internal/cli/api no longer imports %q — the data-plane commands must route "+
			"through the generated control SDK; if this is intentional, update the ARCH-21 gate",
			rel(generatedControl))
	}
}
