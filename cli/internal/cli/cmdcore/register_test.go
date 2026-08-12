package cmdcore

import (
	"testing"

	"github.com/spf13/cobra"
)

func TestFlagsAllowPrompt(t *testing.T) {
	newCmd := func() *cobra.Command {
		c := &cobra.Command{Use: "register", Run: func(*cobra.Command, []string) {}}
		c.Flags().String("url", "", "")
		c.Flags().String("env", "", "")
		c.Flags().String("name", "", "")
		return c
	}

	if !flagsAllowPrompt(newCmd(), false, registerFieldFlags...) {
		t.Errorf("bare register (no flags, no --yes) should allow prompting")
	}
	if flagsAllowPrompt(newCmd(), true, registerFieldFlags...) {
		t.Errorf("--yes should suppress prompting")
	}

	for _, f := range registerFieldFlags {
		c := newCmd()
		if err := c.Flags().Set(f, "x"); err != nil {
			t.Fatalf("set %s: %v", f, err)
		}
		if flagsAllowPrompt(c, false, registerFieldFlags...) {
			t.Errorf("setting --%s should suppress prompting", f)
		}
	}
}

// TestBootstrapFlagsAllowPrompt proves bootstrap's extended field-flag set
// treats a flag-driven skill-target run (e.g. --operator) as non-interactive,
// where the bare register set would still prompt.
func TestBootstrapFlagsAllowPrompt(t *testing.T) {
	newCmd := func() *cobra.Command {
		c := &cobra.Command{Use: "bootstrap", Run: func(*cobra.Command, []string) {}}
		c.Flags().String("url", "", "")
		c.Flags().String("env", "", "")
		c.Flags().String("name", "", "")
		c.Flags().StringSlice("operator", nil, "")
		c.Flags().Bool("all", false, "")
		c.Flags().String("scope", "", "")
		c.Flags().Bool("skip-skill", false, "")
		return c
	}

	if !flagsAllowPrompt(newCmd(), false, bootstrapFieldFlags...) {
		t.Errorf("bare bootstrap (no flags, no --yes) should allow prompting")
	}
	for _, f := range []string{"operator", "all", "scope", "skip-skill"} {
		c := newCmd()
		if err := c.Flags().Set(f, valueFor(f)); err != nil {
			t.Fatalf("set %s: %v", f, err)
		}
		if flagsAllowPrompt(c, false, bootstrapFieldFlags...) {
			t.Errorf("setting --%s should suppress prompting for bootstrap", f)
		}
	}
}

func valueFor(flag string) string {
	switch flag {
	case "all", "skip-skill":
		return "true"
	case "operator":
		return "claude"
	default:
		return "x"
	}
}
