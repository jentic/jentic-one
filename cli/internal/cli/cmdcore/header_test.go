package cmdcore

import (
	"testing"

	"github.com/spf13/cobra"
)

func TestBannerSkip(t *testing.T) {
	noop := func(*cobra.Command, []string) {}
	root := &cobra.Command{Use: "jentic"} // no Run: not runnable

	status := &cobra.Command{Use: "status", Run: noop}
	root.AddCommand(status)
	if bannerSkip(status) {
		t.Errorf("status should show the banner")
	}

	for _, name := range []string{"help", "install", "update", "execute"} {
		c := &cobra.Command{Use: name, Run: noop}
		root.AddCommand(c)
		if !bannerSkip(c) {
			t.Errorf("%s should skip the banner", name)
		}
	}

	// Completion tree (parent + shell subcommand) must stay silent.
	completion := &cobra.Command{Use: "completion", Run: noop}
	zsh := &cobra.Command{Use: "zsh", Run: noop}
	completion.AddCommand(zsh)
	root.AddCommand(completion)
	if !bannerSkip(completion) || !bannerSkip(zsh) {
		t.Errorf("completion and its subcommands should skip the banner")
	}

	// A non-runnable command falls through to Help() — skip.
	if !bannerSkip(root) {
		t.Errorf("non-runnable root should skip the banner")
	}
}
