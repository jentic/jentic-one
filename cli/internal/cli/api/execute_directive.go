package api

import (
	"context"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// Directive decoding and parsing live in ux.Directive and
// agentops.ParseAgentDirective; styled rendering lives in ux, the terminal
// gatekeeper, so command code never composes stderr text itself. These thin
// methods keep stream choice (a.Err) in the app, which owns its writers.

// printSynthesizedDenialRecovery prints a best-effort recovery hint for a broker
// denial that carried no agent_directive, keyed off the HTTP status the broker
// already returned (UX7).
func (a *app) printSynthesizedDenialRecovery(ctx context.Context, status int) {
	ux.RenderSynthesizedDenialRecovery(ctx, a.Err, status)
}

// printAgentDirective renders a recovery directive to stderr, lifting the
// suggested_command / provisioning_url out of parameters so the agent (or its
// operator) sees the exact next action without parsing JSON.
func (a *app) printAgentDirective(ctx context.Context, d ux.Directive) {
	ux.RenderDirective(ctx, a.Err, d)
}
