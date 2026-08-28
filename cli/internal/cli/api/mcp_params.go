package api

// mcp_params.go is the shared tool-argument normalizer (master §3.2 "param
// alias tolerance"). Models drift on parameter names and shapes — `id` for
// `operation_id`, a bare string where a list is declared, a stringified JSON
// object for structured inputs — and a hard schema rejection would burn a tool
// round-trip on trivia the server can resolve unambiguously. Every tool
// handler funnels its raw arguments through normalizeToolArgs with a declared
// spec table, so the tolerance rules exist exactly once. Genuinely malformed
// input (not an object, unresolvable types, conflicting aliases) errors out —
// the handler maps that to a JSON-RPC invalid-params error, per the §3.7
// error-mapping table.

import (
	"encoding/json"
	"fmt"
	"strconv"
	"strings"
)

// paramKind is the canonical value shape a tool parameter normalizes to.
type paramKind int

const (
	paramString paramKind = iota
	paramStringList
	paramInt
	paramObject
)

// paramSpec declares one tool parameter: its canonical name (the key handlers
// read), the aliases folded onto it, and the shape values are coerced to.
type paramSpec struct {
	name    string
	aliases []string
	kind    paramKind
}

// The alias tables pinned by master §3.2. operationIDSpec serves the discovery
// tools; inputsSpec is declared alongside it (the normalizer lands complete in
// this PR) for 1-C's execute tools to reuse.
var (
	operationIDSpec = paramSpec{name: "operation_id", aliases: []string{"uuid", "id"}, kind: paramString}
	inputsSpec      = paramSpec{name: "inputs", aliases: []string{"params", "parameters"}, kind: paramObject}
)

// normalizeToolArgs decodes a tool call's raw arguments and folds them onto
// the spec table's canonical names: aliases are renamed, values are coerced to
// the declared kind (see coerceParam), and keys no spec claims are dropped —
// a stray key must not fail the call (the same permissive posture as the 1-A
// input schemas). A parameter supplied under several names with conflicting
// values errors — silently picking one would execute something the model did
// not ask for. nil/empty arguments normalize to an empty map.
func normalizeToolArgs(raw json.RawMessage, specs []paramSpec) (map[string]any, error) {
	out := make(map[string]any, len(specs))
	if len(raw) == 0 {
		return out, nil
	}
	var args map[string]any
	if err := json.Unmarshal(raw, &args); err != nil {
		return nil, fmt.Errorf("arguments must be a JSON object: %w", err)
	}
	for _, spec := range specs {
		var (
			got     any
			gotName string
			found   bool
		)
		for _, name := range append([]string{spec.name}, spec.aliases...) {
			v, ok := args[name]
			if !ok || v == nil {
				continue
			}
			coerced, err := coerceParam(v, spec.kind)
			if err != nil {
				return nil, fmt.Errorf("parameter %q: %w", name, err)
			}
			if !found {
				got, gotName, found = coerced, name, true
				continue
			}
			// The same parameter under a second name: identical values are
			// harmless repetition, different values are ambiguous.
			if !equalParam(got, coerced) {
				return nil, fmt.Errorf("parameters %q and %q are aliases of %q but carry different values", gotName, name, spec.name)
			}
		}
		if found {
			out[spec.name] = got
		}
	}
	return out, nil
}

// coerceParam converts a decoded JSON value to the declared kind, tolerating
// the shapes models actually send:
//   - string:      a one-element list unwraps; an integral number formats.
//   - string list: a bare string splits on commas (mirroring the CLI's
//     repeatable StringSlice flags); list elements coerce like strings.
//   - int:         a numeric string parses; a JSON number must be integral.
//   - object:      a JSON-object string decodes (models routinely stringify
//     structured arguments).
func coerceParam(v any, kind paramKind) (any, error) {
	switch kind {
	case paramString:
		return coerceString(v)
	case paramStringList:
		return coerceStringList(v)
	case paramInt:
		return coerceInt(v)
	case paramObject:
		return coerceObject(v)
	default:
		return nil, fmt.Errorf("unknown parameter kind %d", kind)
	}
}

func coerceString(v any) (string, error) {
	switch t := v.(type) {
	case string:
		return t, nil
	case float64:
		if t == float64(int64(t)) {
			return strconv.FormatInt(int64(t), 10), nil
		}
		return strconv.FormatFloat(t, 'g', -1, 64), nil
	case []any:
		if len(t) == 1 {
			return coerceString(t[0])
		}
		return "", fmt.Errorf("expected a string, got a list of %d values", len(t))
	default:
		return "", fmt.Errorf("expected a string, got %T", v)
	}
}

func coerceStringList(v any) ([]string, error) {
	switch t := v.(type) {
	case string:
		// Mirror pflag's StringSlice: comma-separated, whitespace-trimmed,
		// empties dropped.
		parts := strings.Split(t, ",")
		out := make([]string, 0, len(parts))
		for _, p := range parts {
			if p = strings.TrimSpace(p); p != "" {
				out = append(out, p)
			}
		}
		return out, nil
	case []any:
		out := make([]string, 0, len(t))
		for _, e := range t {
			s, err := coerceString(e)
			if err != nil {
				return nil, fmt.Errorf("list element: %w", err)
			}
			out = append(out, s)
		}
		return out, nil
	default:
		return nil, fmt.Errorf("expected a list of strings, got %T", v)
	}
}

func coerceInt(v any) (int, error) {
	switch t := v.(type) {
	case float64:
		if t != float64(int64(t)) {
			return 0, fmt.Errorf("expected an integer, got %v", t)
		}
		return int(t), nil
	case string:
		n, err := strconv.Atoi(strings.TrimSpace(t))
		if err != nil {
			return 0, fmt.Errorf("expected an integer, got %q", t)
		}
		return n, nil
	default:
		return 0, fmt.Errorf("expected an integer, got %T", v)
	}
}

func coerceObject(v any) (map[string]any, error) {
	switch t := v.(type) {
	case map[string]any:
		return t, nil
	case string:
		var m map[string]any
		if err := json.Unmarshal([]byte(t), &m); err != nil {
			return nil, fmt.Errorf("expected a JSON object (a stringified object is accepted): %w", err)
		}
		return m, nil
	default:
		return nil, fmt.Errorf("expected a JSON object, got %T", v)
	}
}

// equalParam compares two coerced values for the conflicting-alias check.
// Coerced shapes are strings, ints, []string, and JSON maps — all comparable
// through their JSON encoding, which spares a reflect.DeepEqual on interface
// soup.
func equalParam(a, b any) bool {
	ja, errA := json.Marshal(a)
	jb, errB := json.Marshal(b)
	return errA == nil && errB == nil && string(ja) == string(jb)
}
