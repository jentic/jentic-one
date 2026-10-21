package main

import (
	"encoding/json"
	"go/parser"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestHasNonZeroDefault(t *testing.T) {
	cases := []struct {
		name string
		raw  string
		want bool
	}{
		{name: "empty", raw: "", want: false},
		{name: "invalid_json", raw: "{not-json", want: false},
		{name: "null", raw: "null", want: false},
		{name: "bool_false", raw: "false", want: false},
		{name: "bool_true", raw: "true", want: true},
		{name: "int_zero", raw: "0", want: false},
		{name: "float_zero", raw: "0.0", want: false},
		{name: "negative_zero", raw: "-0.0", want: false},
		{name: "int_positive", raw: "900", want: true},
		{name: "float_positive", raw: "1.0", want: true},
		{name: "int_negative", raw: "-1", want: true},
		{name: "string_empty", raw: `""`, want: false},
		{name: "string_non_empty", raw: `"postgres"`, want: true},
		{name: "array_empty", raw: "[]", want: false},
		{name: "array_non_empty", raw: `["a"]`, want: true},
		{name: "object_empty", raw: "{}", want: false},
		{name: "object_non_empty", raw: `{"k": 1}`, want: true},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := hasNonZeroDefault(json.RawMessage(tc.raw)); got != tc.want {
				t.Errorf("hasNonZeroDefault(%s) = %v, want %v", tc.raw, got, tc.want)
			}
		})
	}
}

func TestIsZeroExpr(t *testing.T) {
	cases := []struct {
		expr string
		want bool
	}{
		{expr: "false", want: true},
		{expr: "nil", want: true},
		{expr: "true", want: false},
		{expr: `""`, want: true},
		{expr: `"GET"`, want: false},
		{expr: "0", want: true},
		{expr: "0.0", want: true},
		{expr: "-0", want: true},
		{expr: "-0.0", want: true},
		{expr: "+0", want: true},
		{expr: "(0)", want: true},
		{expr: "1", want: false},
		{expr: "-1", want: false},
		{expr: "1.5", want: false},
		{expr: "[]string{}", want: true},
		{expr: `[]string{"a"}`, want: false},
	}

	for _, tc := range cases {
		t.Run(tc.expr, func(t *testing.T) {
			parsed, err := parser.ParseExpr(tc.expr)
			if err != nil {
				t.Fatalf("ParseExpr(%q): %v", tc.expr, err)
			}
			if got := isZeroExpr(parsed); got != tc.want {
				t.Errorf("isZeroExpr(%s) = %v, want %v", tc.expr, got, tc.want)
			}
		})
	}
}

func TestRun_StripsNonZeroDefaultOmitTagsAndStaysIdempotent(t *testing.T) {
	dir := t.TempDir()
	schemaPath := filepath.Join(dir, "schema.json")
	goPath := filepath.Join(dir, "config.go")

	const schema = `{
		"properties": {
			"root_port": {"type": "integer", "default": 8080},
			"root_debug": {"type": "boolean", "default": false}
		},
		"$defs": {
			"SubConfig": {
				"properties": {
					"sample_ratio": {"type": "number", "default": 1.0},
					"enabled": {"type": "boolean", "default": true},
					"empty_str": {"type": "string", "default": ""}
				}
			}
		}
	}`
	const goSrc = `package generated

import "encoding/json"

type ConfigSchemaJson struct {
	RootPort  int  ` + "`json:\"root_port,omitempty,omitzero\" yaml:\"root_port,omitempty\" mapstructure:\"root_port,omitempty\"`" + `
	RootDebug bool ` + "`json:\"root_debug,omitempty,omitzero\" yaml:\"root_debug,omitempty\" mapstructure:\"root_debug,omitempty\"`" + `
}

func (j *ConfigSchemaJson) UnmarshalJSON(value []byte) error {
	var raw map[string]interface{}
	if err := json.Unmarshal(value, &raw); err != nil {
		return err
	}
	type Plain ConfigSchemaJson
	var plain Plain
	if err := json.Unmarshal(value, &plain); err != nil {
		return err
	}
	if v, ok := raw["root_port"]; !ok || v == nil {
		plain.RootPort = 8080
	}
	if v, ok := raw["root_debug"]; !ok || v == nil {
		plain.RootDebug = false
	}
	*j = ConfigSchemaJson(plain)
	return nil
}

type SubConfig struct {
	SampleRatio float64 ` + "`json:\"sample_ratio,omitempty,omitzero\" yaml:\"sample_ratio,omitempty\" mapstructure:\"sample_ratio,omitempty\"`" + `
	Enabled     bool    ` + "`json:\"enabled,omitempty,omitzero\" yaml:\"enabled,omitempty\" mapstructure:\"enabled,omitempty\"`" + `
	EmptyStr    string  ` + "`json:\"empty_str,omitempty,omitzero\" yaml:\"empty_str,omitempty\" mapstructure:\"empty_str,omitempty\"`" + `
}

func (j *SubConfig) UnmarshalJSON(value []byte) error {
	var raw map[string]interface{}
	if err := json.Unmarshal(value, &raw); err != nil {
		return err
	}
	type Plain SubConfig
	var plain Plain
	if err := json.Unmarshal(value, &plain); err != nil {
		return err
	}
	if v, ok := raw["sample_ratio"]; !ok || v == nil {
		plain.SampleRatio = 1.0
	}
	if v, ok := raw["enabled"]; !ok || v == nil {
		plain.Enabled = true
	}
	if v, ok := raw["empty_str"]; !ok || v == nil {
		plain.EmptyStr = ""
	}
	*j = SubConfig(plain)
	return nil
}
`

	if err := os.WriteFile(schemaPath, []byte(schema), 0o600); err != nil {
		t.Fatalf("write schema: %v", err)
	}
	if err := os.WriteFile(goPath, []byte(goSrc), 0o600); err != nil {
		t.Fatalf("write go file: %v", err)
	}

	if err := run(schemaPath, goPath); err != nil {
		t.Fatalf("first run: %v", err)
	}
	afterFirst, err := os.ReadFile(goPath)
	if err != nil {
		t.Fatalf("read after first run: %v", err)
	}
	content := string(afterFirst)

	for _, want := range []string{
		"`json:\"root_port\" yaml:\"root_port\" mapstructure:\"root_port\"`",
		"`json:\"sample_ratio\" yaml:\"sample_ratio\" mapstructure:\"sample_ratio\"`",
		"`json:\"enabled\" yaml:\"enabled\" mapstructure:\"enabled\"`",
		"`json:\"root_debug,omitempty,omitzero\" yaml:\"root_debug,omitempty\" mapstructure:\"root_debug,omitempty\"`",
		"`json:\"empty_str,omitempty,omitzero\" yaml:\"empty_str,omitempty\" mapstructure:\"empty_str,omitempty\"`",
	} {
		if !strings.Contains(content, want) {
			t.Errorf("expected patched output to contain %s; got:\n%s", want, content)
		}
	}

	// Second run on already-patched output must succeed without altering the file.
	if err := run(schemaPath, goPath); err != nil {
		t.Fatalf("second run (idempotency): %v", err)
	}
	afterSecond, err := os.ReadFile(goPath)
	if err != nil {
		t.Fatalf("read after second run: %v", err)
	}
	if string(afterSecond) != content {
		t.Errorf("second run modified file:\nwant:\n%s\ngot:\n%s", content, string(afterSecond))
	}
}

func TestRun_FailsOnSchemaVsASTDefaultMismatch(t *testing.T) {
	dir := t.TempDir()
	schemaPath := filepath.Join(dir, "schema.json")
	goPath := filepath.Join(dir, "config.go")

	const schema = `{
		"$defs": {
			"SubConfig": {
				"properties": {
					"sample_ratio": {"type": "number", "default": 1.0}
				}
			}
		}
	}`
	const goSrc = `package generated

type SubConfig struct {
	SampleRatio float64 ` + "`json:\"sample_ratio,omitempty,omitzero\" yaml:\"sample_ratio,omitempty\" mapstructure:\"sample_ratio,omitempty\"`" + `
}
`
	if err := os.WriteFile(schemaPath, []byte(schema), 0o600); err != nil {
		t.Fatalf("write schema: %v", err)
	}
	if err := os.WriteFile(goPath, []byte(goSrc), 0o600); err != nil {
		t.Fatalf("write go file: %v", err)
	}

	err := run(schemaPath, goPath)
	if err == nil || !strings.Contains(err.Error(), "SubConfig.sample_ratio") {
		t.Fatalf("expected mismatch error naming SubConfig.sample_ratio, got %v", err)
	}
}

func TestRun_FailsWhenStructFieldMissing(t *testing.T) {
	dir := t.TempDir()
	schemaPath := filepath.Join(dir, "schema.json")
	goPath := filepath.Join(dir, "config.go")

	const schema = `{
		"$defs": {
			"SubConfig": {
				"properties": {
					"sample_ratio": {"type": "number", "default": 1.0}
				}
			}
		}
	}`
	const goSrc = `package generated

import "encoding/json"

type SubConfig struct {
	Other float64 ` + "`json:\"other,omitempty,omitzero\" yaml:\"other,omitempty\" mapstructure:\"other,omitempty\"`" + `
}

func (j *SubConfig) UnmarshalJSON(value []byte) error {
	var raw map[string]interface{}
	if err := json.Unmarshal(value, &raw); err != nil {
		return err
	}
	type Plain SubConfig
	var plain Plain
	if v, ok := raw["sample_ratio"]; !ok || v == nil {
		plain.Other = 1.0
	}
	*j = SubConfig(plain)
	return nil
}
`
	if err := os.WriteFile(schemaPath, []byte(schema), 0o600); err != nil {
		t.Fatalf("write schema: %v", err)
	}
	if err := os.WriteFile(goPath, []byte(goSrc), 0o600); err != nil {
		t.Fatalf("write go file: %v", err)
	}

	err := run(schemaPath, goPath)
	if err == nil || !strings.Contains(err.Error(), "SubConfig.sample_ratio") {
		t.Fatalf("expected unmatched field error naming SubConfig.sample_ratio, got %v", err)
	}
}
