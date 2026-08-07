// Package ctl holds the config-driven machinery layered onto the shipped
// jenticctl installer (impl/6.0): the embedded backend config schema, the
// defaults<preset<flags precedence ladder, and the overlay that merges the
// resolved settings into the generated jentic-one.yaml. It is additive — the
// mature install.Draft/RunWizard flow is preserved; these are the schema-driven
// --section-field flags and presets that sit on top of it (plan.md Phase 6).
package ctl

import (
	"embed"
	"encoding/json"
	"fmt"
	"strings"
)

// schemaFS embeds the vendored backend config schema so jenticctl is
// self-contained (impl/6.2). It is EXACTLY the document `make generate-config`
// generated BackendConfig from and `check-ctl-gen` pins to AppConfig, so the
// flags, the defaults applied here, and the struct can never disagree.
//
//go:embed assets/config-schema.json
var schemaFS embed.FS

// presetFS embeds the curated installer presets. Presets are small, explicit
// partial-config override sets kept ALIGNED with the shipped reference configs
// (config/local.yaml, production.yaml.example) — the flag-layer equivalent of
// those files, not a second opinion. Unknown preset => error (fail loud).
//
//go:embed assets/presets/*.json
var presetFS embed.FS

// SchemaBytes returns the embedded config schema (for the binder/form which read
// titles/descriptions/defaults from the same document).
func SchemaBytes() ([]byte, error) {
	raw, err := schemaFS.ReadFile("assets/config-schema.json")
	if err != nil {
		return nil, fmt.Errorf("embedded config schema missing: %w", err)
	}
	return raw, nil
}

// Settings is the resolved configuration as a nested map (section → key → value),
// the currency of the precedence ladder. We work in map space rather than the
// typed BackendConfig because the generated struct's UnmarshalJSON enforces the
// schema's required sections (databases and its sub-DBs), which a *partial*
// defaults/preset/flag layer legitimately omits — merging maps sidesteps that
// while still producing exactly the nested shape the YAML overlay wants
// (impl/6.0 §3.5). The typed struct is used only by the binder/form to ENUMERATE
// flags (a zero value suffices there), never as the merge currency.
type Settings map[string]any

// ResolveSettings applies the precedence ladder (schema defaults < preset <
// explicit flag overrides, impl/6.0 §3.5) entirely in map space and returns the
// merged nested settings, ready to overlay onto the generated jentic-one.yaml.
// flagOverrides is the nested map the binder produced from the operator's
// explicitly-set --section-field flags (only set leaves; may be nil).
func ResolveSettings(preset string, flagOverrides Settings) (Settings, error) {
	merged, err := defaultSettings()
	if err != nil {
		return nil, err
	}
	if preset != "" {
		p, err := PresetSettings(preset)
		if err != nil {
			return nil, err
		}
		MergeSettings(merged, p)
	}
	if flagOverrides != nil {
		MergeSettings(merged, flagOverrides)
	}
	return merged, nil
}

// defaultSettings returns the schema-declared defaults as a nested map.
func defaultSettings() (Settings, error) {
	raw, err := SchemaBytes()
	if err != nil {
		return nil, err
	}
	var doc schemaDoc
	if err := json.Unmarshal(raw, &doc); err != nil {
		return nil, fmt.Errorf("parse embedded config schema: %w", err)
	}
	return doc.buildDefaults(doc.Properties), nil
}

// PresetSettings returns the named embedded preset as a nested map. Unknown
// preset => loud error.
func PresetSettings(name string) (Settings, error) {
	raw, err := presetFS.ReadFile("assets/presets/" + name + ".json")
	if err != nil {
		return nil, fmt.Errorf("unknown preset %q (expected one of the embedded presets, e.g. local-dev, production): %w", name, err)
	}
	var s Settings
	if err := json.Unmarshal(raw, &s); err != nil {
		return nil, fmt.Errorf("parse preset %q: %w", name, err)
	}
	return s, nil
}

// MergeSettings deep-merges src into dst: nested maps recurse (so a preset that
// names one leaf of a section does not wipe the section's other defaulted
// leaves), scalars overwrite. This is what produces the layered precedence
// without any per-field copying.
func MergeSettings(dst, src Settings) {
	for k, sv := range src {
		if svMap, ok := asSettings(sv); ok {
			if dvMap, ok := asSettings(dst[k]); ok {
				MergeSettings(dvMap, svMap)
				dst[k] = dvMap
				continue
			}
		}
		dst[k] = sv
	}
}

// asSettings coerces a decoded JSON object (map[string]any or Settings) to a
// Settings for recursive merging; returns false for scalars/slices.
func asSettings(v any) (Settings, bool) {
	switch m := v.(type) {
	case Settings:
		return m, true
	case map[string]any:
		return Settings(m), true
	default:
		return nil, false
	}
}

// SensitivePaths returns the set of dotted config paths (server.public_base_url
// style) whose leaves carry a secret, derived from the embedded schema: a leaf is
// sensitive if the schema marks it writeOnly or format=password (Pydantic emits
// both for SecretStr), or its name matches the secret-name heuristic as a
// backstop (secret/password/passwd/pepper/token/private_key/client_secret). The
// installer excludes these from the generated --<section>-<field> flags:
// credentials belong in the wizard's secret-generation/.env flow, never on a
// command line where they land in `ps`, shell history, and --help (impl/6.0;
// same fail-closed posture as the ux redactor).
func SensitivePaths() (map[string]bool, error) {
	raw, err := SchemaBytes()
	if err != nil {
		return nil, err
	}
	var doc schemaDoc
	if err := json.Unmarshal(raw, &doc); err != nil {
		return nil, fmt.Errorf("parse embedded config schema: %w", err)
	}
	out := map[string]bool{}
	doc.collectSensitive(doc.Properties, "", out)
	return out, nil
}

func (d schemaDoc) collectSensitive(props map[string]schemaNode, prefix string, out map[string]bool) {
	for name, node := range props {
		path := name
		if prefix != "" {
			path = prefix + "." + name
		}
		resolved := d.resolve(node)
		if len(resolved.Properties) > 0 {
			d.collectSensitive(resolved.Properties, path, out)
			continue
		}
		if node.WriteOnly || node.Format == "password" || sensitiveName(name) {
			out[path] = true
		}
	}
}

// sensitiveName is the name-based backstop for schema leaves that carry a secret
// but predate the writeOnly/format markers. Matched case-insensitively as a
// substring so compound names (state_secret, jwt_secret, client_secret) are caught.
func sensitiveName(name string) bool {
	lower := strings.ToLower(name)
	for _, needle := range []string{"secret", "password", "passwd", "pepper", "private_key", "token", "material"} {
		if strings.Contains(lower, needle) {
			return true
		}
	}
	return false
}

// Presets returns the names of the embedded presets, for help text and validation.
func Presets() []string {
	entries, err := presetFS.ReadDir("assets/presets")
	if err != nil {
		return nil
	}
	names := make([]string, 0, len(entries))
	for _, e := range entries {
		if e.IsDir() {
			continue
		}
		name := e.Name()
		if len(name) > len(".json") && name[len(name)-len(".json"):] == ".json" {
			names = append(names, name[:len(name)-len(".json")])
		}
	}
	return names
}

// schemaDoc is the minimal slice of JSON Schema buildDefaults needs: the root
// properties, and the $defs it resolves $refs into. Everything else is ignored.
type schemaDoc struct {
	Properties map[string]schemaNode `json:"properties"`
	Defs       map[string]schemaNode `json:"$defs"`
}

type schemaNode struct {
	Type       string                `json:"type"`
	Default    json.RawMessage       `json:"default"`
	Ref        string                `json:"$ref"`
	Format     string                `json:"format"`
	WriteOnly  bool                  `json:"writeOnly"`
	Properties map[string]schemaNode `json:"properties"`
	// allOf carries a single $ref for a section modelled as a sub-object with a
	// default_factory (Pydantic emits {"allOf":[{"$ref":...}], "default":...} or
	// just {"$ref":...} depending on version); we resolve either.
	AllOf []schemaNode `json:"allOf"`
}

// buildDefaults walks the given property set and assembles a nested map of the
// declared defaults, resolving $ref/allOf into $defs so section leaf-defaults are
// included (a section with a default_factory carries no top-level "default", so
// reading only root "properties" defaults would silently skip every section).
func (d schemaDoc) buildDefaults(props map[string]schemaNode) map[string]any {
	out := map[string]any{}
	for name, node := range props {
		resolved := d.resolve(node)
		if len(resolved.Properties) > 0 {
			// A sub-object section: recurse into its leaves.
			if sub := d.buildDefaults(resolved.Properties); len(sub) > 0 {
				out[name] = sub
			}
			continue
		}
		// A scalar leaf: take its declared default if present.
		if len(node.Default) > 0 {
			var v any
			if err := json.Unmarshal(node.Default, &v); err == nil && v != nil {
				out[name] = v
			}
		}
	}
	return out
}

// resolve follows a $ref (or a single-element allOf $ref) into $defs, returning
// the pointed-at node. A node with inline properties or no ref is returned as-is.
func (d schemaDoc) resolve(node schemaNode) schemaNode {
	ref := node.Ref
	if ref == "" && len(node.AllOf) == 1 {
		ref = node.AllOf[0].Ref
	}
	if ref == "" {
		return node
	}
	// $ref is "#/$defs/<Name>".
	const prefix = "#/$defs/"
	if len(ref) > len(prefix) && ref[:len(prefix)] == prefix {
		if def, ok := d.Defs[ref[len(prefix):]]; ok {
			return def
		}
	}
	return node
}
