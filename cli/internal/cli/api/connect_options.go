package api

// connect_options.go holds the read an agent makes before asking for a
// connection: how `:connect` would connect one registry API, scheme by scheme,
// and which scopes a request may name (GET
// /integrations/{vendor}/{name}/{version}/connect-options). `jentic apis auth`
// and the inspect_api MCP tool share it.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strings"

	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/client/generated/control"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

const connectOptionsNextStep = "To connect it, call request_connection (`jentic connect --api <vendor/name/version>`) " +
	"with requested_scopes from the scopes marked requestable, and auth_type when it declares several schemes. " +
	"If whoami already shows a binding serving this API, change its scopes with request_access_change instead."

// getConnectOptions reads how a connection to api would be made. An API the
// registry doesn't hold is a RESOLVE_FAILED pointing at import_api.
func (a *app) getConnectOptions(ctx context.Context, api *control.ApiTargetRequest, registrationID string) (map[string]any, error) {
	client, err := a.controlClient(ctx)
	if err != nil {
		return nil, err
	}
	params := &control.IntegrationsConnectOptionsParams{}
	if registrationID != "" {
		params.RegistrationId = &registrationID
	}
	resp, err := client.IntegrationsConnectOptionsWithResponse(ctx, api.Vendor, api.Name, api.Version, params)
	if err := apiErrorFor(resp, err); err != nil {
		var httpErr *HTTPError
		if errors.As(err, &httpErr) && httpErr.StatusCode == http.StatusNotFound {
			return nil, &ux.CodedError{
				Code: ux.CodeResolveFailed,
				Msg:  fmt.Sprintf("the registry holds no API %s", connectTargetLabel("", api)),
				Actionable: "find it with search_catalog (`jentic catalog search`) and import it " +
					"(import_api / `jentic catalog import`), then ask again",
			}
		}
		return nil, err
	}
	var options map[string]any
	if err := json.Unmarshal(resp.Body, &options); err != nil {
		return nil, fmt.Errorf("decode connect-options response: %w", err)
	}
	options["next_step"] = connectOptionsNextStep
	return options, nil
}

func newApisAuthCmd(app *app) *cobra.Command {
	var registrationID string
	cmd := &cobra.Command{
		Use:   "auth <vendor/name/version>",
		Short: "Show how a connection to an API would be made, and the scopes you can ask for",
		Long: "auth answers with each security scheme the API declares, how a connection\n" +
			"would collect it (an OAuth flow, a credential a human enters, or waiting for\n" +
			"an OAuth app), and for OAuth the scopes a request may name (requestable).\n" +
			"Use it before `jentic connect --api` or `jentic access edit --add-scopes`.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			aud := ux.FromContext(cmd.Context())
			api, err := parseConnectAPI(args[0])
			if err != nil {
				return reportCoded(aud, &ux.CodedError{Code: ux.CodeMissingArgument, Msg: err.Error()})
			}
			options, err := app.getConnectOptions(cmd.Context(), api, strings.TrimSpace(registrationID))
			if err != nil {
				return reportCoded(aud, asCoded(err))
			}
			aud.Render(options)
			return nil
		},
	}
	cmd.Flags().StringVar(&registrationID, "registration-id", "", "pin a shared OAuth app, as `jentic connect --registration-id` would")
	return cmd
}

var inspectAPIParams = []paramSpec{
	{name: "api", kind: paramJSON},
	{name: "oauth_app_registration_id", aliases: []string{"registration_id"}, kind: paramString},
}

var inspectAPISchema = map[string]any{
	"type": "object",
	"properties": map[string]any{
		"api": map[string]any{
			"description": `The registry API (required): {"vendor", "name", "version"} or a "vendor/name/version" string, as search_apis or import_api report it.`,
		},
		"oauth_app_registration_id": map[string]any{
			"type":        "string",
			"description": "Pin a shared OAuth app, as request_connection would.",
		},
	},
	"required": []string{"api"},
}

func (s *mcpServer) inspectAPIToolSpec() mcpToolSpec {
	return mcpToolSpec{
		tool: &mcp.Tool{
			Name:  "inspect_api",
			Title: "Show how to connect an API",
			Description: "Show how a connection to one registry API would be made, before asking for it: " +
				"each security scheme it declares, how a connection would collect it (an OAuth flow, a " +
				"credential a human enters, or waiting for an OAuth app), and for OAuth the scopes a request " +
				"may name (requestable: true; the spec's others are listed but can't be asked for here). " +
				`Example: {"api": "googleapis.com/gmail/v1"}. The flow is search_catalog, import_api if needed, ` +
				"inspect_api, then request_connection with requested_scopes from the requestable ones. An API " +
				"the registry doesn't hold is not found: import it first.",
			InputSchema: inspectAPISchema,
			Annotations: &mcp.ToolAnnotations{ReadOnlyHint: true},
		},
		handler: s.handleInspectAPI,
	}
}

func (s *mcpServer) handleInspectAPI(ctx context.Context, req *mcp.CallToolRequest) (*mcp.CallToolResult, error) {
	s.noteClient(req.ClientInfo())
	cctx, cancel := s.callContext(ctx)
	defer cancel()

	args, err := normalizeToolArgs(req.Params.Arguments, inspectAPIParams)
	if err != nil {
		return nil, invalidParams(err)
	}
	raw, ok := args["api"].(json.RawMessage)
	if !ok {
		return nil, invalidParams(errors.New(`inspect_api requires "api", e.g. {"api": "vendor/name/version"}`))
	}
	api, err := decodeConnectAPIArg(raw)
	if err != nil {
		return nil, invalidParams(err)
	}
	registrationID, _ := args["oauth_app_registration_id"].(string)
	options, err := s.app.getConnectOptions(cctx, api, strings.TrimSpace(registrationID))
	if err != nil {
		s.logger.Warn("inspect_api failed", "api", connectTargetLabel("", api), "error", redactedErr(err))
		next := ""
		if mcpCoded(err).Code == ux.CodeResolveFailed {
			next = "import_api"
		}
		return s.softErrorNext(cctx, err, next), nil
	}
	options["schema_version"] = mcpSchemaVersion
	return s.result(cctx, options), nil
}
