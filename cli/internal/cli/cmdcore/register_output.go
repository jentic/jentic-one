package cmdcore

import (
	"context"
	"fmt"

	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

// renderRegistrationSuccess emits the terminal outcome for an approved
// registration: one structured Result in machine mode, or the context and
// workflow summary in human mode.
func (a *App) renderRegistrationSuccess(
	ctx context.Context,
	req registrationRequest,
	clientID string,
	claimRequired bool,
) {
	st := theme.StylesFromContext(ctx)
	if isMachineCtx(ctx) {
		// Machine mode: one JSON Result on stdout is the terminal success signal,
		// replacing the human "Token minted" / "Ready:" prose (which stays
		// suppressed by registerProgress). If claiming is enabled, surface the
		// fact machine-readably (never the token in prose) so an automated caller
		// can route a human to complete the claim — an agent cannot claim itself.
		res := ux.Result{
			Status: ux.StatusRegistered, Resource: "identity", Name: req.identity, ID: clientID,
			Message: "approved; token minted",
		}
		if claimRequired {
			// A claim needs a HUMAN (an agent cannot claim itself), so the machine
			// signal is the fact + where a human goes — not the raw token, which
			// the key-based redactor would mask anyway and which the agent cannot
			// action. The human-readable token is shown once via presentClaimAffordance.
			res.Fields = map[string]any{
				"claim_required": true,
				"claim_url":      agentClaimURL(req.baseURL, clientID, ""),
				"claim_command":  fmt.Sprintf("jentic identity claim %s --token <claim_token>", clientID),
			}
		}
		ux.FromContext(ctx).Render(res)
		return
	}
	fmt.Fprintln(a.Out, st.Successf("Token minted for %s.", req.identity))
	// Make the active identity unambiguous and switching obvious: register may
	// have created a NEW per-identity context (env "-" identity), so spell out
	// who you now are and how to move between agents. This is the same name
	// RegisterSetup activated.
	contextName := sdkconfig.SanitizeName(req.envName + "-" + req.identity)
	fmt.Fprintf(a.Out, "\n%s\n", st.Dimf("You are now %q on %q (context %q).", req.identity, req.envName, contextName))
	fmt.Fprintf(a.Out, "%s %s\n", st.Dim.Render("Switch agents:"), st.Command.Render("jentic context use <name>"))
	fmt.Fprintf(a.Out, "%s %s\n", st.Dim.Render("See all:      "), st.Command.Render("jentic context list"))
	// Human-context nudge (UX6): `register` mints tokens only — it silently skips
	// the agent skill + MCP entry that `setup` adds. A person who reached here
	// by hand may have wanted the full setup, so point them at it. This whole
	// success block is human-only (machine mode returned above), so no TTY guard
	// is needed.
	fmt.Fprintf(a.Out, "\n%s %s%s\n",
		st.Dim.Render("Tip:"),
		st.Command.Render("jentic setup"),
		st.Dim.Render(" also installs the agent skill and MCP entry — run it if you're setting up a coding agent."))
	// Multi-agent case: registering a SECOND agent into an env creates its own
	// per-identity context and silently makes it active (RegisterSetup). Spell
	// out WHY a new context appeared and how to get back, so a user who now sees
	// an unfamiliar active context isn't left wondering what happened to their
	// first agent (UX5).
	if prev := siblingContextInEnv(req.envName, contextName); prev != "" {
		fmt.Fprintf(a.Out, "\n%s\n", st.Dimf(
			"Created a new context %q (another agent in env %q); your previous context %q is unchanged.",
			contextName, req.envName, prev))
		fmt.Fprintf(a.Out, "%s %s\n", st.Dim.Render("Switch back:  "), st.Command.Render("jentic context use "+prev))
	}
	a.printNextSteps(st)
}

// siblingContextInEnv returns the name of an existing context bound to the same
// environment as (but a different context than) the one just registered — the
// signal that this register minted a SECOND agent into an env that already had
// one. Returns "" when there is no such sibling (the common single-agent case)
// or the config can't be read. Deterministic (lowest name) so the "switch back"
// pointer is stable.
func siblingContextInEnv(envName, currentContext string) string {
	cfg, err := sdkconfig.Load()
	if err != nil {
		return ""
	}
	prev := ""
	for name, c := range cfg.Contexts {
		if name == currentContext || c.Environment != envName {
			continue
		}
		if prev == "" || name < prev {
			prev = name
		}
	}
	return prev
}

// printNextSteps teaches the core discover -> inspect -> execute workflow after a
// successful register. Bare `jentic register` on an already-configured machine
// just re-mints, so this block is what
// makes "what do I do now?" obvious — a few copy-pasteable examples plus the
// pointer to full help, in place of a bare "Ready:" line.
func (a *App) printNextSteps(st theme.Styles) {
	fmt.Fprintf(a.Out, "\n%s\n", st.Heading.Render("Next steps"))
	steps := []struct{ desc, cmd string }{
		{"Browse the API catalog", "jentic catalog"},
		{"Find an operation (each result prints a ready-to-paste inspect/execute target)", "jentic search \"send a slack message\""},
		{"See what you can run right now", "jentic whoami"},
		{"A fresh agent is bound to no APIs — ask your operator to connect one and bind you (dashboard)", ""},
		{"Inspect that operation (paste the target search printed)", "jentic inspect <METHOD:url from search>"},
		{"Run it (same target)", "jentic execute <METHOD:url from search> -d '{\"key\":\"value\"}'"},
	}
	for _, s := range steps {
		if s.cmd == "" {
			fmt.Fprintf(a.Out, "  %s\n", st.Dim.Render(s.desc))
			continue
		}
		fmt.Fprintf(a.Out, "  %s\n    %s\n", st.Dim.Render(s.desc), st.Command.Render(s.cmd))
	}
	fmt.Fprintf(a.Out, "\n%s %s\n", st.Dim.Render("See all commands:"), st.Command.Render("jentic --help"))
	// Advertise doctor as the first thing to run when stuck (UX9): it is
	// read-only and each of its warnings already carries the exact remediation,
	// but it lives under "Local agent client" and isn't surfaced at the moments
	// of confusion.
	fmt.Fprintf(a.Out, "%s %s\n", st.Dim.Render("Stuck? Check your setup:"), st.Command.Render("jentic doctor"))
}
