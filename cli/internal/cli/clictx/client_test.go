package clictx

import (
	"context"
	"testing"

	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
)

// TestConfigFromState_MapsResolvedState verifies the ActiveState -> client.Config
// projection carries the control/broker URLs and identity/environment through.
func TestConfigFromState_MapsResolvedState(t *testing.T) {
	state := &ActiveState{
		ResolvedState: &sdkconfig.ResolvedState{
			IdentityName:        "ci",
			EnvironmentName:     "prod",
			BaseURL:             "https://ctl.example",
			BrokerURL:           "https://brk.example",
			InjectedBearerToken: "at_x",
		},
		Mode: ModeAgent,
	}
	cfg := configFromState(state)
	if cfg.ControlBaseURL != "https://ctl.example" || cfg.BrokerBaseURL != "https://brk.example" {
		t.Errorf("urls = %q/%q", cfg.ControlBaseURL, cfg.BrokerBaseURL)
	}
	if cfg.IdentityName != "ci" || cfg.EnvironmentName != "prod" {
		t.Errorf("identity/env = %q/%q", cfg.IdentityName, cfg.EnvironmentName)
	}
	if cfg.InjectedBearerToken != "at_x" {
		t.Errorf("injected token = %q", cfg.InjectedBearerToken)
	}
}

// TestGetControlClient_RequiresState: without an ActiveState in context (the root
// interceptor never ran) the adapter errors rather than building a half-configured
// client.
func TestGetControlClient_RequiresState(t *testing.T) {
	if _, err := GetControlClient(context.Background()); err == nil {
		t.Fatal("expected an error with no ActiveState in context")
	}
}

// TestGetControlClient_BuildsWithState: with a control URL present the adapter
// constructs a client.
func TestGetControlClient_BuildsWithState(t *testing.T) {
	state := &ActiveState{
		ResolvedState: &sdkconfig.ResolvedState{BaseURL: "https://ctl.example"},
		Mode:          ModeHuman,
	}
	ctx := WithActiveState(context.Background(), state)
	c, err := GetControlClient(ctx)
	if err != nil {
		t.Fatalf("GetControlClient: %v", err)
	}
	if c == nil {
		t.Fatal("nil control client")
	}
}

// TestGetBrokerClient_RequiresBrokerURL: the broker URL is never derived from the
// control URL, so a state without a broker_url errors.
func TestGetBrokerClient_RequiresBrokerURL(t *testing.T) {
	state := &ActiveState{
		ResolvedState: &sdkconfig.ResolvedState{BaseURL: "https://ctl.example"},
		Mode:          ModeHuman,
	}
	ctx := WithActiveState(context.Background(), state)
	if _, err := GetBrokerClient(ctx); err == nil {
		t.Fatal("expected GetBrokerClient to require a broker URL")
	}
}
