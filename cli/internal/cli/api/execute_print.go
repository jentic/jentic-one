package api

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

func (a *app) executeOutput(cmd *cobra.Command, opts *executeOptions, resp *http.Response) error {
	if opts.raw {
		// Read (bounded by the broker transport's cap) then redact before
		// streaming, so --raw matches the redaction guarantee of the JSON and
		// `jentic api` paths (SEC-2). A secret in an upstream body must not leak
		// just because the caller asked for raw output.
		body, err := io.ReadAll(resp.Body)
		if err != nil {
			return fmt.Errorf("read response: %w", err)
		}
		if _, err := a.Out.Write(ux.RedactBytes(body)); err != nil {
			return err
		}
		if isBrokerDenial(resp) {
			return brokerDeniedErr(resp)
		}
		return nil
	}

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Errorf("read response: %w", err)
	}

	if jsonOrPretty(cmd, opts.json) {
		if err := a.executeJSONOutput(resp, respBody); err != nil {
			return err
		}
	} else {
		a.executePrettyOutput(resp, respBody)
	}

	// A broker denial (403/409/424/401) means the call did not run; exit 2 so a
	// scripted agent can branch on the denial instead of mistaking the 4xx body
	// for success. The exit code keys off the *status*, not the presence of an
	// agent_directive: some denials (e.g. action_denied from a permission rule)
	// carry no directive, and gating exit 2 on a parsed directive would let those
	// silently exit 0 — the exact regression this surfacing is meant to remove.
	// When a directive *is* present it enriches the message with recovery steps.
	if isBrokerDenial(resp) {
		if directive, ok := parseAgentDirective(resp, respBody); ok {
			a.printAgentDirective(directive)
		} else {
			// No agent_directive on the body (UX7): a first-timer who most needs the
			// pointer would otherwise get only the generic "broker denied" line and
			// a dead end. Synthesize a default next-step from the HTTP status the
			// broker already returned so no denial is a dead end.
			a.printSynthesizedDenialRecovery(resp.StatusCode)
		}
		return brokerDeniedErr(resp)
	}
	return nil
}

func (a *app) executeJSONOutput(resp *http.Response, body []byte) error {
	headers := make(map[string]string)
	for k := range resp.Header {
		headers[k] = resp.Header.Get(k)
	}

	var parsedBody any
	if err := json.Unmarshal(body, &parsedBody); err != nil {
		parsedBody = string(body)
	}

	envelope := map[string]any{
		// AGT-23: stamp the machine-contract schema_version like every sanctioned
		// wrapper (ux.List/Page/Result/Export), so an agent can branch on the
		// envelope shape. execute builds an ad-hoc map (not a ux type) because its
		// body is the arbitrary upstream response, so the version is set here.
		"schema_version": apiEnvelopeSchemaVersion,
		"status":         resp.StatusCode,
		"headers":        headers,
		"body":           parsedBody,
	}
	if execID := resp.Header.Get("Jentic-Execution-Id"); execID != "" {
		envelope["execution_id"] = execID
	}

	return writeJSON(a.Out, envelope)
}

func (a *app) executePrettyOutput(resp *http.Response, body []byte) {
	statusLine := fmt.Sprintf("HTTP %d %s", resp.StatusCode, http.StatusText(resp.StatusCode))
	switch {
	case resp.StatusCode >= 200 && resp.StatusCode < 300:
		fmt.Fprintln(a.Out, theme.Success.Render(statusLine))
	case resp.StatusCode >= 400:
		fmt.Fprintln(a.Out, theme.Warn.Render(statusLine))
	default:
		fmt.Fprintln(a.Out, statusLine)
	}

	for k, vs := range resp.Header {
		if strings.HasPrefix(k, "Jentic-") {
			fmt.Fprintln(a.Out, theme.Dim.Render(fmt.Sprintf("  %s: %s", k, strings.Join(vs, ", "))))
		}
	}

	fmt.Fprintln(a.Out)
	if len(body) > 0 {
		// Redact the upstream body before display (SEC-2): the pretty path is
		// human-facing but can still carry secrets echoed by an upstream API.
		var pretty bytes.Buffer
		if err := json.Indent(&pretty, body, "", "  "); err == nil {
			_, _ = a.Out.Write(ux.RedactBytes(pretty.Bytes()))
		} else {
			_, _ = a.Out.Write(ux.RedactBytes(body))
		}
		fmt.Fprintln(a.Out)
	}
}
