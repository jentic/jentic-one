package api

// mcp_access.go holds the show_access MCP tool: the calling agent's own access
// through one credential binding (`jentic access show`'s read). It is what a
// connect-session edit is relative to, so an agent reads it before asking for
// a change, and again when such a request fails.

import (
	"context"
	"errors"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

var showAccessParams = []paramSpec{
	{name: "credential_id", kind: paramString},
}

var showAccessSchema = map[string]any{
	"type": "object",
	"properties": map[string]any{
		"credential_id": map[string]any{
			"type":        "string",
			"description": "The credential whose binding to read (required), e.g. \"cred_…\" from whoami's credential bindings.",
		},
	},
	"required": []string{"credential_id"},
}

func (s *mcpServer) accessToolSpecs() []mcpToolSpec {
	return []mcpToolSpec{
		{
			tool: &mcp.Tool{
				Name:  "show_access",
				Title: "Show your access through a credential",
				Description: "Show what your binding to one credential lets you do: its permission rules " +
					"with their ids (data, first match wins, default deny), the shared rule set it points " +
					"at (whose rules apply instead of data), the credential's granted OAuth scopes, and " +
					"whether the binding is suspended. " +
					`Example: {"credential_id": "cred_…"} — credential ids come from whoami's bindings. ` +
					"Call it before asking for a change to your access, and when such a request fails. " +
					"A credential you are not bound to (or whose binding is suspended) is not found: " +
					"call whoami to list your bindings.",
				InputSchema: showAccessSchema,
				Annotations: &mcp.ToolAnnotations{ReadOnlyHint: true},
			},
			handler: s.handleShowAccess,
		},
	}
}

func (s *mcpServer) handleShowAccess(ctx context.Context, req *mcp.CallToolRequest) (*mcp.CallToolResult, error) {
	s.noteClient(req.ClientInfo())
	cctx, cancel := s.callContext(ctx)
	defer cancel()

	args, err := normalizeToolArgs(req.Params.Arguments, showAccessParams)
	if err != nil {
		return nil, invalidParams(err)
	}
	credentialID, _ := args["credential_id"].(string)
	if credentialID == "" {
		return nil, invalidParams(errors.New(`show_access requires "credential_id", e.g. {"credential_id": "cred_…"}`))
	}
	access, err := s.app.getBindingAccess(cctx, credentialID)
	if err != nil {
		s.logger.Warn("show_access failed", "credential_id", credentialID, "error", redactedErr(err))
		next := ""
		if mcpCoded(err).Code == ux.CodeResolveFailed {
			next = "whoami"
		}
		return s.softErrorNext(cctx, err, next), nil
	}
	access["schema_version"] = mcpSchemaVersion
	return s.result(cctx, access), nil
}
