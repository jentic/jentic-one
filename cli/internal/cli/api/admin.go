package api

import (
	"github.com/spf13/cobra"
)

// newAdminCmd builds the `admin` command tree: operator-facing platform
// administration. Today it carries a single group, `config providers`, for
// runtime credential provider configuration (set/get/list); the tree is shaped
// so future admin config surfaces can hang off `admin config` additively.
func newAdminCmd(app *app) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "admin",
		Short: "Administer the jentic-one platform",
		Long: "admin groups operator-facing platform administration. Subcommands\n" +
			"require an identity with admin privileges (e.g. org:admin).\n\n" +
			"`admin config providers` manages runtime credential provider\n" +
			"configuration (e.g. Pipedream) without hand-editing backend YAML or\n" +
			"restarting the server.",
		Args: cobra.NoArgs,
	}
	cmd.AddCommand(newAdminConfigCmd(app))
	return cmd
}

// newAdminConfigCmd is the `admin config` parent: runtime, DB-backed platform
// configuration.
func newAdminConfigCmd(app *app) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "config",
		Short: "Manage runtime platform configuration",
		Args:  cobra.NoArgs,
	}
	cmd.AddCommand(newAdminProvidersCmd(app))
	return cmd
}
