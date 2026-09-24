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
	"strings"
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
		Name          string                       `json:"name"`
		Title         string                       `json:"title"`
		Description   string                       `json:"description"`
		InputSchema   map[string]any               `json:"input_schema"`
		Annotations   map[string]bool              `json:"annotations"`
		LaneOverrides map[string]map[string]string `json:"lane_overrides,omitempty"`
	}
	specs := s.toolSpecs()
	tools := make([]toolDoc, 0, len(specs))
	for _, spec := range specs {
		schema, ok := spec.tool.InputSchema.(map[string]any)
		if !ok {
			t.Fatalf("tool %s: input schema is not a map[string]any", spec.tool.Name)
		}
		var overrides map[string]map[string]string
		if len(spec.laneOverrides) > 0 {
			overrides = make(map[string]map[string]string, len(spec.laneOverrides))
			for lane, o := range spec.laneOverrides {
				overrides[lane] = map[string]string{"description": o.description}
			}
		}
		tools = append(tools, toolDoc{
			Name:          spec.tool.Name,
			Title:         spec.tool.Title,
			Description:   spec.tool.Description,
			InputSchema:   schema,
			Annotations:   specAnnotations(spec.tool.Annotations),
			LaneOverrides: overrides,
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

// TestMCPToolSpecs_LaneOverridesAreDeliberate guards the per-lane override
// mechanism itself: an override exists only for a known lane, is non-empty,
// and genuinely differs from the base rendering (an identical override is
// dead weight that would mask real drift). It also pins WHY the one current
// override exists: the base whoami prose routes auth recovery through
// get_started, which the http lane does not serve — the http rendering must
// not name it. The lane-completeness invariant (no served description may
// name a tool absent from that lane's served set) lives with the lane's
// served-set owner: tests/unit/mcp/test_tool_surface.py.
func TestMCPToolSpecs_LaneOverridesAreDeliberate(t *testing.T) {
	s := newTestMCPServer(t, &mcpOptions{})
	knownLanes := map[string]bool{"http": true}

	overridden := map[string]bool{}
	for _, spec := range s.toolSpecs() {
		for lane, o := range spec.laneOverrides {
			if !knownLanes[lane] {
				t.Errorf("tool %s: override for unknown lane %q", spec.tool.Name, lane)
			}
			if o.description == "" {
				t.Errorf("tool %s: empty %s description override", spec.tool.Name, lane)
			}
			if o.description == spec.tool.Description {
				t.Errorf("tool %s: %s override is identical to the base description", spec.tool.Name, lane)
			}
			overridden[spec.tool.Name] = true
		}
		if http, ok := spec.laneOverrides["http"]; ok {
			if strings.Contains(http.description, "get_started") {
				t.Errorf("tool %s: http description override names get_started, which the http lane does not serve", spec.tool.Name)
			}
		}
	}
	if !overridden["whoami"] {
		t.Error("whoami: expected an http description override — its base prose names get_started, which the http lane does not serve")
	}
}
