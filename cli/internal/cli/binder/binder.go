// Package binder builds Cobra flags (and, in form.go, an interactive huh form)
// from a Go struct by reflection. In V1 it ships for exactly one consumer: the
// jenticctl installer's generated BackendConfig (impl/6.0 §3) — the API-surface
// binder described in impl/2.2 stays a post-V1 exploration (00_openapi_gen.md §3).
//
// The installer config is NESTED and not uniformly one level deep
// (databases.registry.host is depth 2; security.jwt_verification.* nests
// further), so the binder walks the struct recursively and binds every SCALAR
// leaf under its full dotted path with dots/underscores rendered as dashes:
// server.public_base_url → --server-public-base-url,
// databases.registry.host → --databases-registry-host. That is deliberately the
// same naming rule as the backend's JENTIC__SECTION__KEY env overrides and the
// YAML keys — one mental model across flags, env, and file. Non-scalar leaves
// (slices, maps) are skipped: they stay reachable only via YAML/--out, never a
// flag (impl/6.0 §3, "flatten scalars, never collections").
//
// Two lessons from impl/2.2 §1a carry over: unmapped scalar-ish kinds FAIL LOUD
// (panic at construction, caught in CI, never a silently-undsettable field), and
// pointer-ness is used only for allocation during hydration, never as a semantic
// signal.
package binder

import (
	"fmt"
	"reflect"
	"strings"

	"github.com/spf13/cobra"
	"github.com/spf13/pflag"
)

// maxDepth bounds the recursive walk. The real BackendConfig nests at most ~3
// levels; a much larger depth means a cycle or a pathological schema, which we
// refuse loudly rather than overflow the stack. Tests assert the real config
// stays well under this.
const maxDepth = 8

// leaf is one scalar field discovered by the walk: its dotted path (json names,
// e.g. "databases.registry.host") and the reflect.Value to read/write.
type leaf struct {
	path  string
	value reflect.Value
}

// flagName converts a dotted json path to a kebab-case flag name:
// "server.public_base_url" → "server-public-base-url".
func flagName(dottedPath string) string {
	return strings.ReplaceAll(strings.ReplaceAll(dottedPath, ".", "-"), "_", "-")
}

// jsonName returns the field's json property name (tag minus options), or "" when
// the field is unexported or explicitly json-ignored ("-").
func jsonName(f reflect.StructField) string {
	tag := f.Tag.Get("json")
	if tag == "" || tag == "-" {
		return ""
	}
	name := strings.Split(tag, ",")[0]
	if name == "-" {
		return ""
	}
	return name
}

// walkLeaves recursively collects every scalar leaf under v, threading the dotted
// path prefix. Pointers are followed (allocating along the way so hydration has a
// place to write); nested structs recurse; slices/maps/other collections are
// skipped (flag/YAML-only). Unsupported scalar-ish kinds panic (§ Problem B).
func walkLeaves(v reflect.Value, prefix string, depth int, out *[]leaf) {
	if depth > maxDepth {
		panic(fmt.Sprintf("binder: config nesting exceeded max depth %d at %q (cycle or pathological schema?)", maxDepth, prefix))
	}
	t := v.Type()
	for i := range t.NumField() {
		field := t.Field(i)
		name := jsonName(field)
		if name == "" {
			continue
		}
		path := name
		if prefix != "" {
			path = prefix + "." + name
		}

		fv := v.Field(i)
		ft := field.Type

		// Follow a pointer, allocating so the leaf is addressable for hydration.
		if ft.Kind() == reflect.Pointer {
			if fv.IsNil() {
				if !fv.CanSet() {
					continue
				}
				fv.Set(reflect.New(ft.Elem()))
			}
			fv = fv.Elem()
			ft = ft.Elem()
		}

		switch ft.Kind() {
		case reflect.Struct:
			walkLeaves(fv, path, depth+1, out)
		case reflect.String, reflect.Bool,
			reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64,
			reflect.Uint, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64,
			reflect.Float32, reflect.Float64:
			*out = append(*out, leaf{path: path, value: fv})
		case reflect.Slice, reflect.Map, reflect.Array, reflect.Interface:
			// Collections and free-form maps have no flat-scalar flag form; they
			// stay reachable via YAML/--out only (impl/6.0 §3). Skip, don't fail.
			continue
		default:
			// A kind we neither map nor can safely leave to YAML (chan, func,
			// complex, uintptr, …). Fail loud at construction so CI catches the
			// gap the moment the schema introduces it (impl/2.2 §1a Problem B).
			panic(fmt.Sprintf(
				"binder: field %q (path %q) has unsupported kind %s; extend the binder or adjust the config schema",
				field.Name, path, ft.Kind(),
			))
		}
	}
}

// collectLeaves validates target is a struct pointer and returns its scalar
// leaves. Shared by BindFlags, HydrateStruct, and the form generator so all three
// agree on exactly which leaves exist and under which paths.
func collectLeaves(target interface{}) []leaf {
	v := reflect.ValueOf(target)
	if v.Kind() != reflect.Pointer || v.Elem().Kind() != reflect.Struct {
		panic("binder: target must be a non-nil pointer to a struct")
	}
	var leaves []leaf
	walkLeaves(v.Elem(), "", 0, &leaves)
	return leaves
}

// LeafPaths returns the dotted path of every scalar leaf of the nested config
// struct, in walk order. Callers use it to build Exclude sets for a whole
// section (e.g. everything under `telemetry.`) without duplicating the leaf
// walk that BindFlags and the form generator share.
func LeafPaths(target interface{}) []string {
	leaves := collectLeaves(target)
	out := make([]string, 0, len(leaves))
	for _, lf := range leaves {
		out = append(out, lf.path)
	}
	return out
}

// BindOptions tune BindFlags for the installer's needs (impl/6.0): Exclude drops
// leaves entirely (sensitive secret-bearing paths never become flags), and Hidden
// registers the flags but marks them hidden so they work yet stay out of --help
// and the public cli-reference noise. Paths are dotted (server.public_base_url).
type BindOptions struct {
	Exclude map[string]bool
	Hidden  bool
}

// BindFlags registers one Cobra flag per scalar leaf of the nested config struct,
// named by its dotted path flattened to kebab-case (impl/6.0 §3). It is
// idempotent-safe only on a fresh command; re-binding the same struct onto a
// command that already has a colliding flag will panic via pflag, which is the
// desired loud failure.
func BindFlags(cmd *cobra.Command, target interface{}) {
	BindFlagsWithOptions(cmd, target, BindOptions{})
}

// BindFlagsWithOptions is BindFlags with the installer's Exclude/Hidden controls.
func BindFlagsWithOptions(cmd *cobra.Command, target interface{}, opts BindOptions) {
	for _, lf := range collectLeaves(target) {
		if opts.Exclude[lf.path] {
			continue
		}
		name := flagName(lf.path)
		usage := "Set " + lf.path
		switch lf.value.Kind() {
		case reflect.String:
			cmd.Flags().String(name, "", usage)
		case reflect.Bool:
			cmd.Flags().Bool(name, false, usage)
		case reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64:
			cmd.Flags().Int64(name, 0, usage)
		case reflect.Uint, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64:
			cmd.Flags().Uint64(name, 0, usage)
		case reflect.Float32, reflect.Float64:
			cmd.Flags().Float64(name, 0, usage)
		}
		if opts.Hidden {
			if f := cmd.Flags().Lookup(name); f != nil {
				f.Hidden = true
			}
		}
	}
}

// HydrateStruct writes every EXPLICITLY-SET flag back onto its leaf, leaving
// unset leaves untouched so the defaults<preset<flags precedence ladder
// (impl/6.0 §3.5) holds: applyDefaults/applyPreset populate cfg first, then this
// overrides only what the operator actually passed. Flags the user did not set
// are skipped via pflag's Changed(), never clobbering a preset/default value with
// a flag zero-value.
func HydrateStruct(cmd *cobra.Command, target interface{}) error {
	flags := cmd.Flags()
	for _, lf := range collectLeaves(target) {
		name := flagName(lf.path)
		if !flags.Changed(name) {
			continue
		}
		if err := setLeaf(flags, name, lf.value); err != nil {
			return fmt.Errorf("--%s: %w", name, err)
		}
	}
	return nil
}

// setLeaf reads the parsed flag value for name and assigns it to the leaf,
// matching the flag type registered in BindFlags.
// setLeaf reads the parsed flag value for name and assigns it to the leaf,
// matching the flag type registered in BindFlags.
func setLeaf(flags *pflag.FlagSet, name string, dst reflect.Value) error {
	switch dst.Kind() {
	case reflect.String:
		s, err := flags.GetString(name)
		if err != nil {
			return err
		}
		dst.SetString(s)
	case reflect.Bool:
		b, err := flags.GetBool(name)
		if err != nil {
			return err
		}
		dst.SetBool(b)
	case reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64:
		n, err := flags.GetInt64(name)
		if err != nil {
			return err
		}
		dst.SetInt(n)
	case reflect.Uint, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64:
		n, err := flags.GetUint64(name)
		if err != nil {
			return err
		}
		dst.SetUint(n)
	case reflect.Float32, reflect.Float64:
		f, err := flags.GetFloat64(name)
		if err != nil {
			return err
		}
		dst.SetFloat(f)
	}
	return nil
}

// ChangedOverrides returns a nested map of ONLY the flags the operator explicitly
// set, keyed by the config's dotted path split back into a section tree
// (databases-registry-host, set, → {"databases":{"registry":{"host":...}}}). This
// is the flag layer of the installer's precedence ladder: it feeds straight into
// the settings merge (ctl.ResolveSettings) so unset flags never contribute a
// zero-value that would clobber a schema default or preset (impl/6.0 §3.5). The
// target is used only to enumerate the leaves and their kinds; its field values
// are not read.
func ChangedOverrides(cmd *cobra.Command, target interface{}) (map[string]any, error) {
	flags := cmd.Flags()
	out := map[string]any{}
	for _, lf := range collectLeaves(target) {
		name := flagName(lf.path)
		if !flags.Changed(name) {
			continue
		}
		v, err := flagValue(flags, name, lf.value.Kind())
		if err != nil {
			return nil, fmt.Errorf("--%s: %w", name, err)
		}
		insertPath(out, strings.Split(lf.path, "."), v)
	}
	return out, nil
}

// flagValue reads the parsed flag as the Go value matching the leaf's kind.
func flagValue(flags *pflag.FlagSet, name string, kind reflect.Kind) (any, error) {
	switch kind {
	case reflect.String:
		return flags.GetString(name)
	case reflect.Bool:
		return flags.GetBool(name)
	case reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64:
		return flags.GetInt64(name)
	case reflect.Uint, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64:
		return flags.GetUint64(name)
	case reflect.Float32, reflect.Float64:
		return flags.GetFloat64(name)
	default:
		return nil, fmt.Errorf("unsupported flag kind %s", kind)
	}
}

// insertPath sets nested[path...] = value, creating intermediate maps as needed.
func insertPath(nested map[string]any, path []string, value any) {
	for i := range len(path) - 1 {
		key := path[i]
		child, ok := nested[key].(map[string]any)
		if !ok {
			child = map[string]any{}
			nested[key] = child
		}
		nested = child
	}
	nested[path[len(path)-1]] = value
}

// NonZeroOverrides returns a nested map of the struct's scalar leaves that hold a
// NON-ZERO value, skipping any path in exclude. It is the read-back for the
// interactive config form (impl/6.1): the form binds a fresh struct, the operator
// fills in only what they want to change, and this collects exactly those leaves
// as overrides ("" / 0 / false == "leave the wizard's value alone"). exclude
// carries the sensitive paths so a secret typed into a (non-excluded-by-accident)
// field can never leak into the overlay.
func NonZeroOverrides(target interface{}, exclude map[string]bool) map[string]any {
	out := map[string]any{}
	for _, lf := range collectLeaves(target) {
		if exclude[lf.path] {
			continue
		}
		if lf.value.IsZero() {
			continue
		}
		insertPath(out, strings.Split(lf.path, "."), lf.value.Interface())
	}
	return out
}
