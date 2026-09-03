package api

import (
	"context"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// The agent_directive type and its parsing moved to the UX-free core
// (ux.Directive / agentops.ParseAgentDirective); the styled rendering moved
// into ux (the sole terminal gatekeeper — plan 0.2: command code no longer
// composes stderr text itself). These two thin methods keep the stream choice
// (a.Err) here, where the app owns its writers.

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
