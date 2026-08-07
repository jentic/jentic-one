package api

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

// newThemeCmd persists the global human-mode color theme (plan Phase 3 item 4 —
// the registry lands in Phase 2; this is the persistence half). It writes the
// top-level `theme` in ~/.config/jentic/config.yaml, which the resolver reads as
// the config rung of the ladder (--theme > JENTIC_THEME > NO_COLOR > config >
// dark, impl/1.4 §3).
//
// Not fenced: a color preference neither mutates management state nor re-points
// which environment/identity is active (the fence rule, clitree.MustBeFenced).
// The Stage-0 mode gate still forces no-color for agents regardless of this.
func newThemeCmd(_ *app) *cobra.Command {
	return &cobra.Command{
		Use:   "theme <dark|light|no-color>",
		Short: "Set the persistent color theme",
		Long: "theme persists the global color theme in ~/.config/jentic/config.yaml.\n" +
			"Override per-invocation with --theme or $JENTIC_THEME. Agent and\n" +
			"service-account modes always use no-color regardless of this setting.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			name := args[0]
			aud := ux.FromContext(cmd.Context())
			if _, ok := theme.Themes[name]; !ok {
				return reportCoded(aud, &ux.CodedError{
					Code:       ux.CodeMissingArgument,
					Msg:        fmt.Sprintf("unknown theme %q", name),
					Actionable: "Choose one of: dark, light, no-color.",
				})
			}
			if err := config.MutateConfig(func(cfg *config.Config) error {
				cfg.Theme = name
				return nil
			}); err != nil {
				return reportCoded(aud, err)
			}
			aud.Render(ux.Result{Status: ux.StatusUpdated, Resource: "theme", Name: name})
			return nil
		},
	}
}
