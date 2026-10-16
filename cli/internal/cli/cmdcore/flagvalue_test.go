package cmdcore

import (
	"testing"

	"github.com/spf13/cobra"
)

// TestFlagValue_ReadsRootPersistentNotLocalShadow pins #1412: a subcommand's
// local flag that shares a name with a root session flag must not be read as
// the session value, while the root flag still reaches every command that does
// not shadow it. (The api tree forbids such shadowing outright; this keeps the
// lookup itself honest.)
func TestFlagValue_ReadsRootPersistentNotLocalShadow(t *testing.T) {
	newRoot := func() (root, shadow, plain *cobra.Command) {
		root = &cobra.Command{Use: "jentic"}
		root.PersistentFlags().String("mode", "", "")
		shadow = &cobra.Command{Use: "create", Run: func(*cobra.Command, []string) {}}
		shadow.Flags().String("mode", "", "")
		plain = &cobra.Command{Use: "use", Run: func(*cobra.Command, []string) {}}
		root.AddCommand(shadow, plain)
		return root, shadow, plain
	}

	t.Run("local shadow is ignored", func(t *testing.T) {
		root, shadow, _ := newRoot()
		root.SetArgs([]string{"create", "--mode", "agent"})
		if err := root.Execute(); err != nil {
			t.Fatalf("execute: %v", err)
		}
		if got := flagValue(shadow, "mode"); got != "" {
			t.Errorf("flagValue(mode) = %q, want \"\" (local flag must not leak into the session mode)", got)
		}
	})

	t.Run("root flag is read on unshadowed command", func(t *testing.T) {
		root, _, plain := newRoot()
		root.SetArgs([]string{"--mode", "agent", "use"})
		if err := root.Execute(); err != nil {
			t.Fatalf("execute: %v", err)
		}
		if got := flagValue(plain, "mode"); got != "agent" {
			t.Errorf("flagValue(mode) = %q, want agent", got)
		}
	})
}
