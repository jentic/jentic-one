package api

import (
	"github.com/spf13/cobra"
)

// newAdminCmd builds the `admin` command tree: operator-facing platform
// administration: `config providers` for runtime credential provider
// configuration (set/get/list), and `cli-profiles provision` for importing a
// vendor CLI profile's APIs. Future admin config surfaces hang off `admin
// config` additively.
func newAdminCmd(app *app) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "admin",
		Short: "Administer the jentic-one platform",
		Long: "admin groups operator-facing platform administration. Subcommands\n" +
			"require an identity with admin privileges (e.g. org:admin).\n\n" +
			"`admin config providers` manages runtime credential provider\n" +
			"configuration (e.g. Pipedream) without hand-editing backend YAML or\n" +
			"restarting the server. `admin cli-profiles provision` imports the APIs\n" +
			"of a vendor CLI profile.",
		Args: cobra.NoArgs,
	}
	cmd.AddCommand(newAdminConfigCmd(app), newAdminCLIProfilesCmd(app))
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
