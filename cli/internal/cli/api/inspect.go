package api

import (
	"errors"
	"fmt"
	"net/http"
	"strings"

	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
	"github.com/spf13/cobra"
)

type inspectOptions struct {
	revision string
	format   string
	json     bool
}

func newInspectCmd(app *app) *cobra.Command {
	opts := &inspectOptions{}

	cmd := &cobra.Command{
		Use:   "inspect <METHOD:url>",
		Short: "Inspect an operation's contract (schema, parameters, examples)",
		Long: "inspect resolves an operation to its full structural detail: HTTP\n" +
			"method, URL, parameters, request/response schemas, and examples. The\n" +
			"target is a discovered operation's METHOD:url (the same form\n" +
			"`jentic search` prints, e.g. GET:https://rest.coincap.io/v3/markets).\n" +
			"An opaque registry operation_id also still resolves, for compatibility\n" +
			"— prefer the METHOD:url form. The output format is negotiated with the\n" +
			"server: JSON for machine consumption, Markdown for human reading.\n\n" +
			"The space form (`GET <url>`) is also accepted, but the colon form is\n" +
			"canonical (it needs no shell quoting).\n\n" +
			"Default format: JSON when stdout is not a TTY, Markdown when it is.",
		Example: "  jentic inspect GET:https://rest.coincap.io/v3/markets --format json | jq .method\n" +
			"  jentic inspect POST:https://api.example.com/v1/users --format markdown\n" +
			"  jentic search \"list users\" --json | jq -r '.data[0] | \"\\(.method):\\(.url)\"' | xargs jentic inspect",
		Args: exactNamedArgs("<METHOD:url>", "target"),
		RunE: func(cmd *cobra.Command, args []string) error {
			return app.inspectE(cmd, opts, args[0])
		},
	}

	cmd.Flags().StringVar(&opts.revision, "revision", "", "pin to a specific revision ID")
	cmd.Flags().StringVar(&opts.format, "format", "", "output format: json, markdown, openapi (default: json for non-TTY, markdown for TTY)")
	cmd.Flags().BoolVar(&opts.json, "json", false, "shorthand for --format json")

	return cmd
}

func (a *app) inspectE(cmd *cobra.Command, opts *inspectOptions, operationID string) error {
	client, err := a.apisSession(cmd.Context())
	if err != nil {
		return err
	}

	format := opts.format
	if opts.json {
		format = "json"
	}
	if format == "" {
		if cmdcore.JSONOrPretty(cmd, false) {
			format = "json"
		} else {
			format = "markdown"
		}
	}

	body, err := client.Inspect(cmd.Context(), operationID, opts.revision, format)
	if err != nil {
		var he *HTTPError
		if errors.As(err, &he) && he.StatusCode == http.StatusNotFound {
			// AGT-2: return a coded error (not a human string + bare exit-2) so an
			// agent gets a machine error_code (RESOLVE_FAILED) identical to the
			// execute-side resolve failure. The UX layer renders the human hint;
			// the agent envelope carries the same guidance as Actionable.
			return &ux.CodedError{
				Code: ux.CodeResolveFailed,
				Msg:  fmt.Sprintf("operation %q not found", operationID),
				Actionable: "inspect resolves a METHOD:url pair (e.g. " +
					"jentic inspect GET:https://api.example.com/v1/things) — the form `jentic search` " +
					"prints; build it from a hit's method + url. A registry operation id (from " +
					"`jentic search` / `jentic apis operations`) and a unique spec operationId " +
					"(from `jentic catalog show`) also resolve, as fallbacks.",
			}
		}
		return err
	}

	// SEC (review round-3 P0): redact before writing the upstream body. inspect
	// emits the API's own contract (JSON/Markdown/OpenAPI), which can carry
	// example secrets or auth blocks — it must match the redaction guarantee of
	// execute/api, not leak just because the output is a raw body.
	out := strings.TrimRight(string(ux.RedactBytes(body)), "\n")
	fmt.Fprintln(a.Out, out)
	return nil
}
