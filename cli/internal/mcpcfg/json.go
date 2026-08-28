package mcpcfg

// json.go merges our server entry into a runtime's JSON MCP config
// (`~/.cursor/mcp.json`, `claude_desktop_config.json`). The managed unit is
// exactly the `mcpServers.jentic` key: every foreign key — sibling servers,
// unrelated top-level settings — round-trips through the merge untouched in
// value (the file is re-marshalled, so formatting normalizes to two-space
// indent with sorted keys, which is also what makes the write idempotent:
// merging twice yields byte-identical output).

import (
	"bytes"
	"encoding/json"
	"fmt"
)

// mcpServersKey is the top-level object both Cursor and Claude Desktop read
// their server entries from.
const mcpServersKey = "mcpServers"

// MergeJSON splices entry under mcpServers.<ServerName> into existing (the
// current file bytes; nil/empty for a new file), preserving every foreign
// key. It returns the full new file bytes and whether they differ from
// existing. A file whose top level is not a JSON object is refused rather
// than clobbered.
func MergeJSON(existing []byte, entry Entry) (out []byte, changed bool, err error) {
	root := map[string]any{}
	trimmed := bytes.TrimSpace(existing)
	if len(trimmed) > 0 {
		if err := json.Unmarshal(trimmed, &root); err != nil {
			return nil, false, fmt.Errorf("existing config is not a JSON object: %w", err)
		}
	}

	servers, ok := root[mcpServersKey].(map[string]any)
	if !ok {
		if existingVal, present := root[mcpServersKey]; present && existingVal != nil {
			return nil, false, fmt.Errorf("existing %q key is not an object", mcpServersKey)
		}
		servers = map[string]any{}
	}
	servers[ServerName] = map[string]any{
		"command": entry.Command,
		"args":    entry.Args,
	}
	root[mcpServersKey] = servers

	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetIndent("", "  ")
	// Keep the file readable when a foreign entry carries an "&" or similar
	// in an env value — this is a config file, not an HTML sink.
	enc.SetEscapeHTML(false)
	if err := enc.Encode(root); err != nil {
		return nil, false, err
	}
	out = buf.Bytes() // Encode appends the trailing newline
	return out, !bytes.Equal(out, existing), nil
}
