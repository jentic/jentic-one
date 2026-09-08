package api

// mcp_spec_test.go pins the machine-readable tool-surface spec at
// docs/reference/mcp-tools.json — the phase-1 stdio server's toolSpecs() is
// the single source of truth (master §3.2), and the phase-3 daemon-native
// Streamable HTTP mount CONSUMES the pinned file (its tests compare the
// mounted app's tools/list against the same JSON). A drift on either side
// fails against the identical file; divergence is a bug, not a re-pin.
//
// Regenerate deliberately after changing a tool declaration:
//
//	UPDATE_MCP_SPEC=1 go test ./internal/cli/api -run TestMCPToolSurfaceSpec
//
// and commit the updated file together with the change (the endpoints.json
// pattern: a contract change must be visible in review as a doc diff).

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

// mcpToolSpecPath locates docs/reference/mcp-tools.json from this package.
func mcpToolSpecPath() string {
	return filepath.Join("..", "..", "..", "..", "docs", "reference", "mcp-tools.json")
}

// specAnnotations projects the SDK annotations onto a flat, language-neutral
// map carrying only the hints a tool actually sets (absent == unset, so the
// Python consumer never has to distinguish Go's nil-pointer defaults).
func specAnnotations(a *mcp.ToolAnnotations) map[string]bool {
	out := map[string]bool{}
	if a == nil {
		return out
	}
	if a.ReadOnlyHint {
		out["read_only_hint"] = true
	}
	if a.IdempotentHint {
		out["idempotent_hint"] = true
	}
	if a.DestructiveHint != nil && *a.DestructiveHint {
		out["destructive_hint"] = true
	}
	if a.OpenWorldHint != nil && *a.OpenWorldHint {
		out["open_world_hint"] = true
	}
	return out
}

func TestMCPToolSurfaceSpec_PinnedAtDocsReference(t *testing.T) {
	s := newTestMCPServer(t, &mcpOptions{})

	type toolDoc struct {
		Name        string          `json:"name"`
		Title       string          `json:"title"`
		Description string          `json:"description"`
		InputSchema map[string]any  `json:"input_schema"`
		Annotations map[string]bool `json:"annotations"`
	}
	specs := s.toolSpecs()
	tools := make([]toolDoc, 0, len(specs))
	for _, spec := range specs {
		schema, ok := spec.tool.InputSchema.(map[string]any)
		if !ok {
			t.Fatalf("tool %s: input schema is not a map[string]any", spec.tool.Name)
		}
		tools = append(tools, toolDoc{
			Name:        spec.tool.Name,
			Title:       spec.tool.Title,
			Description: spec.tool.Description,
			InputSchema: schema,
			Annotations: specAnnotations(spec.tool.Annotations),
		})
	}
	doc := map[string]any{
		"$comment": "Generated from the Go stdio server's toolSpecs() — the phase-1 " +
			"tool-surface source of truth (master §3.2). Regenerate with " +
			"UPDATE_MCP_SPEC=1 go test ./internal/cli/api -run TestMCPToolSurfaceSpec. " +
			"Consumed by the phase-3 /mcp mount (src/jentic_one/mcp) and its drift tests.",
		"schema_version": mcpSchemaVersion,
		"tools":          tools,
	}
	got, err := json.MarshalIndent(doc, "", "  ")
	if err != nil {
		t.Fatalf("marshal spec: %v", err)
	}
	got = append(got, '\n')

	if os.Getenv("UPDATE_MCP_SPEC") == "1" {
		if err := os.WriteFile(mcpToolSpecPath(), got, 0o644); err != nil {
			t.Fatalf("write spec: %v", err)
		}
		return
	}

	want, err := os.ReadFile(mcpToolSpecPath())
	if err != nil {
		t.Fatalf("read pinned spec (regenerate with UPDATE_MCP_SPEC=1): %v", err)
	}
	if !bytes.Equal(got, want) {
		t.Errorf("toolSpecs() diverged from docs/reference/mcp-tools.json.\n" +
			"The pinned spec is the cross-implementation contract the /mcp mount consumes; " +
			"if the change is deliberate, regenerate with UPDATE_MCP_SPEC=1 and commit the diff.")
	}
}
