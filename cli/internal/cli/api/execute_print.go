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
	// A broker-origin error that is not a denial (unknown credential, unregistered
	// operation) also means the call never reached the upstream (#1429).
	brokerErr := agentops.BrokerError(res)

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
		if brokerErr != nil {
			return brokerErr
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
		if denial.Directive == nil {
			// A held call that never ran (denied, expired, unresumable) has its
			// own recovery: the status-keyed binding hint would mislead.
			if detail := problemDetail(res.Body); ux.RenderApprovalOutcome(cmd.Context(), a.Err, denial.ProblemType, detail) {
				coded := denial.Err()
				coded.Msg = fmt.Sprintf("this held call never ran (%s)", denial.ProblemType)
				coded.Details["problem_type"] = denial.ProblemType
				return coded
			}
		}
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
	// A broker resolve failure carries its recovery (and any relayed candidate
	// credentials) in Actionable, which the root error reporter renders once on
	// stderr: a styled "Next Step" for humans, actionable_step in the agent
	// envelope.
	if brokerErr != nil {
		return brokerErr
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

// problemDetail returns a problem body's detail line, or "" when the body has
// none.
func problemDetail(body []byte) string {
	var p struct {
		Detail string `json:"detail"`
	}
	if json.Unmarshal(body, &p) != nil {
		return ""
	}
	return p.Detail
}
