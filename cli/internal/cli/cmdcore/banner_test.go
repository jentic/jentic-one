package cmdcore

import (
	"bytes"
	"context"
	"testing"

	"github.com/spf13/cobra"
)

// TestInterceptor_BannerIsHumanOnly pins the banner gate: agent harnesses often
// run on a PTY, so a TTY stdout alone must not let the logo onto stdout in a
// machine mode or with --json — that would corrupt the one-JSON-document
// contract. A human on a terminal still gets it.
func TestInterceptor_BannerIsHumanOnly(t *testing.T) {
	orig := stdoutIsTTY
	stdoutIsTTY = func() bool { return true }
	t.Cleanup(func() { stdoutIsTTY = orig })
	t.Setenv("JENTIC_NO_BANNER", "")

	for _, tc := range []struct {
		name       string
		args       []string
		wantBanner bool
	}{
		{"agent mode", []string{"--mode", "agent", "probe"}, false},
		{"unknown mode fails closed", []string{"--mode", "agnet", "probe"}, false},
		{"human with --json", []string{"--mode", "human", "probe", "--json"}, false},
		{"human", []string{"--mode", "human", "probe"}, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			out := new(bytes.Buffer)
			app := &App{Out: out}
			root := &cobra.Command{Use: "jentic", SilenceUsage: true, SilenceErrors: true}
			root.PersistentFlags().String("mode", "", "")
			root.PersistentFlags().String("context", "", "")
			root.PersistentFlags().String("theme", "", "")
			root.PersistentFlags().Bool("verbose", false, "")
			root.PersistentFlags().Bool("yes", false, "")
			installInterceptor(app, root)
			probe := &cobra.Command{Use: "probe", RunE: func(*cobra.Command, []string) error { return nil }}
			probe.Flags().Bool("json", false, "")
			root.AddCommand(probe)

			root.SetArgs(tc.args)
			if err := root.ExecuteContext(context.Background()); err != nil {
				t.Fatalf("execute: %v", err)
			}
			if got := out.Len() > 0; got != tc.wantBanner {
				t.Errorf("banner printed = %v, want %v; stdout:\n%s", got, tc.wantBanner, out.String())
			}
		})
	}
}
