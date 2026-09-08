package api

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"

	"github.com/jentic/jentic-one/cli/internal/agentops"
	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

// executeOutput renders an ExecuteResult and maps a broker denial to its exit.
// Classification is the extracted core's job (agentops.Classify — the old
// fused render-and-classify is unfused per plan 0.2); this method owns only
// the UX side: which stream, which format, and the recovery rendering.
func (a *app) executeOutput(cmd *cobra.Command, opts *executeOptions, res *agentops.ExecuteResult) error {
	denial := agentops.Classify(res)

	if opts.raw {
		// Redact before streaming, so --raw matches the redaction guarantee of
		// the JSON and `jentic api` paths (SEC-2). A secret in an upstream body
		// must not leak just because the caller asked for raw output. (The read
		// itself was already bounded in agentops.Do.)
		if _, err := a.Out.Write(ux.RedactBytes(res.Body)); err != nil {
			return err
		}
		if denial != nil {
			return denial.Err()
		}
		return nil
	}

	if cmdcore.JSONOrPretty(cmd, opts.json) {
		// The success envelope is the shared ux type (AGT-23 schema_version
		// stamped by NewExecuteEnvelope), written via the legacy WriteJSON
		// path — its body is arbitrary upstream data, outside ux.Render's
		// three-layer funnel, so WriteJSON's byte-level redaction backstop
		// applies.
		if err := cmdcore.WriteJSON(a.Out, res.Envelope()); err != nil {
			return err
		}
	} else {
		a.executePrettyOutput(cmd.Context(), res)
	}

	// A broker denial (403/409/424/401) means the call did not run; exit 2 so a
	// scripted agent can branch on the denial instead of mistaking the 4xx body
	// for success. The exit code keys off the *status*, not the presence of an
	// agent_directive (see agentops.Classify). When a directive *is* present it
	// enriches the message with recovery steps.
	if denial != nil {
		if denial.Directive != nil {
			a.printAgentDirective(cmd.Context(), *denial.Directive)
		} else {
			// No agent_directive on the body (UX7): a first-timer who most needs the
			// pointer would otherwise get only the generic "broker denied" line and
			// a dead end. Synthesize a default next-step from the HTTP status the
			// broker already returned so no denial is a dead end.
			a.printSynthesizedDenialRecovery(cmd.Context(), denial.Status)
		}
		return denial.Err()
	}
	return nil
}

func (a *app) executePrettyOutput(ctx context.Context, res *agentops.ExecuteResult) {
	st := theme.StylesFromContext(ctx)
	statusLine := fmt.Sprintf("HTTP %d %s", res.Status, http.StatusText(res.Status))
	switch {
	case res.Status >= 200 && res.Status < 300:
		fmt.Fprintln(a.Out, st.Success.Render(statusLine))
	case res.Status >= 400:
		fmt.Fprintln(a.Out, st.Warn.Render(statusLine))
	default:
		fmt.Fprintln(a.Out, statusLine)
	}

	for k, vs := range res.Headers {
		if strings.HasPrefix(k, "Jentic-") {
			fmt.Fprintln(a.Out, st.Dim.Render(fmt.Sprintf("  %s: %s", k, strings.Join(vs, ", "))))
		}
	}

	fmt.Fprintln(a.Out)
	if len(res.Body) > 0 {
		// Redact the upstream body before display (SEC-2): the pretty path is
		// human-facing but can still carry secrets echoed by an upstream API.
		var pretty bytes.Buffer
		if err := json.Indent(&pretty, res.Body, "", "  "); err == nil {
			_, _ = a.Out.Write(ux.RedactBytes(pretty.Bytes()))
		} else {
			_, _ = a.Out.Write(ux.RedactBytes(res.Body))
		}
		fmt.Fprintln(a.Out)
	}
}
