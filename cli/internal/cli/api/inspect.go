package api

import (
	"errors"
	"fmt"
	"net/http"
	"strings"

	"github.com/jentic/jentic-one/cli/internal/apiclient"
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
		Use:   "inspect <METHOD:url | operation_id>",
		Short: "Inspect an operation's contract (schema, parameters, examples)",
		Long: "inspect resolves an operation to its full structural detail: HTTP\n" +
			"method, URL, parameters, request/response schemas, and examples. The\n" +
			"target may be a discovered operation's METHOD:url (the same form\n" +
			"`jentic search` prints, e.g. GET:https://rest.coincap.io/v3/markets) or\n" +
			"an opaque operation_id. The output format is negotiated with the\n" +
			"server: JSON for machine consumption, Markdown for human reading.\n\n" +
			"Default format: JSON when stdout is not a TTY, Markdown when it is.",
		Example: "  jentic inspect GET:https://rest.coincap.io/v3/markets --format json | jq .method\n" +
			"  jentic inspect createUser --format markdown\n" +
			"  jentic search \"list users\" --json | jq -r '.data[0].operation_id' | xargs jentic inspect",
		Args: cobra.ExactArgs(1),
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
	baseURL, token, err := a.agentSession(cmd.Context())
	if err != nil {
		return err
	}

	format := opts.format
	if opts.json {
		format = "json"
	}
	if format == "" {
		if jsonOrPretty(cmd, false) {
			format = "json"
		} else {
			format = "markdown"
		}
	}

	client := apiclient.New(baseURL)
	body, err := client.Inspect(cmd.Context(), token, operationID, opts.revision, format)
	if err != nil {
		var he *apiclient.HTTPError
		if errors.As(err, &he) && he.StatusCode == http.StatusNotFound {
			// AGT-2: return a coded error (not a human string + bare exit-2) so an
			// agent gets a machine error_code (RESOLVE_FAILED) identical to the
			// execute-side resolve failure. The UX layer renders the human hint;
			// the agent envelope carries the same guidance as Actionable.
			return &ux.CodedError{
				Code: ux.CodeResolveFailed,
				Msg:  fmt.Sprintf("operation %q not found", operationID),
				Actionable: "inspect accepts the registry operation id (from `jentic search` _links.inspect / " +
					"`jentic apis operations`), a \"METHOD URL\" pair (e.g. " +
					"jentic inspect 'GET https://api.example.com/v1/things'), or the spec operationId " +
					"shown by `jentic catalog show` when it's unique across imported APIs. " +
					"If a spec operationId is ambiguous, use the registry operation id from `jentic search`.",
			}
		}
		return err
	}

	out := strings.TrimRight(string(body), "\n")
	fmt.Fprintln(a.Out, out)
	return nil
}
