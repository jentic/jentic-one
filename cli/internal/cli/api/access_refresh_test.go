package api

import (
	"strings"
	"testing"

	"github.com/spf13/cobra"
)

// TestAccessRefreshRejectsAPIKeyProfile verifies the command fails fast with an
// actionable message for a static-API-key profile — which has no mintable token
// to refresh — before attempting any network call. See issue #673.
func TestAccessRefreshRejectsAPIKeyProfile(t *testing.T) {
	app := testApp(t)

	// Configure "work" as a static-API-key profile so agentSessionOpen succeeds
	// (IsAPIKey bypasses the registered-agent check) and accessRefreshE reaches
	// the API-key guard.
	opts := &addKeyOptions{apiKey: "jak_supersecretkey", baseURL: "http://example:9000"}
	if err := app.profileAddKeyE(&cobra.Command{}, "work", opts); err != nil {
		t.Fatalf("profileAddKeyE: %v", err)
	}

	ident := &identityOptions{Profile: "work"}
	err := app.accessRefreshE(&cobra.Command{}, ident, false)
	if err == nil {
		t.Fatal("expected an error for an API-key profile, got nil")
	}
	if !strings.Contains(err.Error(), "static API key") {
		t.Errorf("error should explain the API-key has no token to refresh, got: %v", err)
	}
}
