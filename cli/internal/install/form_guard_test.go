package install

import (
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

// TestAllFormsUseSharedConstructors guards theme consistency: every interactive
// form/input in the CLI must be built through install.NewForm / install.Input so
// it inherits the one brand theme, radio selectors, prompt glyph, and quit keys.
// Constructing huh.NewForm()/huh.NewInput() directly anywhere else bypasses the
// theme (that is how the register form once showed a default ">" prompt), so we
// fail the build if any such call appears outside this package's theme.go.
func TestAllFormsUseSharedConstructors(t *testing.T) {
	root := moduleRoot(t)

	// The sanctioned homes of the raw huh constructors: install/theme.go (the
	// shared themed helpers) and the binder's dynamic-form generator, whose output
	// is themed at run time via install.RunForm (impl/6.1 — the one generic
	// schema-driven form builder; it cannot import install without inverting the
	// layering, so it builds raw huh fields and the caller applies the theme).
	allowed := map[string]bool{
		filepath.Join(root, "internal", "install", "theme.go"):      true,
		filepath.Join(root, "internal", "cli", "binder", "form.go"): true,
	}
	forbidden := []string{"huh.NewForm(", "huh.NewInput("}

	err := filepath.WalkDir(root, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() || !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return nil
		}
		if allowed[path] {
			return nil
		}
		data, readErr := os.ReadFile(path)
		if readErr != nil {
			return readErr
		}
		src := string(data)
		for _, tok := range forbidden {
			if strings.Contains(src, tok) {
				rel, _ := filepath.Rel(root, path)
				t.Errorf("%s calls %s directly; use install.NewForm / install.Input instead", rel, tok)
			}
		}
		return nil
	})
	if err != nil {
		t.Fatalf("walk module: %v", err)
	}
}

// TestNoConfirmRunEscapeHatch forbids running a huh confirm on its own
// (huh.NewConfirm()....Run()), which bypasses the shared brand theme and quit
// keymap and reintroduces the swallowed-first-Enter bug. Standalone confirms
// must go through install.RunConfirm; multi-field wizard sections compose
// huh.NewConfirm into install.NewForm groups (sections.go) and never call .Run()
// on the confirm itself.
func TestNoConfirmRunEscapeHatch(t *testing.T) {
	root := moduleRoot(t)
	// theme.go documents and implements the sanctioned helper; exempt it.
	allowed := filepath.Join(root, "internal", "install", "theme.go")
	// Matches huh.NewConfirm()...Run() across lines, with any builder chain in
	// between, but stops at the first Run( so it can't span unrelated calls.
	pattern := regexp.MustCompile(`huh\.NewConfirm\((?s:[^;]*?)\.Run\(`)

	err := filepath.WalkDir(root, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() || !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return nil
		}
		if path == allowed {
			return nil
		}
		data, readErr := os.ReadFile(path)
		if readErr != nil {
			return readErr
		}
		if pattern.Match(data) {
			rel, _ := filepath.Rel(root, path)
			t.Errorf("%s runs a huh confirm directly (huh.NewConfirm()....Run()); use install.RunConfirm instead", rel)
		}
		return nil
	})
	if err != nil {
		t.Fatalf("walk module: %v", err)
	}
}

// moduleRoot walks up from the test's working directory to the dir holding go.mod.
func moduleRoot(t *testing.T) string {
	t.Helper()
	dir, err := os.Getwd()
	if err != nil {
		t.Fatalf("getwd: %v", err)
	}
	for {
		if _, statErr := os.Stat(filepath.Join(dir, "go.mod")); statErr == nil {
			return dir
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			t.Fatalf("go.mod not found from %s upward", dir)
		}
		dir = parent
	}
}
