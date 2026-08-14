package cmdcore

import (
	"context"
	"fmt"
	"net/url"

	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

// agentConsoleURL builds the operator-facing UI link for approving an agent.
// The SPA is mounted under /app, so the agent detail page (where the Approve
// action lives) is {baseURL}/app/agents/{id}. This mirrors how access requests
// surface a clickable approve_url instead of a raw API endpoint.
func agentConsoleURL(baseURL, agentID string) string {
	return config.AppURL(baseURL, "agents/"+agentID)
}

// agentClaimURL builds the human-facing UI link for CLAIMING ownership of a
// self-registered agent, carrying the single-use claim token so the console can
// pre-fill it. The console claim page lives at {baseURL}/app/agents/{id}/claim
// and reads the token from the `token` query param (enterprise AgentClaimPage:
// searchParams.get("token")). The backend does not hand back a ready-made claim
// URL and the page may not exist on every deployment, so the raw token + the
// `jentic identity claim` command are always shown alongside as the reliable
// fallback (an agent cannot claim itself; a human must). The token is a
// short-lived bearer capability shown once — never persisted, never logged.
func agentClaimURL(baseURL, agentID, claimToken string) string {
	u := config.AppURL(baseURL, "agents/"+agentID+"/claim")
	if claimToken != "" {
		u += "?token=" + url.QueryEscape(claimToken)
	}
	return u
}

// presentClaimAffordance guides the HUMAN to take ownership of a just-registered
// agent when the backend enabled claiming (non-empty claimToken). It is a no-op
// when claiming is off (OSS default) — so the OSS onboarding output is unchanged
// — and in machine mode, where the terminal ux.Result.Fields carry the
// machine-actionable signal instead (an agent cannot claim itself). Shown once:
// the token is single-use, short-lived, and deliberately never persisted, so we
// surface it exactly like the backend's "returned once" contract. Both a
// console link (by convention; the page may not exist everywhere) AND the raw
// token + exact command are printed, so the human always has a reliable path.
func (a *App) presentClaimAffordance(ctx context.Context, baseURL, agentID, claimToken string) {
	if claimToken == "" || isMachineCtx(ctx) {
		return
	}
	fmt.Fprintln(a.Out, "\n"+theme.Heading.Render("Claim ownership of this agent (you, the human — an agent cannot claim itself):"))
	fmt.Fprintf(a.Out, "    %s\n", theme.Command.Render(agentClaimURL(baseURL, agentID, claimToken)))
	fmt.Fprintln(a.Out, theme.Dim.Render("    Open the link above, or run the command below with the one-time token:"))
	fmt.Fprintf(a.Out, "    %s\n", theme.Command.Render(fmt.Sprintf("jentic identity claim %s --token %s", agentID, claimToken)))
	fmt.Fprintf(a.Out, "    %s\n", theme.Dimf("Token %s is single-use and short-lived — it is shown only once and is not saved.", claimToken))
}
