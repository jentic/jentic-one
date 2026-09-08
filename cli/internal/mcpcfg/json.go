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
	"os"
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
		// UseNumber keeps foreign numeric values textual (json.Number), so an
		// integer above 2^53 in a sibling server's config (an ID, a timestamp)
		// round-trips exactly instead of being silently corrupted by a
		// float64 detour.
		dec := json.NewDecoder(bytes.NewReader(trimmed))
		dec.UseNumber()
		if err := dec.Decode(&root); err != nil {
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

// ReadJSONEntry parses the jentic server entry back out of a JSON MCP config
// file. It returns ok=false (no error) when the file, the mcpServers object,
// or our key is absent — doctor uses this to validate what was ACTUALLY
// written for a runtime rather than re-deriving it from the live environment.
func ReadJSONEntry(path string) (entry Entry, ok bool, err error) {
	data, err := os.ReadFile(path) //nolint:gosec // path is a fixed per-runtime config location under the user's home.
	if os.IsNotExist(err) {
		return Entry{}, false, nil
	}
	if err != nil {
		return Entry{}, false, err
	}
	root := map[string]any{}
	if err := json.Unmarshal(data, &root); err != nil {
		return Entry{}, false, fmt.Errorf("%s: %w", path, err)
	}
	servers, _ := root[mcpServersKey].(map[string]any)
	raw, _ := servers[ServerName].(map[string]any)
	if raw == nil {
		return Entry{}, false, nil
	}
	entry.Command, _ = raw["command"].(string)
	if args, isList := raw["args"].([]any); isList {
		for _, a := range args {
			if s, isStr := a.(string); isStr {
				entry.Args = append(entry.Args, s)
			}
		}
	}
	return entry, entry.Command != "", nil
}
