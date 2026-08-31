package api

import (
	"encoding/json"
	"reflect"
	"strings"
	"testing"
)

// --- normalizeToolArgs: alias folding + coercion (master §3.2 tolerance) -----

func TestNormalizeToolArgs_Table(t *testing.T) {
	searchSpecs := searchAPIsParams
	inspectSpecs := inspectOperationParams
	inputsSpecs := []paramSpec{operationIDSpec, inputsSpec}

	cases := []struct {
		name    string
		raw     string
		specs   []paramSpec
		want    map[string]any
		wantErr string // substring of the expected error, "" for success
	}{
		{
			name:  "nil arguments normalize to an empty map",
			raw:   "",
			specs: inspectSpecs,
			want:  map[string]any{},
		},
		{
			name:  "canonical keys pass through",
			raw:   `{"query":"list pets","apis":["acme/pets/v1"],"limit":5,"cursor":"c1"}`,
			specs: searchSpecs,
			want:  map[string]any{"query": "list pets", "apis": []string{"acme/pets/v1"}, "limit": 5, "cursor": "c1"},
		},
		{
			name:  "id aliases operation_id",
			raw:   `{"id":"op_abc"}`,
			specs: inspectSpecs,
			want:  map[string]any{"operation_id": "op_abc"},
		},
		{
			name:  "uuid aliases operation_id",
			raw:   `{"uuid":"op_abc"}`,
			specs: inspectSpecs,
			want:  map[string]any{"operation_id": "op_abc"},
		},
		{
			name:  "params aliases inputs",
			raw:   `{"params":{"petId":"42"}}`,
			specs: inputsSpecs,
			want:  map[string]any{"inputs": map[string]any{"petId": "42"}},
		},
		{
			name:  "parameters aliases inputs",
			raw:   `{"parameters":{"petId":"42"}}`,
			specs: inputsSpecs,
			want:  map[string]any{"inputs": map[string]any{"petId": "42"}},
		},
		{
			name:  "stringified object decodes for inputs",
			raw:   `{"inputs":"{\"petId\":\"42\"}"}`,
			specs: inputsSpecs,
			want:  map[string]any{"inputs": map[string]any{"petId": "42"}},
		},
		{
			name:  "bare string coerces to a one-element list",
			raw:   `{"query":"q","apis":"acme/pets/v1"}`,
			specs: searchSpecs,
			want:  map[string]any{"query": "q", "apis": []string{"acme/pets/v1"}},
		},
		{
			name:  "comma-separated string splits like the CLI flag",
			raw:   `{"query":"q","apis":"acme/pets/v1, acme/toys/v2"}`,
			specs: searchSpecs,
			want:  map[string]any{"query": "q", "apis": []string{"acme/pets/v1", "acme/toys/v2"}},
		},
		{
			name:  "api singular aliases apis",
			raw:   `{"query":"q","api":"acme/pets/v1"}`,
			specs: searchSpecs,
			want:  map[string]any{"query": "q", "apis": []string{"acme/pets/v1"}},
		},
		{
			// The response key copied straight back as the argument name —
			// the pagination drift the alias exists to absorb.
			name:  "next_cursor aliases cursor",
			raw:   `{"query":"q","next_cursor":"page2"}`,
			specs: searchSpecs,
			want:  map[string]any{"query": "q", "cursor": "page2"},
		},
		{
			name:  "one-element list unwraps to a string",
			raw:   `{"operation_id":["op_abc"]}`,
			specs: inspectSpecs,
			want:  map[string]any{"operation_id": "op_abc"},
		},
		{
			name:  "numeric string parses as int",
			raw:   `{"query":"q","limit":"25"}`,
			specs: searchSpecs,
			want:  map[string]any{"query": "q", "limit": 25},
		},
		{
			name:  "matching duplicate alias values are tolerated",
			raw:   `{"id":"op_abc","operation_id":"op_abc"}`,
			specs: inspectSpecs,
			want:  map[string]any{"operation_id": "op_abc"},
		},
		{
			name:  "unknown keys are dropped, not fatal",
			raw:   `{"query":"q","verbosity":"high"}`,
			specs: searchSpecs,
			want:  map[string]any{"query": "q"},
		},
		{
			name:  "null values are treated as absent",
			raw:   `{"query":"q","cursor":null}`,
			specs: searchSpecs,
			want:  map[string]any{"query": "q"},
		},
		{
			// json.Unmarshal decodes "null" to a nil map without error; that
			// must mean absent (matching the literal-null rule above), never
			// a present-but-nil object.
			name:  "stringified null object is treated as absent",
			raw:   `{"operation_id":"op_abc","inputs":"null"}`,
			specs: inputsSpecs,
			want:  map[string]any{"operation_id": "op_abc"},
		},
		{
			name:    "conflicting alias values error",
			raw:     `{"id":"op_abc","operation_id":"op_xyz"}`,
			specs:   inspectSpecs,
			wantErr: "aliases",
		},
		{
			name:    "non-object arguments error",
			raw:     `["op_abc"]`,
			specs:   inspectSpecs,
			wantErr: "JSON object",
		},
		{
			name:    "fractional limit errors",
			raw:     `{"query":"q","limit":2.5}`,
			specs:   searchSpecs,
			wantErr: "integer",
		},
		{
			name:    "multi-element list where a string is expected errors",
			raw:     `{"operation_id":["op_a","op_b"]}`,
			specs:   inspectSpecs,
			wantErr: "list of 2 values",
		},
		{
			name:    "non-string list element errors",
			raw:     `{"query":"q","apis":[{"vendor":"acme"}]}`,
			specs:   searchSpecs,
			wantErr: "list element",
		},
		{
			name:    "non-object inputs errors",
			raw:     `{"inputs":42}`,
			specs:   inputsSpecs,
			wantErr: "JSON object",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			var raw json.RawMessage
			if tc.raw != "" {
				raw = json.RawMessage(tc.raw)
			}
			got, err := normalizeToolArgs(raw, tc.specs)
			if tc.wantErr != "" {
				if err == nil {
					t.Fatalf("want error containing %q, got %v", tc.wantErr, got)
				}
				if !strings.Contains(err.Error(), tc.wantErr) {
					t.Fatalf("error %q missing %q", err.Error(), tc.wantErr)
				}
				return
			}
			if err != nil {
				t.Fatalf("normalizeToolArgs: %v", err)
			}
			if !reflect.DeepEqual(got, tc.want) {
				t.Errorf("normalized = %#v, want %#v", got, tc.want)
			}
		})
	}
}

// TestNormalizeToolArgs_NumberCoercesToString pins the number→string tolerance
// separately (a model sending a bare numeric id must not lose the call).
func TestNormalizeToolArgs_NumberCoercesToString(t *testing.T) {
	got, err := normalizeToolArgs(json.RawMessage(`{"id":42}`), inspectOperationParams)
	if err != nil {
		t.Fatalf("normalizeToolArgs: %v", err)
	}
	if got["operation_id"] != "42" {
		t.Errorf("operation_id = %v, want \"42\"", got["operation_id"])
	}
}
