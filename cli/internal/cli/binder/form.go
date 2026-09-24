package binder

import (
	"errors"
	"fmt"
	"reflect"
	"strconv"
	"strings"

	"github.com/charmbracelet/huh"
)

// FormOptions tune the generated interactive form. Exclude drops leaves (the
// installer passes the sensitive-path set so secrets are never prompted as
// plain form fields — they belong in the secret-generation flow, impl/6.0).
type FormOptions struct {
	Exclude map[string]bool
}

// BuildDynamicForm inspects the nested config struct and generates a grouped
// interactive huh.Form: one huh.Group per top-level section, one huh.Field per
// scalar leaf inside it (impl/6.1 §3). Grouping is STRUCTURAL — AppConfig's
// section models already partition every leaf into the logical clusters the
// wizard wants, so there are no group tags to inject into the read-only generated
// struct. Deeper scalar leaves surface under their dotted path within their
// section's screen, matching the flag flatten rule. Non-scalar leaves
// (slices/maps) and excluded (sensitive) leaves are skipped — reachable only via
// flags/YAML.
//
// The returned form's fields bind directly to the struct's leaves, so on
// form.Run() completion the config is populated in place. Pointer sections/leaves
// are allocated first (via the shared walk) so Bubble Tea never nil-derefs.
func BuildDynamicForm(target interface{}, opts FormOptions) *huh.Form {
	v := reflect.ValueOf(target)
	if v.Kind() != reflect.Pointer || v.Elem().Kind() != reflect.Struct {
		panic("binder: BuildDynamicForm target must be a non-nil pointer to a struct")
	}
	// Reuse the exact same leaf walk BindFlags uses, so the form and the flags can
	// never disagree on which leaves exist, their paths, or their kinds. Group the
	// leaves by their top-level section (the first path segment).
	leaves := collectLeaves(target)
	order, bySection := groupLeaves(leaves, opts.Exclude)

	var groups []*huh.Group
	for _, section := range order {
		var fields []huh.Field
		for _, lf := range bySection[section] {
			if f := fieldFor(lf); f != nil {
				fields = append(fields, f)
			}
		}
		if len(fields) > 0 {
			groups = append(groups, huh.NewGroup(fields...).Title(section))
		}
	}
	return huh.NewForm(groups...)
}

// groupLeaves partitions leaves by their top-level section, preserving
// first-seen section order for a stable screen sequence, and dropping excluded
// (sensitive) leaves.
func groupLeaves(leaves []leaf, exclude map[string]bool) ([]string, map[string][]leaf) {
	var order []string
	by := map[string][]leaf{}
	for _, lf := range leaves {
		if exclude[lf.path] {
			continue
		}
		section := lf.path
		if i := strings.IndexByte(lf.path, '.'); i >= 0 {
			section = lf.path[:i]
		}
		if _, seen := by[section]; !seen {
			order = append(order, section)
		}
		by[section] = append(by[section], lf)
	}
	return order, by
}

// fieldFor builds the huh.Field for one scalar leaf, bound to its address so the
// form writes back in place. Named string types (go-jsonschema emits enums as
// `type Foo string`) do not assert to *string, so string leaves use a reflect
// bridge that copies the entered value back on completion. Kinds we do not render
// interactively (they stay flag/YAML-only) return nil.
func fieldFor(lf leaf) huh.Field {
	addr := lf.value.Addr()
	title := lf.path
	switch lf.value.Kind() {
	case reflect.String:
		if ptr, ok := addr.Interface().(*string); ok {
			return huh.NewInput().Title(title).Value(ptr)
		}
		return newReflectStringInput(title, lf.value)
	case reflect.Bool:
		if ptr, ok := addr.Interface().(*bool); ok {
			return huh.NewConfirm().Title(title).Value(ptr)
		}
		return nil
	default:
		// Ints/floats/uints: rendered as validated text inputs bound through a
		// reflect bridge so the operator can still set them interactively.
		return newReflectScalarInput(title, lf.value)
	}
}

// newReflectStringInput bridges a named-string leaf through a temp *string,
// copying the entered value back via reflection when the input is accepted.
func newReflectStringInput(title string, dst reflect.Value) huh.Field {
	tmp := dst.String()
	return huh.NewInput().Title(title).Value(&tmp).Validate(func(s string) error {
		dst.SetString(s)
		return nil
	})
}

// newReflectScalarInput renders a numeric leaf as a text input, parsing and
// writing back through reflection on each keystroke via Validate (which huh calls
// to gate acceptance). An unparseable value is surfaced as a validation error
// rather than silently dropped.
func newReflectScalarInput(title string, dst reflect.Value) huh.Field {
	tmp := scalarString(dst)
	return huh.NewInput().Title(title).Value(&tmp).Validate(func(s string) error {
		return assignScalarString(dst, s)
	})
}

// scalarString renders the current numeric leaf value as its default text.
func scalarString(v reflect.Value) string {
	switch v.Kind() {
	case reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64:
		return strconv.FormatInt(v.Int(), 10)
	case reflect.Uint, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64:
		return strconv.FormatUint(v.Uint(), 10)
	case reflect.Float32, reflect.Float64:
		return strconv.FormatFloat(v.Float(), 'g', -1, 64)
	default:
		return ""
	}
}

// assignScalarString parses s into the numeric leaf, returning a validation error
// on malformed input (empty is accepted as "leave unchanged").
func assignScalarString(v reflect.Value, s string) error {
	if s == "" {
		return nil
	}
	switch v.Kind() {
	case reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64:
		var n int64
		if _, err := fmt.Sscanf(s, "%d", &n); err != nil {
			return errors.New("must be a whole number")
		}
		v.SetInt(n)
	case reflect.Uint, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64:
		var n uint64
		if _, err := fmt.Sscanf(s, "%d", &n); err != nil {
			return errors.New("must be a non-negative whole number")
		}
		v.SetUint(n)
	case reflect.Float32, reflect.Float64:
		var f float64
		if _, err := fmt.Sscanf(s, "%g", &f); err != nil {
			return errors.New("must be a number")
		}
		v.SetFloat(f)
	}
	return nil
}
