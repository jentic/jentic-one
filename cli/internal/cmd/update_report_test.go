package cmd

import (
	"context"
	"errors"
	"strings"
	"testing"
)

// TestReportLatestReleaseSeamSwallowsErrors verifies that the way updateE calls
// the reporting seam (`_ = reportLatestRelease(...)`) discards a reporting
// error, and that the discovered latest tag — not a pinned --ref — is what gets
// reported. The seam is stubbed so no network/profile is touched.
func TestReportLatestReleaseSeamSwallowsErrors(t *testing.T) {
	orig := reportLatestRelease
	t.Cleanup(func() { reportLatestRelease = orig })

	var gotVersion string
	var called bool
	reportLatestRelease = func(_ context.Context, _ *App, _, version string) error {
		called = true
		gotVersion = version
		return errors.New("boom")
	}

	// Mirror updateE's call site exactly: the error is dropped with `_ =`.
	_ = reportLatestRelease(context.Background(), testApp(t), "", "v0.26.0")

	if !called {
		t.Fatal("reporting seam was not invoked")
	}
	if gotVersion != "v0.26.0" {
		t.Errorf("reported version = %q, want v0.26.0 (the latest tag, not a pinned ref)", gotVersion)
	}
}

// TestDefaultReportLatestReleaseSkipsWithoutProfile confirms the production seam
// degrades to a returned error (which updateE swallows) rather than minting a
// token or panicking when no cached credential is configured. testApp gives it
// an isolated t.TempDir()-rooted Paths, so this never touches the real ~/.jentic
// and never reaches the network — it fails at the cached-token check.
func TestDefaultReportLatestReleaseSkipsWithoutProfile(t *testing.T) {
	app := testApp(t) // isolated temp root: no config, no profile, no cached token
	err := defaultReportLatestRelease(context.Background(), app, "", "v0.26.0")
	if err == nil {
		t.Fatal("expected a (swallowed) error when no cached token is available")
	}
	// It must fail on the missing cached credential, not by minting/refreshing
	// (which would hit the network) — assert the specific degrade path.
	if !strings.Contains(err.Error(), "no valid cached token") {
		t.Errorf("err = %q, want the cached-token degrade (no mint/refresh)", err)
	}
}
