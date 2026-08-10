package clictx

// client.go is the CLI adapter (impl/4.2 §3b): it translates the Cobra-resolved
// ActiveState into the SDK's UX-free client.Config and constructs the generated,
// authenticated plane clients. It lives in the leaf clictx package so command
// subpackages can import it without cycling back into the api command root.

import (
	"context"
	"errors"

	"github.com/jentic/jentic-one/cli/client"
	"github.com/jentic/jentic-one/cli/client/generated/broker"
	"github.com/jentic/jentic-one/cli/client/generated/control"
)

// configFromState maps the CLI's resolved ActiveState onto the SDK's UX-free
// Config. ActiveState embeds *config.ResolvedState, so the SDK fields (BaseURL,
// BrokerURL, IdentityName, …) are promoted and read through the same value.
func configFromState(state *ActiveState) client.Config {
	return client.Config{
		ControlBaseURL:      state.BaseURL,
		BrokerBaseURL:       state.BrokerURL,
		IdentityName:        state.IdentityName,
		EnvironmentName:     state.EnvironmentName,
		InjectedBearerToken: state.InjectedBearerToken,
		SessionID:           state.SessionID,
	}
}

// stateForClient validates the ActiveState a client constructor needs. It exists
// so a degraded state (root interceptor's no-config fallback carries an empty
// ResolvedState) or a nil embed can never nil-deref: commands get an actionable
// error instead of a panic (whose runtime exit code 2 collides with ExitDenied).
func stateForClient(state *ActiveState) (*ActiveState, error) {
	if state == nil {
		return nil, errors.New("no active state in context (was the root PersistentPreRunE run?)")
	}
	// A nil embed or a state with no plane URL at all is the unconfigured-machine
	// case — surface the recovery command rather than the SDK's terser
	// "control base URL is required".
	if state.ResolvedState == nil || (state.BaseURL == "" && state.BrokerURL == "") {
		return nil, errors.New("no configuration found; run 'jentic register' (or 'jentic migrate' on a machine with a V1 setup) first")
	}
	return state, nil
}

// GetControlClient is the single constructor every Control Plane command uses. It
// pulls the ActiveState the root interceptor injected and delegates to the SDK, so
// token state, API-key credentials, and the file-less override are all handled
// transparently and identically to a third-party SDK consumer.
func GetControlClient(ctx context.Context) (*control.ClientWithResponses, error) {
	state, err := stateForClient(FromContext(ctx))
	if err != nil {
		return nil, err
	}
	return client.NewControl(configFromState(state))
}

// GetBrokerClient is the Data Plane counterpart. The Env's broker_url must be set
// (the SDK errors otherwise); it is NEVER derived from the control base URL.
func GetBrokerClient(ctx context.Context) (*broker.ClientWithResponses, error) {
	state, err := stateForClient(FromContext(ctx))
	if err != nil {
		return nil, err
	}
	return client.NewBroker(configFromState(state))
}

// GetControlRawClient returns the raw control client for the `jentic api`
// passthrough (arbitrary METHOD/PATH). It shares the identical transport + editor
// chain as GetControlClient, so the passthrough inherits auth, session, retry, and
// the transport guard from the SDK rather than re-implementing them.
func GetControlRawClient(ctx context.Context) (*control.Client, error) {
	state, err := stateForClient(FromContext(ctx))
	if err != nil {
		return nil, err
	}
	return client.NewControlRaw(configFromState(state))
}
